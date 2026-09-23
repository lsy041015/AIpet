import unittest
from providers import parse_codex, parse_antigravity, parse_copilot, percent, reset_text, QuotaError


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


if __name__ == "__main__":
    unittest.main()
