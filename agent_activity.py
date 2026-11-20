"""Read-only agent states. Local event formats may change between CLI versions."""
import json
import heapq
from functools import lru_cache
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from providers import claude_max_dir

# Slash-command, `!` shell and injected context records never start a model turn.
CLAUDE_LOCAL = ('<command-name>', '<local-command-', '<bash-input>', '<bash-stdout>', '<bash-stderr>')


def timestamp(value):
    try:
        # Python 3.10 accepts only 3 or 6 fraction digits; Antigravity trims trailing zeros.
        return datetime.fromisoformat(re.sub(r'\.(\d+)', lambda m: '.' + m[1][:6].ljust(6, '0'), value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0


def tool_label(name):
    name = name.lower() if isinstance(name, str) else ''
    for words, label in ((('patch', 'edit', 'write_file'), '파일 수정 중'),
                         (('search', 'find', 'web', 'read'), '자료 확인 중'),
                         (('exec', 'shell', 'terminal', 'bash'), '명령 실행 중')):
        if any(w in name for w in words):
            return label
    return '도구 실행 중'


def short_model(model):
    """claude-opus-5-5[1m] -> opus 5.5; other vendors' ids stay as logged."""
    if not isinstance(model, str) or not model or model.startswith('<') or model in ('inherit', 'auto', 'default'):
        return None
    match = re.search(r'(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?!\d)', model)
    if match:
        return match[1] + ' ' + match[2] + ('.' + match[3] if match[3] else '')
    return model.split('[')[0]


def plan_items(items, text_key):
    return [dict(text=str(item.get(text_key, '')), status=str(item.get('status', 'pending')))
            for item in items if isinstance(item, dict)] if isinstance(items, list) else None


def update_plan(todos, data):
    """Claude Desktop's update_plan: `items` replaces the list; advance/done/start change steps (1-based)."""
    if isinstance(data.get('items'), list):
        return plan_items(data['items'], 'content')
    todos = [dict(item) for item in todos or ()]
    done = data.get('done')
    for index in done if isinstance(done, list) else (done,):
        if isinstance(index, int) and 0 < index <= len(todos):
            todos[index - 1]['status'] = 'completed'
    if data.get('advance'):
        for status, new in (('in_progress', 'completed'), ('pending', 'in_progress')):
            item = next((item for item in todos if item['status'] == status), None)
            if item:
                item['status'] = new
    start = data.get('start')
    if isinstance(start, int) and 0 < start <= len(todos):
        todos[start - 1]['status'] = 'in_progress'
    return todos


# Logs that failed to parse, reported once each.
SKIPPED = set()


NOTIFIED = re.compile(r'<(?:tool-use-id|task-id)>([^<]+)</')


def claude_detail(detail, event):
    """Title, model, task list and launched subagents of the main Claude session."""
    kind = event.get('type')
    message = event.get('message') if isinstance(event.get('message'), dict) else {}
    content = message.get('content')
    if kind in ('custom-title', 'agent-name'):
        detail['custom'] = event.get('customTitle') or event.get('agentName') or detail['custom']
    elif kind == 'ai-title':
        detail['ai'] = event.get('aiTitle') or detail['ai']
    elif kind == 'assistant':
        detail['model'] = short_model(message.get('model')) or detail['model']
        detail['effort'] = event.get('effort') or detail['effort']
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict) or block.get('type') != 'tool_use' or not isinstance(block.get('input'), dict):
                continue
            name, data = str(block.get('name', '')), block['input']
            if name in ('Agent', 'Task'):
                detail['agents'][str(block.get('id'))] = dict(
                    type=data.get('subagent_type') or 'general-purpose', description=data.get('description'),
                    model=short_model(data.get('model')), effort=data.get('effort'))
            elif name == 'TodoWrite':
                detail['todos'] = plan_items(data.get('todos'), 'content')
            elif name.endswith('update_plan'):
                detail['todos'] = update_plan(detail['todos'], data)
            elif name == 'TaskStop':
                detail['finished'].add(str(data.get('task_id')))
    # Background agents end with a task notification, queued or delivered.
    attachment = event.get('attachment') if isinstance(event.get('attachment'), dict) else {}
    text = event.get('content') if kind == 'queue-operation' else attachment.get('prompt') if kind == 'attachment' else content
    if isinstance(text, str) and '<task-notification>' in text:
        detail['finished'].update(NOTIFIED.findall(text))
    if kind == 'user' and isinstance(content, list):
        result = event.get('toolUseResult') if isinstance(event.get('toolUseResult'), dict) else {}
        for block in content:
            agent = detail['agents'].get(str(block.get('tool_use_id'))) if isinstance(block, dict) and block.get('type') == 'tool_result' else None
            if agent is None:
                continue
            if result.get('status') == 'async_launched':
                agent.update(agent_id=result.get('agentId'), model=agent['model'] or short_model(result.get('resolvedModel')))
            else:
                detail['finished'].add(str(block.get('tool_use_id')))


def claude_event(state, event, moment, pending):
    kind = event.get('type')
    message = event.get('message')
    if not isinstance(message, dict):
        message = {}
    content = message.get('content')
    blocks = content if isinstance(content, list) else []
    if kind == 'user':
        results = {str(block.get('tool_use_id')) for block in blocks if isinstance(block, dict) and block.get('type') == 'tool_result'}
        text = (content if isinstance(content, str) else ''.join(
            str(block.get('text', '')) for block in blocks if isinstance(block, dict) and block.get('type') == 'text')).lstrip()
        if text.startswith('[Request interrupted by user'):
            state.update(status='cancelled', label='작업 중단')
            pending.clear()
        elif event.get('isMeta') or event.get('isCompactSummary') or text.startswith(CLAUDE_LOCAL):
            pass
        elif not results:
            state.update(status='running', label='작업 중', started=moment)
            pending.clear()
        else:
            state.update(status='running', label='응답 준비 중')
            pending.difference_update(results)
    elif kind == 'assistant':
        tools = [block for block in blocks if isinstance(block, dict) and block.get('type') == 'tool_use']
        if state['status'] in ('done', 'cancelled'):
            state['started'] = moment  # turn started by a command or injected message
        if tools:
            state.update(status='running', label=tool_label(tools[-1].get('name', '')))
            pending.update(str(block.get('id')) for block in tools)
        elif message.get('stop_reason') == 'end_turn':
            state.update(status='done', label='작업 완료')
            pending.clear()
        elif message.get('stop_reason'):
            state.update(status='running', label='응답 준비 중')
    elif kind == 'system' and event.get('subtype') == 'turn_duration':
        state.update(status='done', label='작업 완료')
        pending.clear()
    if moment:
        state['updated'] = moment


def session_state(path, agent):
    stat = path.stat()
    version = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return _session_state(path, agent, version).copy()


@lru_cache(maxsize=64)
def _session_state(path, agent, version, span=262144):
    # Cache only the small parsed state, never the session text or credentials.
    with path.open('rb') as stream:
        head = stream.readline(1 << 20) if agent == 'codex' else b''
        stream.seek(max(0, path.stat().st_size - span))
        lines = stream.read().decode('utf-8', errors='replace').splitlines()
    state = dict(id=str(path), agent=agent, status='unknown', label='작업 중',
                 started=0, updated=path.stat().st_mtime, target=str(path),
                 title=None, model=None, effort=None, todos=None, current_task=None, subagents=[])
    try:
        meta = json.loads(head)['payload'] if head else {}
        source = meta.get('source')
        state['_thread'] = meta.get('id')
    except (ValueError, TypeError, KeyError, AttributeError):
        source = None
    if isinstance(source, dict) and 'subagent' in source:
        # Codex sub-agent and guardian rollouts belong to the parent session.
        state['subagent'] = True
        spawn = source['subagent'].get('thread_spawn') if isinstance(source['subagent'], dict) else None
        if not isinstance(spawn, dict):
            return state
        state.update(_parent=spawn.get('parent_thread_id'), _role=spawn.get('agent_role'), _nickname=spawn.get('agent_nickname'))
    waiting = set()
    async_requests = set()
    requested_tools = False
    detail = dict(custom=None, ai=None, model=None, effort=None, todos=None, agents={}, finished=set())
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get('type', '')
        if agent == 'claude':
            if not event.get('isSidechain'):
                claude_event(state, event, timestamp(event.get('timestamp')), waiting)
                claude_detail(detail, event)
            continue
        data = event.get('payload', {}) if agent == 'codex' else event.get('data', {})
        if not isinstance(data, dict):
            continue
        moment = timestamp(event.get('timestamp'))
        if kind == 'turn_context':
            mode = data.get('collaboration_mode')
            settings = (mode.get('settings') if isinstance(mode, dict) else None) or {}
            settings = settings if isinstance(settings, dict) else {}
            state['model'] = short_model(data.get('model') or settings.get('model')) or state['model']
            state['effort'] = data.get('effort') or settings.get('reasoning_effort') or state['effort']
        elif kind == 'assistant.message':
            state['model'] = short_model(data.get('model')) or state['model']
        elif kind == 'session.model_change':
            state['model'] = short_model(data.get('newModel')) or state['model']
            state['effort'] = data.get('reasoningEffort') or state['effort']
        if agent == 'codex' and kind == 'event_msg':
            kind = data.get('type', '')
        if kind in ('task_started', 'user.message', 'user_message'):
            state.update(status='running', label='작업 중', started=moment)
            waiting.clear()
        elif kind == 'assistant.turn_start':
            state.update(status='running', label='작업 중')
            if not state['started']:
                state['started'] = moment
        elif kind == 'assistant.message':
            requested_tools = bool(data.get('toolRequests'))
        elif kind == 'assistant.turn_end' and requested_tools:
            state.update(status='running', label='도구 실행 중')
        elif kind in ('task_complete', 'session.idle', 'assistant.turn_end'):
            state.update(status='done', label='작업 완료')
            waiting.clear()
        elif kind in ('turn_aborted', 'abort', 'session.shutdown'):
            state.update(status='cancelled', label='작업 중단')
            waiting.clear()
        elif kind in ('session.error', 'task_failed'):
            state.update(status='failed', label='작업 실패')
            waiting.clear()
        elif kind == 'permission.requested':
            waiting.add(data.get('requestId', 'permission'))
        elif kind == 'permission.completed':
            waiting.discard(data.get('requestId', 'permission'))
            state.update(status='running', label='응답 준비 중')
        elif kind == 'tool.execution_start':
            state.update(status='running', label=tool_label(data.get('toolName', '')))
        elif kind == 'tool.execution_complete':
            state.update(status='running', label='응답 준비 중')
        elif kind == 'response_item':
            item = data.get('type')
            if item in ('function_call', 'custom_tool_call'):
                name = data.get('name', '')
                state.update(status='running', label=tool_label(name))
                if name == 'update_plan':
                    try:
                        state['todos'] = plan_items(json.loads(data.get('arguments') or '{}').get('plan'), 'step')
                    except (ValueError, AttributeError):
                        pass
                if 'request_user_input' in name or 'request_permissions' in name:
                    waiting.add(data.get('call_id', 'input'))
                    if name.endswith('request_user_input_async'):
                        async_requests.add(data.get('call_id', 'input'))
            elif item in ('function_call_output', 'custom_tool_call_output'):
                if data.get('call_id', 'input') not in async_requests:
                    waiting.discard(data.get('call_id', 'input'))
                state.update(status='running', label='응답 준비 중')
            elif item == 'reasoning':
                state.update(status='running', label='응답 준비 중')
            elif item == 'message' and data.get('phase') == 'final_answer':
                state.update(status='done', label='작업 완료')
                waiting.clear()
        if waiting:
            state.update(status='waiting', label='승인·입력 대기')
        if moment:
            state['updated'] = moment
    if agent == 'claude':
        state['pending'] = bool(waiting)
        title = detail['custom']
        if not title:
            try:
                title = json.loads((path.parent / path.stem / 'custom-title.json').read_text()).get('customTitle')
            except (OSError, ValueError, AttributeError):
                pass
        finished = detail['finished']
        state.update(title=title or detail['ai'], model=detail['model'], effort=detail['effort'], todos=detail['todos'],
                     subagents=[dict(agent, id=key) for key, agent in detail['agents'].items()
                                if key not in finished and agent.get('agent_id') not in finished],
                     _known=frozenset(detail['agents']) | frozenset(finished))
    state['current_task'] = next((item['text'] for item in state['todos'] or () if item['status'] == 'in_progress'), None)
    if state['status'] == 'unknown' and span < 1 << 24 and path.stat().st_size > span:
        # A pasted image or compacted history can make the newest line larger than the tail.
        return _session_state.__wrapped__(path, agent, version, span * 8)
    return state


def session_activity(path, agent):
    state = session_state(path, agent)
    return state['label'] if state['status'] in ('running', 'waiting') else None


def live_agent_files(claude=None):
    found = set()
    try:
        processes = os.scandir('/proc')
    except OSError:
        return found
    with processes:
        for process in processes:
            if not process.name.isdigit():
                continue
            try:
                # Raw reads: this runs for every process each poll, and a text stream costs twice as much.
                handle = os.open(process.path + '/comm', os.O_RDONLY)
                try:
                    name = os.read(handle, 64).decode('utf-8', 'replace').strip()
                finally:
                    os.close(handle)
                # cursor-agent (not installed here) would be matched here and read from its own store.
                if name not in ('agy', 'copilot', 'codex', 'claude', 'opencode'):
                    continue
                if name == 'claude' and claude is not None:
                    claude.append(process.path)
                with os.scandir(process.path + '/fd') as descriptors:
                    for fd in descriptors:
                        try:
                            target = os.readlink(fd.path)
                            if (target.endswith('.jsonl') and '/sessions/' in target or target.endswith('/events.jsonl') or target.endswith('.jsonl') and '/.claude/projects/' in target
                                    or '/conversations/' in target and target.endswith('.db') or target.endswith('/opencode/opencode.db')):
                                found.add(target)
                        except OSError:
                            pass
            except OSError:
                pass
    return found


def claude_project(cwd):
    # Claude names a project folder after its working directory: /home/a_b/.x -> -home-a-b--x
    return re.sub(r'[^A-Za-z0-9]', '-', cwd)


def claude_roots(home):
    profile = claude_max_dir() / 'projects'
    try:
        profile = home / profile.relative_to(Path.home())
    except ValueError:
        pass
    return home / '.claude/projects', profile


def claude_sessions(processes, roots):
    """Claude keeps no transcript open; a running CLI keeps its project's newest sessions visible."""
    projects = {}
    try:
        with open('/proc/stat') as stream:
            boot = next(int(line.split()[1]) for line in stream if line.startswith('btime '))
        for process in processes:
            try:
                cwd = os.readlink(process + '/cwd')
                with open(process + '/stat') as stream:
                    started = boot + int(stream.read().rsplit(')', 1)[1].split()[19]) / os.sysconf('SC_CLK_TCK')
            except (OSError, ValueError, IndexError):
                continue
            count, first = projects.get(claude_project(cwd), (0, started))
            projects[claude_project(cwd)] = count + 1, min(first, started)
    except (OSError, ValueError, IndexError, StopIteration):
        return set()
    found = set()
    for project, (count, started) in projects.items():
        for root in roots:
            try:
                with os.scandir(root / project) as entries:
                    files = sorted(((entry.stat().st_mtime, entry.path) for entry in entries if entry.name.endswith('.jsonl')), reverse=True)
            except OSError:
                continue
            # Older logs belong to earlier runs, so a crashed turn is not revived.
            found.update(path for modified, path in files[:count] if modified >= started)
    return found


def subagent_activity(path):
    try:
        with os.scandir(path[:-len('.jsonl')] + '/subagents') as entries:
            return max((entry.stat().st_mtime for entry in entries), default=0)
    except OSError:
        return 0


@lru_cache(maxsize=256)
def _json_file(path, version):
    try:
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=64)
def _agent_definition(path, version):
    """model/effort from an agents/<type>.md frontmatter."""
    found = {}
    try:
        with open(path, encoding='utf-8') as stream:
            if stream.readline().strip() == '---':
                for line in stream:
                    if line.strip() == '---':
                        break
                    key, _, value = line.partition(':')
                    if key.strip() in ('model', 'effort'):
                        found[key.strip()] = value.strip().strip('"\'')
    except (OSError, UnicodeDecodeError):
        pass
    return found


_first_replies = {}


def first_reply(path):
    """Model and effort of a subagent transcript's first assistant record; kept once found."""
    if path in _first_replies:
        return _first_replies[path]
    try:
        with open(path, 'rb') as stream:
            for line in stream.readlines(1 << 21):
                if b'"assistant"' not in line:
                    continue
                try:
                    event = json.loads(line)
                    model = event['message']['model']
                except (ValueError, TypeError, KeyError):
                    continue
                if len(_first_replies) > 512:
                    _first_replies.clear()
                _first_replies[path] = short_model(model), event.get('effort')
                return _first_replies[path]
    except OSError:
        pass
    return None, None


def file_version(path):
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns


def claude_subagents(path, state, agents_dir, now):
    """Running subagents with model/effort from the call, then the subagent log, then agents/<type>.md."""
    known = state.pop('_known', frozenset())
    folder = path[:-len('.jsonl')] + '/subagents'
    metas = {}
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.endswith('.meta.json'):
                    meta = _json_file(entry.path, file_version(entry.path))
                    metas[meta.get('toolUseId')] = entry.name[:-len('.meta.json')], meta
    except OSError:
        pass
    running = [(agent, metas.get(agent['id'], (agent.get('agent_id') and 'agent-' + agent['agent_id'], {}))) for agent in state['subagents']]
    for key, (name, meta) in metas.items():
        # Background agents outlive the parsed tail. Unfinished metadata with a log written in the last
        # 15 min (longer than one 10 min tool call) counts as running; killed agents never set `finished`.
        if key not in known and name[len('agent-'):] not in known and not meta.get('finished'):
            try:
                if now - os.stat(f'{folder}/{name}.jsonl').st_mtime <= 900:
                    running.append((dict(type=meta.get('agentType'), description=meta.get('description'), model=None, effort=meta.get('effort')), (name, meta)))
            except OSError:
                pass
    result = []
    for agent, (name, meta) in running:
        if meta.get('finished'):
            continue
        model, effort = agent.get('model'), agent.get('effort')
        if name and not (model and effort):
            logged = first_reply(f'{folder}/{name}.jsonl')
            model, effort = model or logged[0], effort or logged[1]
        definition = Path(agents_dir) / f"{agent['type']}.md"
        if not (model and effort) and definition.is_file():
            found = _agent_definition(str(definition), file_version(definition))
            model = model or short_model(found.get('model')) or (state['model'] if found.get('model') == 'inherit' else None)
            effort = effort or found.get('effort')
        result.append(dict(type=agent['type'], description=agent.get('description') or meta.get('description'),
                           model=model, effort=effort, status='running'))
    return result


@lru_cache(maxsize=4)
def _codex_titles(path, version):
    titles = {}
    try:
        with open(path, encoding='utf-8', errors='replace') as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                    titles[entry['id']] = entry.get('thread_name')
                except (ValueError, TypeError, KeyError):
                    continue
    except OSError:
        pass
    return titles


def codex_titles(home):
    path = home / '.codex/session_index.jsonl'
    try:
        return _codex_titles(str(path), file_version(path))
    except OSError:
        return {}


def opencode_activity(database, now):
    """opencode keeps sessions, child (subagent) sessions and todos in one SQLite file; read it read-only."""
    result = []
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.1)
    try:
        def last(session):
            row = connection.execute('SELECT data, time_updated FROM message WHERE session_id = ? ORDER BY time_created DESC LIMIT 1', (session,)).fetchone()
            data = json.loads(row[0]) if row else {}
            if not row or not isinstance(data, dict):
                return 'unknown', 0, {}
            if data.get('role') == 'user' or data.get('role') == 'assistant' and not (data.get('time') or {}).get('completed'):
                status = 'running'
            elif data.get('error'):
                status = 'cancelled' if 'Abort' in str(data['error'].get('name')) else 'failed'
            else:
                status = 'running' if data.get('finish') == 'tool-calls' else 'done'
            return status, row[1] / 1000, data
        def model(raw, data):
            try:
                chosen = json.loads(raw or '{}')
            except ValueError:
                chosen = {}
            effort = data.get('variant') or chosen.get('variant')
            return short_model(data.get('modelID') or chosen.get('id')), effort if effort != 'default' else None
        sessions = connection.execute('SELECT id, title, model, time_updated FROM session WHERE parent_id IS NULL AND time_archived IS NULL'
                                      ' ORDER BY time_updated DESC LIMIT 16').fetchall()
        for session, title, chosen, updated in sessions:
            status, moment, data = last(session)
            updated = max(updated / 1000, moment)
            # shortcut: a turn cut off by a crash looks running for an hour; upgrade if opencode logs process ids.
            if not (now - updated <= 120 or status == 'running' and now - updated <= 3600):
                continue
            started = connection.execute("SELECT max(time_created) FROM message WHERE session_id = ? AND json_extract(data, '$.role') = 'user'", (session,)).fetchone()[0]
            todos = plan_items([dict(content=c, status=s) for c, s in connection.execute(
                'SELECT content, status FROM todo WHERE session_id = ? ORDER BY position', (session,))], 'content') or None
            subagents = []
            for child, child_title, agent, child_model in connection.execute(
                    'SELECT id, title, agent, model FROM session WHERE parent_id = ? ORDER BY time_updated DESC LIMIT 8', (session,)).fetchall():
                child_status, _, child_data = last(child)
                if child_status == 'running':
                    name, effort = model(child_model, child_data)
                    subagents.append(dict(type=agent or child_data.get('agent'), description=child_title, model=name, effort=effort, status='running'))
            name, effort = model(chosen, data)
            label = {'running': '작업 중', 'done': '작업 완료', 'failed': '작업 실패', 'cancelled': '작업 중단', 'unknown': '작업 중'}[status]
            result.append(dict(id=f'{database}#{session}', agent='opencode', status=status, label=label,
                               started=(started or 0) / 1000, updated=updated, target=str(database), title=title, model=name, effort=effort,
                               todos=todos, current_task=next((t['text'] for t in todos or () if t['status'] == 'in_progress'), None), subagents=subagents))
    finally:
        connection.close()
    return result


def recent_sessions(directory, pattern):
    for root, folders, names in os.walk(directory):
        # Claude subagent transcripts belong to the parent session, which represents the work.
        folders[:] = [name for name in folders if name != 'subagents']
        for name in names:
            if not (name.endswith('.jsonl') if pattern == '*.jsonl' else name == pattern):
                continue
            path = os.path.join(root, name)
            try:
                yield os.stat(path).st_mtime, path
            except OSError:
                continue


def antigravity_step(path):
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.1)
        try:
            row = connection.execute('SELECT status FROM steps ORDER BY idx DESC LIMIT 1').fetchone()
            return row[0] if row else None
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def read_activity(home=None, now=None, detailed=False, extra_agents=False):
    """extra_agents adds opencode records; callers need a display entry for that agent first."""
    home, now = Path(home or Path.home()), time.time() if now is None else now
    result = []
    codex_children = []
    claude = []
    live = live_agent_files(claude)
    roots = claude_roots(home)
    live = live | claude_sessions(claude, roots)
    for agent, folder, pattern in (('codex', '.codex/sessions', '*.jsonl'),
                                    ('copilot', '.copilot/session-state', 'events.jsonl'),
                                    ('claude', roots[0], '*.jsonl'), ('claude', roots[1], '*.jsonl')):
        try:
            live_paths = []
            def other_sessions():
                for modified, path in recent_sessions(home / folder, pattern):
                    if path in live:
                        live_paths.append((modified, path))
                    else:
                        yield modified, path
            paths = heapq.nlargest(32, other_sessions()) + live_paths
            for modified, path in paths:
                try:
                    if path in live or now - modified <= 120:
                        state = session_state(Path(path), agent)
                        if state.pop('subagent', False):
                            if state.get('_parent') and state['status'] in ('running', 'waiting'):
                                codex_children.append(state)
                            continue
                        if state.pop('pending', False) and state['status'] == 'running' and now - max(modified, subagent_activity(path)) > 30:
                            # Claude logs no permission prompt; a silent pending tool may be waiting for approval.
                            state.update(status='waiting', label='승인 대기 또는 실행 중')
                        if agent == 'claude':
                            state['subagents'] = claude_subagents(path, state, home / '.claude/agents', now)
                        result.append(state)
                except OSError:
                    continue
                except Exception as error:  # one malformed log must not hide every other agent
                    if path not in SKIPPED:
                        SKIPPED.add(path)
                        print(f'agent activity: skipped {path}: {type(error).__name__}: {error}', file=sys.stderr)
        except OSError:
            pass
    titles = codex_titles(home) if any(item['agent'] == 'codex' for item in result) else {}
    for item in result:
        thread = item.pop('_thread', None)
        if item['agent'] == 'codex':
            item['title'] = titles.get(thread)
            item['subagents'] = [dict(type=child['_role'], description=child['_nickname'], model=child['model'],
                                      effort=child['effort'], status=child['status'])
                                 for child in codex_children if child['_parent'] == thread]
    database = home / '.local/share/opencode/opencode.db'
    if extra_agents and str(database) in live:
        try:
            result.extend(opencode_activity(database, now))
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            pass
    # Gemini CLI (~/.gemini/tmp/*/chats) is not installed here; it would be read like the rollouts above.
    for folder in ('antigravity-cli', 'antigravity'):
        database = home / '.gemini' / folder / 'conversation_summaries.db'
        if not database.is_file():
            continue
        try:
            connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.1)
            try:
                query = 'SELECT title,last_modified_time,conversation_id,status,not_fully_idle,killed,last_user_input_time FROM conversation_summaries'
                rows = connection.execute(query + ' ORDER BY last_modified_time DESC LIMIT 16').fetchall()
                recent_ids = {row[2] for row in rows}
                for path in sorted(live):
                    target = Path(path)
                    if target.parent == database.parent / 'conversations' and target.stem not in recent_ids:
                        row = connection.execute(query + ' WHERE conversation_id = ?', (target.stem,)).fetchone()
                        if row:
                            rows.append(row)
                for title, modified, identifier, status, busy, killed, started in rows:
                    target = database.parent / 'conversations' / (identifier + '.db')
                    updated = timestamp(modified)
                    if str(target) not in live and not 0 <= now - updated <= 120:
                        continue
                    step = antigravity_step(target)
                    state = 'running' if busy and status != 'CASCADE_RUN_STATUS_IDLE' else 'done'
                    if killed or step in (6, 12):
                        state = 'cancelled'
                    elif step == 7 and not busy:
                        state = 'failed'
                    elif step == 9:
                        state = 'waiting'
                    label = {'running': '작업 중', 'done': '작업 완료', 'failed': '작업 실패',
                             'cancelled': '작업 중단', 'waiting': '입력·작업 대기'}[state]
                    result.append(dict(id=str(target), agent='antigravity', status=state,
                                       label=label + ' · ' + (title or '진행 중인 작업')[:48],
                                       started=timestamp(started), updated=updated, target=str(target), title=title))
            finally:
                connection.close()
        except (sqlite3.Error, ValueError, TypeError):
            continue
    if detailed:
        return result
    grouped = {}
    for item in result:
        if item['status'] in ('running', 'waiting'):
            grouped.setdefault(item['agent'], []).append(item['label'])
    return [(key, labels[0] + (f' · {len(labels)}개 작업' if len(labels) > 1 else '')) for key, labels in grouped.items()]


class ActivityTracker:
    """Missing/expired logs never mean success; initial old results stay quiet."""
    def __init__(self):
        self.previous = {}
        self.observed = {}
        self.ready = False
        self.last_scan = 0
        self.last_seen = {}

    def update(self, records, now=None):
        now = time.time() if now is None else now
        active, notices = [], []
        order = {key: index for index, key in enumerate(self.previous)}
        records = list(records)
        confirmed = {item['id'] for item in records if item['status'] != 'unknown'}
        records = [item for item in records if item['status'] != 'unknown']
        for key, old in self.previous.items():
            if key not in confirmed and old['status'] in ('running', 'waiting') and now - self.last_seen.get(key, 0) < 15:
                records.append(old)
        for record in records:
            item = dict(record)
            helpers = [s for s in item.get('subagents') or () if isinstance(s, dict) and s.get('status') == 'running']
            if item['status'] == 'done' and helpers:
                # The main turn ended but background subagents still work: the session is still busy.
                item['status'], item['label'] = 'running', f'서브에이전트 {len(helpers)}개 작업 중'
            key, status = item['id'], item['status']
            old = self.previous.get(key)
            if key in confirmed:
                self.last_seen[key] = now
            if status in ('running', 'waiting'):
                if old and old['status'] not in ('running', 'waiting'):
                    self.observed.pop(key, None)
                started = item['started'] or self.observed.setdefault(key, now)
                item['elapsed'] = max(0, int(now - started))
                item['estimated'] = not bool(item['started'])
                active.append(item)
            if self.ready and status in ('done', 'failed'):
                observed = old and old['status'] in ('running', 'waiting')
                # A later write to an old log is not a new task; only a turn that started since the last scan is.
                new_task = item['started'] > self.last_scan
                if observed or new_task:
                    notices.append(item)
            elif self.ready and status == 'waiting' and (not old or old['status'] != 'waiting'):
                notices.append(item)
            self.previous[key] = item
        self.ready = True
        self.last_scan = now
        present = {item['id'] for item in records}
        self.previous = {k: v for k, v in self.previous.items() if k in present}
        self.observed = {k: v for k, v in self.observed.items() if k in present}
        self.last_seen = {k: v for k, v in self.last_seen.items() if k in present}
        active.sort(key=lambda item: order.get(item['id'], len(order)))
        return active, notices
