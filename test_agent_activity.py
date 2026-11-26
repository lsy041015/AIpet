from contextlib import closing
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timezone
from agent_activity import (read_activity, session_state, ActivityTracker, timestamp, live_agent_files, claude_project,
                            short_model, agent_kind, session_file, claude_session_id)
from agent_windows import agent_pids


class ActivityTests(unittest.TestCase):
    def test_live_sessions_survive_the_recent_history_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for agent, folder, filename, event in (
                ("codex", ".codex/sessions", "session.jsonl", {"type": "event_msg", "payload": {"type": "task_started"}}),
                ("copilot", ".copilot/session-state", "events.jsonl", {"type": "assistant.turn_start"}),
                ("claude", ".claude/projects", "session.jsonl", {"type": "user", "message": {"content": "start"}}),
            ):
                with self.subTest(agent=agent):
                    active = root / folder / "active" / filename
                    active.parent.mkdir(parents=True)
                    active.write_text(json.dumps(event) + "\n")
                    os.utime(active, (100, 100))
                    for index in range(40):
                        path = root / folder / str(index) / filename
                        path.parent.mkdir()
                        path.write_text("{}\n")
                        os.utime(path, (200 + index, 200 + index))
                    with patch("agent_activity.live_agent_files", return_value={str(active)}):
                        rows = [item for item in read_activity(root, now=250, detailed=True) if item["agent"] == agent]
                    self.assertEqual(len(rows), 33)
                    self.assertEqual(sum(item["id"] == str(active) for item in rows), 1)
                    self.assertEqual(next(item for item in rows if item["id"] == str(active))["status"], "running")
                    os.utime(active, (249, 249))
                    with patch("agent_activity.live_agent_files", return_value={str(active)}):
                        rows = [item for item in read_activity(root, now=250, detailed=True) if item["agent"] == agent]
                    self.assertEqual(sum(item["id"] == str(active) for item in rows), 1)
                    all_paths = {str(path) for path in (root / folder).rglob(filename)}
                    with patch("agent_activity.live_agent_files", return_value=all_paths):
                        rows = [item for item in read_activity(root, now=250, detailed=True) if item["agent"] == agent]
                    self.assertEqual({item["id"] for item in rows}, all_paths)

    @unittest.skipIf(os.name == "nt", "reads /proc")
    def test_unavailable_process_list_does_not_interrupt_activity_scan(self):
        for error in (FileNotFoundError, PermissionError):
            with self.subTest(error=error), patch("agent_activity.os.scandir", side_effect=error):
                self.assertEqual(live_agent_files(), set())

    @unittest.skipIf(os.name == "nt", "fakes /proc with symlinks")
    def test_live_files_come_from_agent_processes_only(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory)
            for pid, name in ((10, 'codex'), (11, 'bash'), (12, 'claude')):
                (fake / str(pid) / 'fd').mkdir(parents=True)
                (fake / str(pid) / 'comm').write_text(name + '\n')
                (fake / str(pid) / 'fd' / '3').symlink_to(f'/h/.codex/sessions/{pid}.jsonl')
            (fake / 'self').mkdir()
            real = os.scandir
            with patch('agent_activity.os.scandir', side_effect=lambda path: real(fake if path == '/proc' else path)):
                claude = []
                # Claude keeps no transcript open, so its descriptors are not read.
                self.assertEqual(live_agent_files(claude), {'/h/.codex/sessions/10.jsonl'})
                self.assertEqual(claude, [str(fake / '12')])

    def test_unchanged_logs_are_cached_but_append_and_replace_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            start = json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n"
            finish = json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n"
            path.write_text(start)
            with patch("agent_activity.json.loads", wraps=json.loads) as decode:
                first = session_state(path, "codex")
                self.assertEqual(first["status"], "running")
                parsed = decode.call_count
                first["status"] = "changed by caller"
                self.assertEqual(session_state(path, "codex")["status"], "running")
                self.assertEqual(decode.call_count, parsed)
                with path.open("a") as stream:
                    stream.write(finish)
                self.assertEqual(session_state(path, "codex")["status"], "done")
                self.assertGreater(decode.call_count, parsed)
                metadata = path.stat()
                replacement = Path(directory) / "replacement"
                replacement.write_text(start.ljust(metadata.st_size))
                os.utime(replacement, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
                replacement.replace(path)
                self.assertEqual(session_state(path, "codex")["status"], "running")

    def test_claude_tool_turn_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.claude/projects/-tmp/demo.jsonl'
            path.parent.mkdir(parents=True)
            events = [
                {'type': 'user', 'timestamp': '2026-09-28T00:00:00Z',
                 'message': {'role': 'user', 'content': 'start'}},
                {'type': 'assistant', 'timestamp': '2026-09-28T00:00:01Z',
                 'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'name': 'Bash', 'id': 'x'}],
                             'stop_reason': 'tool_use'}},
            ]
            path.write_text('\n'.join(json.dumps(event) for event in events) + '\n')
            state = session_state(path, 'claude')
            self.assertEqual((state['status'], state['label']), ('running', '명령 실행 중'))
            self.assertEqual(state['started'], timestamp('2026-09-28T00:00:00Z'))
            self.assertEqual(read_activity(directory), [('claude', '명령 실행 중')])
            events.extend([
                {'type': 'user', 'timestamp': '2026-09-28T00:00:02Z',
                 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'x'}]}},
                {'type': 'assistant', 'timestamp': '2026-09-28T00:00:03Z',
                 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'done'}],
                             'stop_reason': 'end_turn'}},
            ])
            path.write_text('\n'.join(json.dumps(event) for event in events) + '\n')
            self.assertEqual(session_state(path, 'claude')['status'], 'done')
            self.assertEqual(read_activity(directory), [])

    def test_missing_grace_stable_order_and_explicit_end(self):
        tracker = ActivityTracker()
        a = dict(id='a', agent='codex', status='running', label='작업 중', started=0, updated=100, target='a')
        b = dict(a, id='b')
        tracker.update([a, b], 100)
        self.assertEqual([x['id'] for x in tracker.update([b, a], 105)[0]], ['a', 'b'])
        active, notices = tracker.update([], 115)
        self.assertEqual(len(active), 2)
        self.assertEqual(active[0]['elapsed'], 15)
        self.assertEqual(notices, [])
        active, notices = tracker.update([dict(a, status='done', updated=116)], 116)
        self.assertEqual([x['id'] for x in active], ['b'])
        self.assertEqual(len(notices), 1)
        self.assertEqual(tracker.update([], 120), ([], []))

    def test_done_session_with_running_subagents_stays_active(self):
        tracker = ActivityTracker()
        sub = {'type': 'reviewer', 'description': 'x', 'model': 'opus 5.5', 'effort': 'xhigh', 'status': 'running'}
        base = dict(id='s', agent='claude', status='done', label='완료', started=0, updated=100, target='s',
                    model='opus 5.5', effort='medium')
        tracker.update([dict(base, status='running')], 100)
        active, notices = tracker.update([dict(base, subagents=[sub, dict(sub)])], 105)
        self.assertEqual([x['status'] for x in active], ['running'])
        self.assertEqual(active[0]['label'], '서브에이전트 2개 작업 중')
        self.assertEqual(notices, [])
        active, notices = tracker.update([dict(base, subagents=[])], 110)
        self.assertEqual((active, len(notices)), ([], 1))

    def test_tracker_elapsed_terminal_and_missing_records(self):
        tracker = ActivityTracker()
        task = dict(id='a', agent='codex', status='done', label='완료', started=10, updated=20, target='x')
        self.assertEqual(tracker.update([task], 30)[1], [])
        task = dict(task, status='running', started=30, updated=31)
        active, _ = tracker.update([task], 90)
        self.assertEqual(active[0]['elapsed'], 60)
        self.assertFalse(active[0]['estimated'])
        self.assertEqual(tracker.update([], 100)[1], [])
        tracker.update([task], 101)
        failed = dict(task, status='failed', updated=102)
        self.assertEqual(len(tracker.update([failed], 103)[1]), 1)
        self.assertEqual(tracker.update([failed], 104)[1], [])
        task = dict(task, id='b', started=0)
        tracker.update([task], 200)
        active, _ = tracker.update([dict(task, status='waiting')], 260)
        self.assertEqual(active[0]['elapsed'], 60)
        self.assertTrue(active[0]['estimated'])

    def test_permission_request_reply_and_tool_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            events = [dict(type='assistant.turn_start', data={}),
                      dict(type='permission.requested', data={'requestId': 'p'})]
            def state():
                path.write_text('\n'.join(json.dumps(e) for e in events))
                return session_state(path, 'copilot')['status']
            self.assertEqual(state(), 'waiting')
            events.append(dict(type='permission.completed', data={'requestId': 'p'}))
            self.assertEqual(state(), 'running')
            events.extend([dict(type='assistant.message', data={'toolRequests': [{'name': 'edit'}]}), dict(type='assistant.turn_end', data={})])
            self.assertEqual(state(), 'running')
            events.extend([dict(type='assistant.message', data={'toolRequests': []}), dict(type='assistant.turn_end', data={})])
            self.assertEqual(state(), 'done')

    def test_short_task_finishes_between_polls(self):
        tracker = ActivityTracker()
        tracker.update([], 100)
        done = dict(id='new', agent='codex', status='done', label='완료', started=101, updated=102, target='x')
        self.assertEqual(len(tracker.update([done], 105)[1]), 1)
        self.assertEqual(tracker.update([done], 110)[1], [])
        tracker.update([], 115)
        self.assertEqual(tracker.update([done], 120)[1], [])

    def test_waiting_state_notifies_once_per_episode(self):
        tracker = ActivityTracker()
        record = lambda status: dict(id='s', agent='codex', status=status, label='x', started=50, updated=60, target='x')
        self.assertEqual(tracker.update([record('waiting')], 100)[1], [])
        self.assertEqual(tracker.update([record('running')], 105)[1], [])
        notices = tracker.update([record('waiting')], 110)[1]
        self.assertEqual([n['status'] for n in notices], ['waiting'])
        self.assertEqual(tracker.update([record('waiting')], 115)[1], [])
        tracker.update([record('running')], 120)
        self.assertEqual(len(tracker.update([record('waiting')], 125)[1]), 1)
        # The 15 second grace re-append of the old waiting record is not a new episode.
        self.assertEqual(tracker.update([], 130)[1], [])
        self.assertEqual(tracker.update([record('waiting')], 135)[1], [])

    def test_done_notices_unchanged_with_waiting(self):
        tracker = ActivityTracker()
        record = lambda status: dict(id='s', agent='codex', status=status, label='x', started=50, updated=60, target='x')
        tracker.update([record('running')], 100)
        self.assertEqual([n['status'] for n in tracker.update([record('waiting')], 105)[1]], ['waiting'])
        self.assertEqual([n['status'] for n in tracker.update([record('done')], 110)[1]], ['done'])
        tracker.update([record('running')], 115)
        self.assertEqual([n['status'] for n in tracker.update([record('failed')], 120)[1]], ['failed'])

    def test_events_completion_and_stale_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for agent, folder, start, end in (
                ('codex', '.codex/sessions/x.jsonl',
                 {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'apply_patch'}},
                 {'type': 'event_msg', 'payload': {'type': 'task_complete'}}),
                ('copilot', '.copilot/session-state/x/events.jsonl',
                 {'type': 'tool.execution_start', 'data': {'toolName': 'edit'}},
                 {'type': 'assistant.turn_end', 'data': {}})):
                path = root / folder
                path.parent.mkdir(parents=True)
                path.write_text('partial\n[]\n' + json.dumps(start) + '\n')
                self.assertEqual(read_activity(root), [(agent, '파일 수정 중')])
                os.utime(path, (time.time()-180, time.time()-180))
                self.assertEqual(read_activity(root), [])
                path.write_text(json.dumps(start) + '\n' + json.dumps(end))
                self.assertEqual(read_activity(root), [])

    def test_live_antigravity_conversation_survives_history_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / ".gemini/antigravity-cli/conversation_summaries.db"
            database.parent.mkdir(parents=True)
            active = database.parent / "conversations/active.db"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE conversation_summaries(title,last_modified_time,not_fully_idle,killed,status,conversation_id,last_user_input_time)")
                for index in range(21):
                    moment = datetime.fromtimestamp(100 + index, timezone.utc).isoformat()
                    connection.execute("INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?,?)",
                        ("test", moment, 1, 0, "CASCADE_RUN_STATUS_RUNNING", "active" if index == 0 else str(index), moment))
            with patch("agent_activity.live_agent_files", return_value={str(active)}):
                rows = read_activity(root, now=230, detailed=True)
            self.assertEqual(sum(item["id"] == str(active) for item in rows), 1)
            self.assertLessEqual(len(rows), 17)

    def test_antigravity_read_only_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / '.gemini/antigravity-cli/conversation_summaries.db'
            path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('CREATE TABLE conversation_summaries(title,last_modified_time,not_fully_idle,killed,status,conversation_id,last_user_input_time)')
                timestamp = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', '391+00:00')
                connection.execute('INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?,?)', ('테스트 작업', timestamp, 1, 0, 'CASCADE_RUN_STATUS_RUNNING', 'test', timestamp))
            self.assertEqual(read_activity(root), [('antigravity', '작업 중 · 테스트 작업')])
            target = path.parent / 'conversations/test.db'
            target.parent.mkdir()
            with closing(sqlite3.connect(target)) as connection, connection:
                connection.execute('CREATE TABLE steps(idx,status)')
                connection.execute('INSERT INTO steps VALUES(1,9)')
            self.assertEqual(read_activity(root, detailed=True)[0]['status'], 'waiting')
            with closing(sqlite3.connect(target)) as connection, connection:
                connection.execute('UPDATE steps SET status=7')
            self.assertEqual(read_activity(root, detailed=True)[0]['status'], 'running')
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('UPDATE conversation_summaries SET not_fully_idle=0')
            self.assertEqual(read_activity(root, detailed=True)[0]['status'], 'failed')
            with closing(sqlite3.connect(target)) as connection, connection:
                connection.execute('UPDATE steps SET status=3')
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("UPDATE conversation_summaries SET status='CASCADE_RUN_STATUS_IDLE'")
            self.assertEqual(read_activity(root), [])

    def write_claude(self, path, events, moment=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        if moment is not None:
            os.utime(path, (moment, moment))

    def test_claude_commands_and_interrupts_do_not_start_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.claude/projects/-tmp/demo.jsonl'
            user = lambda content, **extra: dict(type='user', timestamp='2026-09-28T00:00:10Z', message={'role': 'user', 'content': content}, **extra)
            events = [user('start'), {'type': 'assistant', 'timestamp': '2026-09-28T00:00:01Z',
                      'message': {'content': [{'type': 'text', 'text': 'ok'}], 'stop_reason': 'end_turn'}},
                      user('<local-command-caveat>x</local-command-caveat>', isMeta=True),
                      user('<command-name>/model</command-name>\n<command-message>model</command-message>'),
                      user('<local-command-stdout>Set model</local-command-stdout>'),
                      user('<bash-input>ls</bash-input>'), user('summary', isCompactSummary=True)]
            self.write_claude(path, events)
            self.assertEqual(session_state(path, 'claude')['status'], 'done')
            self.write_claude(path, events[3:5])
            self.assertEqual(session_state(path, 'claude')['status'], 'unknown')
            self.assertEqual(read_activity(directory), [])
            self.write_claude(path, [user('start'), user([{'type': 'text', 'text': '[Request interrupted by user]'}])])
            self.assertEqual(session_state(path, 'claude')['status'], 'cancelled')
            command = user('<command-message>review</command-message>\n<command-name>/review</command-name>')
            self.write_claude(path, events[:2] + [command])
            self.assertEqual(session_state(path, 'claude')['status'], 'running')
            peer = {'type': 'assistant', 'timestamp': '2026-09-28T00:00:20Z',
                    'message': {'content': [{'type': 'tool_use', 'name': 'Read', 'id': 'r'}], 'stop_reason': 'tool_use'}}
            self.write_claude(path, events[:3] + [peer])
            self.assertEqual(session_state(path, 'claude')['started'], timestamp('2026-09-28T00:00:20Z'))

    def test_subagent_logs_belong_to_parent_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = {'type': 'user', 'message': {'content': 'start'}}
            main = root / '.claude/projects/-tmp/s.jsonl'
            self.write_claude(main, [start])
            self.write_claude(root / '.claude/projects/-tmp/s/subagents/agent-a.jsonl', [start])
            rollout = root / '.codex/sessions/rollout.jsonl'
            rollout.parent.mkdir(parents=True)
            meta = {'type': 'session_meta', 'payload': {'source': {'subagent': {'other': 'guardian'}}, 'padding': 'x' * 300000}}
            rollout.write_text(json.dumps(meta) + '\n' + json.dumps({'type': 'event_msg', 'payload': {'type': 'task_started'}}) + '\n')
            self.assertEqual([item['id'] for item in read_activity(root, detailed=True)], [str(main)])
        tracker = ActivityTracker()
        tracker.update([], 100)
        old = dict(id='old', agent='claude', status='done', label='완료', started=50, updated=101, target='x')
        self.assertEqual(tracker.update([old], 105)[1], [])

    @unittest.skipIf(os.name == "nt", "fakes /proc with symlinks")
    def test_running_claude_cli_keeps_silent_pending_tool_visible(self):
        self.assertEqual(claude_project('/home/a_b/.config/x y'), '-home-a-b--config-x-y')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / 'proc/42'
            process.mkdir(parents=True)
            (process / 'cwd').symlink_to('/work/my_proj')
            (process / 'stat').write_text('42 (claude) ' + ' '.join(['S', '1'] + ['0'] * 18))
            def live(claude=None):
                claude.append(str(process))
                return set()
            path = root / '.claude/projects/-work-my-proj/s.jsonl'
            now = time.time()
            self.write_claude(path, [{'type': 'user', 'message': {'content': 'start'}},
                                     {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'id': 'b'}]}}], now - 200)
            self.assertEqual(read_activity(root, now=now), [])
            with patch('agent_activity.live_agent_files', side_effect=live):
                self.assertEqual(read_activity(root, now=now), [('claude', '승인 대기 또는 실행 중')])
                self.assertEqual(read_activity(root, detailed=True, now=now)[0]['status'], 'waiting')
                os.utime(path, (now - 10, now - 10))
                self.assertEqual(read_activity(root, now=now), [('claude', '명령 실행 중')])
                os.utime(path, (now - 200, now - 200))
                subagent = root / '.claude/projects/-work-my-proj/s/subagents/agent-a.jsonl'
                self.write_claude(subagent, [], now - 5)
                self.assertEqual(read_activity(root, now=now), [('claude', '명령 실행 중')])
                os.utime(path, (1000, 1000))
                self.assertEqual(read_activity(root, now=now), [])

    @unittest.skipIf(os.name == "nt", "fakes /proc with symlinks")
    def test_subagents_need_their_session_cli(self):
        sid = 'c1e35db4-8b08-483f-a2bb-9b08b5f3f75e'
        self.assertEqual([claude_session_id(args) for args in (
            ['claude', f'--resume={sid}'], ['claude', '--resume', sid, '--model', 'x'], ['claude', '--session-id', sid],
            ['claude', '--resume'], ['claude', '--resume', '--model'], ['claude'])], [sid, sid, sid, None, None, None])
        now = time.time()
        iso = lambda moment: datetime.fromtimestamp(moment, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        agent = lambda tool_id, moment: {'type': 'assistant', 'timestamp': iso(moment), 'message': {'content': [
            {'type': 'tool_use', 'id': tool_id, 'name': 'Agent', 'input': {'subagent_type': 'scout', 'description': tool_id, 'model': 'haiku', 'effort': 'low'}}]}}
        with open('/proc/stat') as stream:
            boot = next(int(line.split()[1]) for line in stream if line.startswith('btime '))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / 'proc/42'
            process.mkdir(parents=True)
            (process / 'cwd').symlink_to('/work/p')
            # The CLI started 100 s ago; field 22 of stat is its start in clock ticks after boot.
            ticks = int((now - 100 - boot) * os.sysconf('SC_CLK_TCK'))
            (process / 'stat').write_text('42 (claude) ' + ' '.join(['S', '1'] + ['0'] * 17 + [str(ticks)]))
            folder = root / '.claude/projects/-work-p'
            self.write_claude(folder / f'{sid}.jsonl', [{'type': 'user', 'timestamp': iso(now - 300), 'message': {'content': 'start'}},
                                                         agent('before-restart', now - 250), agent('after-restart', now - 50)])
            for name, moment in (('old', now - 150), ('new', now - 10)):
                self.write_claude(folder / sid / f'subagents/agent-{name}.jsonl', [{'type': 'user', 'timestamp': iso(moment), 'message': {'content': 'go'}}])
                (folder / sid / f'subagents/agent-{name}.meta.json').write_text(json.dumps({'agentType': 'scout', 'description': 'meta-' + name, 'toolUseId': name}))
            def rows(cmdline, processes=(str(process),)):
                (process / 'cmdline').write_bytes(cmdline)
                def live(claude=None):
                    claude.extend(processes)
                    return set()
                with patch('agent_activity.live_agent_files', side_effect=live):
                    return [(row['id'], sorted(s['description'] for s in row['subagents'])) for row in read_activity(root, detailed=True, now=now)]
            log = str(folder / f'{sid}.jsonl')
            # Named session: only subagents started (or still writing) since this CLI started.
            self.assertEqual(rows(f'claude\0--resume={sid}\0'.encode()), [(log, ['after-restart', 'meta-new'])])
            self.assertEqual(rows(f'claude\0--session-id\0{sid}\0'.encode()), [(log, ['after-restart', 'meta-new'])])
            # No CLI runs the session: its subagents died with it.
            self.assertEqual(rows(b'claude\0', processes=()), [(log, [])])
            # A CLI that names no session is matched by folder only, so the age limits alone decide.
            self.assertEqual(rows(b'claude\0'), [(log, ['after-restart', 'before-restart', 'meta-new', 'meta-old'])])

    def test_windows_process_and_path_rules(self):
        self.assertEqual([agent_kind(name, args) for name, args in (
            ('claude.exe', []), ('Codex.EXE', None), ('antigravity.exe', []), ('bash.exe', []),
            ('node.exe', ['node', r'C:\Users\u\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\cli.js']),
            ('node.exe', ['node', r'C:\npm\node_modules\@github\copilot\index.js']), ('node.exe', ['node', 'server.js']))],
            ['claude', 'codex', 'agy', None, 'claude', 'copilot', None])
        self.assertTrue(session_file(r'C:\Users\u\.codex\sessions\2026\11\r.jsonl'))
        self.assertTrue(session_file(r'C:\Users\u\.gemini\antigravity\conversations\a.db'))
        self.assertFalse(session_file(r'C:\Users\u\notes.jsonl'))
        self.assertEqual(claude_project(r'C:\Users\u\my_app'), 'C--Users-u-my-app')

    @unittest.skipIf(os.name == "nt", "fakes /proc with symlinks")
    def test_window_fallback_matches_claude_cwd_and_skips_own_children(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory)
            for pid, parent, cwd in ((100, 1, '/work/w'), (101, 100, '/work/my_proj'), (102, 50, '/work/my_proj'), (103, 50, '/work/other')):
                process = fake / str(pid)
                (process / 'fd').mkdir(parents=True)
                (process / 'cwd').symlink_to(cwd)
                (process / 'stat').write_text(f'{pid} (claude) S {parent} 0')
                (process / 'comm').write_text('claude\n')
                (process / 'cmdline').write_bytes(b'claude\0')
            real = Path
            with patch('agent_windows.Path', side_effect=lambda value: real(fake) if value == '/proc' else real(value)), \
                    patch('agent_windows.os.getpid', return_value=100):
                self.assertEqual(agent_pids('claude', '/home/u/.claude/projects/-work-my-proj/s.jsonl'), {102, 50})

    def claude_rows(self, root, events, **extra):
        path = root / '.claude/projects/-tmp/s.jsonl'
        self.write_claude(path, events)
        for name, content in extra.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        # The CLI is unknown here, as on Windows; test_subagents_need_their_session_cli covers known ones.
        with patch('agent_activity.claude_sessions', return_value=(set(), {}, None)):
            return [row for row in read_activity(root, detailed=True) if row['agent'] == 'claude']

    def test_claude_title_model_and_todos(self):
        self.assertEqual([short_model(m) for m in ('claude-opus-5-5', 'claude-haiku-5-5[1m]', 'claude-sonnet-4-20250514', 'opus', 'inherit', 'gpt-6.2-sol')],
                         ['opus 5.5', 'haiku 5.5', 'sonnet 4', 'opus', None, 'gpt-6.2-sol'])
        start = {'type': 'user', 'message': {'content': 'start'}}
        ai = {'type': 'ai-title', 'aiTitle': 'AI name'}
        custom = {'type': 'custom-title', 'customTitle': 'My name'}
        todo = {'type': 'assistant', 'effort': 'xhigh', 'message': {'model': 'claude-opus-5-5', 'content': [{'type': 'tool_use', 'id': 't', 'name': 'TodoWrite', 'input': {'todos': [
            {'content': 'Read code', 'status': 'completed', 'activeForm': 'Reading'},
            {'content': 'Write tests', 'status': 'in_progress', 'activeForm': 'Writing'}]}}]}}
        sidechain = {'type': 'assistant', 'isSidechain': True, 'effort': 'low', 'message': {'model': 'claude-haiku-5-5', 'content': []}}
        with tempfile.TemporaryDirectory() as directory:
            row, = self.claude_rows(Path(directory), [start, custom, ai, todo, sidechain])
            self.assertEqual((row['title'], row['model'], row['effort']), ('My name', 'opus 5.5', 'xhigh'))
            self.assertEqual(row['todos'], [{'text': 'Read code', 'status': 'completed'}, {'text': 'Write tests', 'status': 'in_progress'}])
            self.assertEqual((row['current_task'], row['subagents']), ('Write tests', []))
            self.assertEqual(self.claude_rows(Path(directory), [start, ai])[0]['title'], 'AI name')
            self.assertEqual(self.claude_rows(Path(directory), [start, ai], **{'.claude/projects/-tmp/s/custom-title.json': '{"customTitle": "Saved"}'})[0]['title'], 'Saved')
            plan = lambda **data: {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'p', 'name': 'mcp__ccd_turn__update_plan', 'input': data}]}}
            row, = self.claude_rows(Path(directory), [start, plan(items=[{'content': 'A', 'status': 'in_progress'}, {'content': 'B', 'status': 'pending'}]), plan(advance=True)])
            self.assertEqual(([t['status'] for t in row['todos']], row['current_task']), (['completed', 'in_progress'], 'B'))
            self.assertIsNone(self.claude_rows(Path(directory), [start])[0]['todos'])

    def test_claude_running_subagents(self):
        agent = lambda tool_id, **data: {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': tool_id, 'name': 'Agent', 'input': data}]}}
        result = lambda tool_id, **extra: dict({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': tool_id}]}}, **extra)
        events = [{'type': 'user', 'message': {'content': 'start'}},
                  {'type': 'assistant', 'message': {'model': 'claude-opus-5-5', 'content': []}},
                  agent('given', subagent_type='scout', description='Find files', model='haiku', effort='low'),
                  agent('logged', subagent_type='general-purpose', description='From log'),
                  agent('defined', subagent_type='reviewer', description='From md'),
                  agent('sync-done', subagent_type='scout', description='Finished'), result('sync-done'),
                  agent('async', subagent_type='scout', description='Background'),
                  result('async', toolUseResult={'status': 'async_launched', 'agentId': 'b1', 'resolvedModel': 'claude-haiku-5-5'}),
                  {'type': 'queue-operation', 'operation': 'enqueue', 'content': '<task-notification>\n<task-id>b1</task-id>\n</task-notification>'}]
        files = {'.claude/projects/-tmp/s/subagents/agent-l1.meta.json': json.dumps({'agentType': 'general-purpose', 'toolUseId': 'logged'}),
                 '.claude/projects/-tmp/s/subagents/agent-l1.jsonl': json.dumps({'type': 'user', 'message': {'content': 'go'}}) + '\n' + json.dumps(
                     {'type': 'assistant', 'effort': 'medium', 'message': {'model': 'claude-sonnet-5-5', 'content': []}}) + '\n',
                 '.claude/agents/reviewer.md': '---\nname: reviewer\nmodel: opus\neffort: high\n---\nReview.\n'}
        with tempfile.TemporaryDirectory() as directory:
            row, = self.claude_rows(Path(directory), events, **files)
            self.assertEqual(row['subagents'], [
                dict(type='scout', description='Find files', model='haiku', effort='low', status='running'),
                dict(type='general-purpose', description='From log', model='sonnet 5.5', effort='medium', status='running'),
                dict(type='reviewer', description='From md', model='opus', effort='high', status='running')])
            # A background agent launched before the parsed tail is found by its unfinished metadata and fresh log.
            now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            files = {'.claude/projects/-tmp/s/subagents/agent-old.meta.json': json.dumps({'agentType': 'scout', 'description': 'Old', 'toolUseId': 'gone', 'effort': 'low'}),
                     '.claude/projects/-tmp/s/subagents/agent-old.jsonl': json.dumps({'type': 'assistant', 'timestamp': now_iso, 'message': {'model': 'claude-haiku-5-5'}}) + '\n',
                     '.claude/projects/-tmp/s/subagents/agent-l1.meta.json': json.dumps({'toolUseId': 'logged', 'finished': True})}
            row, = self.claude_rows(Path(directory), events[:3] + events[4:], **files)
            self.assertEqual([(s['description'], s['model'], s['effort']) for s in row['subagents']],
                             [('Find files', 'haiku', 'low'), ('From md', 'opus', 'high'), ('Old', 'haiku 5.5', 'low')])
            stale = Path(directory) / '.claude/projects/-tmp/s/subagents/agent-old.jsonl'
            os.utime(stale, (time.time() - 1000,) * 2)
            self.assertEqual(len(self.claude_rows(Path(directory), events[:3] + events[4:])[0]['subagents']), 2)
            # A finished agent's log touched just now without a new record (the desktop app does this) stays finished.
            stale.write_text(json.dumps({'type': 'assistant', 'timestamp': '2026-09-28T07:51:00.687Z', 'message': {}}) + '\n')
            self.assertEqual(len(self.claude_rows(Path(directory), events[:3] + events[4:])[0]['subagents']), 2)

    def test_codex_detail_and_linked_subagents(self):
        line = lambda kind, **payload: json.dumps({'type': kind, 'payload': payload})
        plan = json.dumps({'plan': [{'step': 'Inspect', 'status': 'completed'}, {'step': 'Patch', 'status': 'in_progress'}]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / '.codex/sessions/2026/11/20'
            sessions.mkdir(parents=True)
            (sessions / 'rollout-parent.jsonl').write_text('\n'.join([
                line('session_meta', id='p1', source='cli'), line('event_msg', type='task_started'),
                line('turn_context', model='gpt-6.2-sol', effort='medium'),
                line('response_item', type='function_call', name='update_plan', arguments=plan, call_id='c1')]) + '\n')
            spawn = {'subagent': {'thread_spawn': {'parent_thread_id': 'p1', 'agent_role': 'scout', 'agent_nickname': 'Darwin'}}}
            (sessions / 'rollout-child.jsonl').write_text('\n'.join([
                line('session_meta', id='c1', source=spawn), line('event_msg', type='task_started'),
                line('turn_context', collaboration_mode={'settings': {'model': 'gpt-6.1-terra', 'reasoning_effort': 'low'}})]) + '\n')
            (root / '.codex/session_index.jsonl').write_text(json.dumps({'id': 'p1', 'thread_name': 'Fix the widget'}) + '\n')
            row, = read_activity(root, detailed=True)
            self.assertEqual((row['title'], row['model'], row['effort'], row['current_task']), ('Fix the widget', 'gpt-6.2-sol', 'medium', 'Patch'))
            self.assertEqual(row['todos'], [{'text': 'Inspect', 'status': 'completed'}, {'text': 'Patch', 'status': 'in_progress'}])
            self.assertEqual(row['subagents'], [dict(type='scout', description='Darwin', model='gpt-6.1-terra', effort='low', status='running')])
            self.assertNotIn('_thread', row)

    def test_opencode_sessions_need_a_live_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / '.local/share/opencode/opencode.db'
            database.parent.mkdir(parents=True)
            now = time.time()
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executescript('CREATE TABLE session (id, parent_id, title, agent, model, time_updated, time_archived);'
                                         'CREATE TABLE message (session_id, time_created, time_updated, data);'
                                         'CREATE TABLE todo (session_id, content, status, position);')
                model = json.dumps({'id': 'deepseek-v4.1', 'variant': 'high'})
                connection.executemany('INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, NULL)', [
                    ('s1', None, 'Build viewer', 'build', model, now * 1000), ('s2', 's1', 'Find f', 'scout', json.dumps({'id': 'gemini-3-flash', 'variant': 'default'}), now * 1000)])
                connection.executemany('INSERT INTO message VALUES (?, ?, ?, ?)', [
                    ('s1', now * 1000 - 5, now * 1000, json.dumps({'role': 'user'})),
                    ('s1', now * 1000, now * 1000, json.dumps({'role': 'assistant', 'modelID': 'deepseek-v4.1', 'variant': 'high', 'time': {}})),
                    ('s2', now * 1000, now * 1000, json.dumps({'role': 'assistant', 'time': {}}))])
                connection.execute("INSERT INTO todo VALUES ('s1', 'Scaffold', 'in_progress', 0)")
            self.assertEqual(read_activity(root, detailed=True, extra_agents=True), [])
            with patch('agent_activity.live_agent_files', return_value={str(database)}):
                self.assertEqual(read_activity(root, detailed=True), [])
                row, = read_activity(root, detailed=True, extra_agents=True, now=now)
            self.assertEqual((row['status'], row['title'], row['model'], row['effort'], row['current_task']), ('running', 'Build viewer', 'deepseek-v4.1', 'high', 'Scaffold'))
            self.assertEqual(row['subagents'], [dict(type='scout', description='Find f', model='gemini-3-flash', effort=None, status='running')])

    @unittest.skipIf(os.name == "nt", "the temp folder is inside home on Windows")
    def test_max_profile_huge_newest_line_and_short_timestamp_fraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'max/projects/-tmp/s.jsonl'
            self.write_claude(path, [{'type': 'assistant', 'message': {'content': [{'type': 'text'}], 'stop_reason': 'end_turn'}},
                                     {'type': 'user', 'timestamp': '2026-09-28T00:00:00Z', 'message': {'content': 'x' * 400000}}])
            with patch('agent_activity.claude_max_dir', return_value=root / 'max'):
                rows = read_activity(root, detailed=True)
            self.assertEqual([(item['status'], item['started']) for item in rows], [('running', timestamp('2026-09-28T00:00:00Z'))])
        self.assertEqual(timestamp('2026-10-02T10:00:28.21802+00:00'), timestamp('2026-10-02T10:00:28.218020+00:00'))
        self.assertEqual(timestamp('2026-10-02T10:00:28.2Z') % 1, timestamp('2026-10-02T10:00:28.200Z') % 1)

    def test_malformed_records_do_not_hide_other_sessions(self):
        start = {'type': 'user', 'message': {'content': 'start'}}
        tool = lambda name, data: {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't', 'name': name, 'input': data}]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row, = self.claude_rows(root, [start, tool('mcp__ccd_turn__update_plan', {'items': [{'content': 'A'}, {'content': 'B'}]}),
                                           tool('mcp__ccd_turn__update_plan', {'done': 2})])
            self.assertEqual([t['status'] for t in row['todos']], ['pending', 'completed'])
            row, = self.claude_rows(root, [start, tool(None, {})])
            self.assertEqual(row['label'], '도구 실행 중')
            sessions = root / '.codex/sessions/2026/11/20'
            sessions.mkdir(parents=True)
            (sessions / 'rollout.jsonl').write_text('\n'.join(json.dumps(line) for line in (
                {'type': 'session_meta', 'payload': {'id': 'abc'}}, {'type': 'event_msg', 'payload': {'type': 'task_started'}},
                {'type': 'turn_context', 'payload': {'collaboration_mode': 'default', 'model': 'gpt-6'}})) + '\n')
            rows = read_activity(root, detailed=True)
            self.assertEqual(next(r for r in rows if r['agent'] == 'codex')['model'], 'gpt-6')
            # Any other parser failure skips only that file.
            real = session_state
            def broken(path, agent):
                if agent == 'codex':
                    raise KeyError('boom')
                return real(path, agent)
            with patch('agent_activity.session_state', side_effect=broken), patch('sys.stderr'):
                self.assertEqual([r['agent'] for r in read_activity(root, detailed=True)], ['claude'])
