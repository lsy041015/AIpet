"""Read-only agent states. Local event formats may change between CLI versions."""
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path


def timestamp(value):
    try:
        return datetime.fromisoformat(re.sub(r'(\.\d{6})\d+', r'\1', value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0


def tool_label(name):
    name = name.lower()
    for words, label in ((('patch', 'edit', 'write_file'), '파일 수정 중'),
                         (('search', 'find', 'web', 'read'), '자료 확인 중'),
                         (('exec', 'shell', 'terminal', 'bash'), '명령 실행 중')):
        if any(w in name for w in words):
            return label
    return '도구 실행 중'


def session_state(path, agent):
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 262144))
        lines = stream.read().decode('utf-8', errors='replace').splitlines()
    state = dict(id=str(path), agent=agent, status='unknown', label='작업 중',
                 started=0, updated=path.stat().st_mtime, target=str(path))
    waiting = set()
    async_requests = set()
    requested_tools = False
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get('type', '')
        data = event.get('payload', {}) if agent == 'codex' else event.get('data', {})
        if not isinstance(data, dict):
            continue
        moment = timestamp(event.get('timestamp'))
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
    return state


def session_activity(path, agent):
    state = session_state(path, agent)
    return state['label'] if state['status'] in ('running', 'waiting') else None


def live_agent_files():
    found = set()
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if (process / 'comm').read_text().strip() not in ('agy', 'copilot', 'codex'):
                continue
            for fd in (process / 'fd').iterdir():
                try:
                    target = os.readlink(fd)
                    if (target.endswith('.jsonl') and '/sessions/' in target or target.endswith('/events.jsonl')
                            or '/conversations/' in target and target.endswith('.db')):
                        found.add(target)
                except OSError:
                    pass
        except OSError:
            pass
    return found


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


def read_activity(home=None, now=None, detailed=False):
    home, now = Path(home or Path.home()), time.time() if now is None else now
    result = []
    live = live_agent_files()
    for agent, folder, pattern in (('codex', '.codex/sessions', '*.jsonl'),
                                    ('copilot', '.copilot/session-state', 'events.jsonl')):
        try:
            paths = sorted((home / folder).rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths[:32]:
                try:
                    if str(path) in live or now - path.stat().st_mtime <= 120:
                        result.append(session_state(path, agent))
                except OSError:
                    continue
        except OSError:
            pass
    for folder in ('antigravity-cli', 'antigravity'):
        database = home / '.gemini' / folder / 'conversation_summaries.db'
        if not database.is_file():
            continue
        try:
            connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.1)
            try:
                rows = connection.execute('SELECT title,last_modified_time,conversation_id,status,not_fully_idle,killed,last_user_input_time FROM conversation_summaries ORDER BY last_modified_time DESC LIMIT 16').fetchall()
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
                                       started=timestamp(started), updated=updated, target=str(target)))
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
                new_task = item['updated'] > self.last_scan and (old is None or item['started'] > old['started'])
                if observed or new_task:
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
