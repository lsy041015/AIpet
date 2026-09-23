import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone
from agent_activity import read_activity, session_state, ActivityTracker


class ActivityTests(unittest.TestCase):
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

    def test_antigravity_read_only_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / '.gemini/antigravity-cli/conversation_summaries.db'
            path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as connection:
                connection.execute('CREATE TABLE conversation_summaries(title,last_modified_time,not_fully_idle,killed,status,conversation_id,last_user_input_time)')
                timestamp = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', '391+00:00')
                connection.execute('INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?,?)', ('테스트 작업', timestamp, 1, 0, 'CASCADE_RUN_STATUS_RUNNING', 'test', timestamp))
            self.assertEqual(read_activity(root), [('antigravity', '작업 중 · 테스트 작업')])
            target = path.parent / 'conversations/test.db'
            target.parent.mkdir()
            with sqlite3.connect(target) as connection:
                connection.execute('CREATE TABLE steps(idx,status)')
                connection.execute('INSERT INTO steps VALUES(1,9)')
            self.assertEqual(read_activity(root, detailed=True)[0]['status'], 'waiting')
            with sqlite3.connect(target) as connection:
                connection.execute('UPDATE steps SET status=7')
            self.assertEqual(read_activity(root, detailed=True)[0]['status'], 'running')
            with sqlite3.connect(path) as connection:
                connection.execute('UPDATE conversation_summaries SET not_fully_idle=0')
            self.assertEqual(read_activity(root, detailed=True)[0]['status'], 'failed')
            with sqlite3.connect(target) as connection:
                connection.execute('UPDATE steps SET status=3')
            with sqlite3.connect(path) as connection:
                connection.execute("UPDATE conversation_summaries SET status='CASCADE_RUN_STATUS_IDLE'")
            self.assertEqual(read_activity(root), [])
