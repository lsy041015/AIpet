import unittest
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo
import providers
from providers import parse_codex, parse_antigravity, parse_copilot, parse_claude, claude_reset, claude_reset_moment, percent, reset_text, QuotaError

DEFAULT_CLAUDE = {"id": "a", "kind": "claude", "label": "", "profile_dir": "", "use_default": True}


def isolated(profile):
    return {"id": "b", "kind": "claude", "label": "", "profile_dir": str(profile), "use_default": False}


class QuotaTests(unittest.TestCase):
    def test_codex_primary_can_be_weekly_and_secondary_can_be_missing(self):
        bucket = {"primary": {"usedPercent": 56, "windowDurationMins": 10080, "resetsAt": 1789435683}, "secondary": None}
        rows = parse_codex({"rateLimits": bucket, "rateLimitsByLimitId": {"codex": bucket}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["remaining"], 44)
        self.assertIn("주간", rows[0]["label"])
        self.assertEqual(parse_codex({"rateLimits": bucket})[0]["remaining"], 44)

    def test_antigravity_real_tsv_zero_and_fractional(self):
        rows = parse_antigravity("Gemini Models\tWeekly Limit Remaining\t93%\t2026-09-11T00:02:30Z\nClaude and GPT models\tFive Hour Limit Remaining\t0%\t2026-09-11T04:20:47Z")
        self.assertEqual([r["remaining"] for r in rows], [93, 0])
        self.assertEqual(rows[1]["label"], "Claude and GPT · 5시간")
        with self.assertRaises(QuotaError):
            parse_antigravity("Please log in")

    def test_copilot_credits_unlimited_and_zero(self):
        rows = parse_copilot({"quota_reset_date": "2026-10-01", "quota_snapshots": {
            "chat": {"unlimited": True, "entitlement": 0},
            "premium_interactions": {"percent_remaining": 0, "entitlement": 200, "quota_remaining": 0, "token_based_billing": True}}})
        self.assertIsNone(rows[0]["remaining"])
        self.assertEqual(rows[1]["remaining"], 0)
        self.assertEqual(rows[1]["detail"], "0/200 크레딧")

    def test_claude_cli_usage_rows_and_errors(self):
        data = {'is_error': False, 'local_command': 'usage', 'result':
                'Current session: 1% used · resets Sep 28, 1pm (Asia/Seoul)\n'
                'Current week (all models): 33% used · resets Sep 30, 3pm (Asia/Seoul)\n'
                'Current week (Fable): 0% used · resets Sep 30, 3pm (Asia/Seoul)\n'
                'Top MCP servers: tool 3%'}
        rows = parse_claude(data)
        self.assertEqual([row['remaining'] for row in rows], [99, 67, 100])
        self.assertEqual([row['label'] for row in rows], ['현재 세션', '이번 주 · 전체 모델', '이번 주 · Fable'])
        self.assertEqual(rows[0]['reset'], datetime(2026, 9, 28, 13, tzinfo=ZoneInfo('Asia/Seoul')).astimezone().strftime('%m/%d %H:%M 초기화'))
        for invalid in ({'is_error': True, 'result': data['result']},
                        {'is_error': False, 'result': 'not a usage response'},
                        {'is_error': False, 'result': 'Current session: 120% used · resets tomorrow'}):
            with self.assertRaises(QuotaError):
                parse_claude(invalid)

    def test_claude_reset_is_local_time_like_other_services(self):
        now = datetime(2026, 10, 2, 19, 0, tzinfo=ZoneInfo('Asia/Seoul'))

        def local(*moment, zone='Asia/Seoul'):
            return datetime(*moment, tzinfo=ZoneInfo(zone)).astimezone().strftime('%m/%d %H:%M 초기화')

        cases = {'Oct 6, 2pm (Asia/Seoul)': local(2026, 10, 6, 14),
                 'Oct 6, 1:59pm (Asia/Seoul)': local(2026, 10, 6, 13, 59),
                 'Oct 3, 12am (Asia/Seoul)': local(2026, 10, 3, 0),
                 'Oct 2, 12pm (Asia/Seoul)': local(2026, 10, 2, 12),
                 '11pm (Asia/Seoul)': local(2026, 10, 2, 23),
                 # Past dates are next year's: 2027-03-10 is still EST in New York.
                 'Mar 10, 9am (America/New_York)': local(2027, 3, 10, 9, zone='America/New_York')}
        for text, expected in cases.items():
            self.assertEqual(claude_reset(text, now), expected, text)
        for unknown in ('tomorrow', '1pm', 'Oct 6, 13pm (Asia/Seoul)', 'Oct 6, 2pm (No/Zone)', 'Okt 6, 2pm (UTC)'):
            self.assertEqual(claude_reset(unknown, now), unknown + ' 초기화')

    def test_default_claude_ignores_inherited_profile(self):
        auth = {'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'team', 'email': 'work@example.test',
                'orgName': 'Example Org', 'planTier': 'other'}
        usage = {'is_error': False, 'result': 'Current session: 20% used · resets 1pm'}
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': '/max'}), \
             patch('providers.output', side_effect=[json.dumps(auth), json.dumps(usage)]) as output:
            snapshot = providers.claude(DEFAULT_CLAUDE)
            self.assertEqual((snapshot['plan'], snapshot['org'], snapshot['tier']), ('team', 'Example Org', 'other'))
            for call in output.call_args_list:
                self.assertNotIn('CLAUDE_CONFIG_DIR', call.kwargs['env'])

    def test_isolated_claude_uses_its_own_login_and_reports_account(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'max'
            profile.mkdir()
            auth = {'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'max', 'planTier': 'max_5x',
                    'configDirectory': str(profile), 'email': 'personal@example.test'}
            usage = {'is_error': False, 'result': 'Current session: 20% used · resets 1pm'}
            with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': '/business', 'CLAUDE_CODE_OAUTH_TOKEN': 'other-account'}), \
                 patch('providers.output', side_effect=[json.dumps(auth), json.dumps(usage)]) as output:
                snapshot = providers.claude(isolated(profile))
                self.assertEqual(snapshot['account'], 'personal@example.test')
                self.assertEqual((snapshot['plan'], snapshot['tier']), ('max', 'max_5x'))
                self.assertEqual(snapshot['rows'][0]['remaining'], 80)
                for call in output.call_args_list:
                    env = call.kwargs['env']
                    self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(profile))
                    self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', env)
                self.assertEqual(os.environ['CLAUDE_CONFIG_DIR'], '/business')

    def test_isolated_claude_requires_its_profile_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'not-created'
            with patch('providers.output') as output:
                with self.assertRaises(providers.QuotaError):
                    providers.claude(isolated(profile))
                output.assert_not_called()
                self.assertFalse(profile.exists())

    def test_isolated_claude_never_falls_back_to_another_login(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'max'
            profile.mkdir()
            for auth in ({'loggedIn': False},
                         {'loggedIn': True, 'authMethod': 'console', 'configDirectory': str(profile)},
                         {'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'max', 'configDirectory': '/business'}):
                with self.subTest(auth=auth), patch('providers.output', return_value=json.dumps(auth)) as output:
                    with self.assertRaises(providers.QuotaError):
                        providers.claude(isolated(profile))
                    self.assertEqual(output.call_count, 1)

    def test_invalid_data_is_never_full_quota(self):
        for invalid in (float("nan"), float("inf"), -1, 101, True):
            with self.assertRaises(ValueError):
                percent(invalid)
        for function in (parse_codex, parse_copilot):
            with self.assertRaises(QuotaError):
                function({})

    def test_date_only_reset_does_not_invent_a_time(self):
        self.assertEqual(reset_text("2026-10-01"), "10/01 초기화 · 시각 미제공")
        self.assertEqual(reset_text("invalid"), "초기화 시각 미제공")

    def test_rows_carry_exact_reset_timestamp(self):
        bucket = {"primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 1790000000}}
        self.assertEqual(parse_codex({"rateLimits": bucket})[0]["reset_at"], 1790000000.0)
        row = parse_antigravity("Gemini Models\tWeekly Limit Remaining\t93%\t2026-10-07T02:26:00Z")[0]
        self.assertEqual(row["reset_at"], datetime(2026, 10, 7, 2, 26, tzinfo=timezone.utc).timestamp())
        copilot = parse_copilot({"quota_snapshots": {"chat": {"percent_remaining": 50, "reset_date": "2026-11-01"}}})
        self.assertIsNone(copilot[0]["reset_at"])
        now = datetime(2026, 10, 2, 19, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertEqual(claude_reset_moment("Oct 6, 2pm (Asia/Seoul)", now).timestamp(),
                         datetime(2026, 10, 6, 14, tzinfo=ZoneInfo("Asia/Seoul")).timestamp())
        self.assertIsNone(claude_reset_moment("tomorrow", now))
        rows = parse_claude({"is_error": False, "result": "Current session: 1% used · resets tomorrow\n"
                             "Current week (all models): 5% used"})
        self.assertEqual([row["reset_at"] for row in rows], [None, None])
        self.assertIn("reset_at", parse_claude({"is_error": False, "result": "Current session: 1% used · resets Oct 6, 2pm (Asia/Seoul)"})[0])


if __name__ == "__main__":
    unittest.main()
