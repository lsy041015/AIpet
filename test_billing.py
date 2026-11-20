import unittest
from datetime import date
from billing import next_payment, payment_summary


class BillingTests(unittest.TestCase):
    def test_month_end_uses_original_day_without_drift(self):
        record = {"date": "2026-01-31", "period": "monthly"}
        self.assertEqual(next_payment(record, date(2026, 2, 1)), date(2026, 2, 28))
        self.assertEqual(next_payment(record, date(2026, 3, 1)), date(2026, 3, 31))
        self.assertEqual(next_payment(record, date(2026, 12, 31)), date(2026, 12, 31))
        self.assertEqual(next_payment(record, date(2027, 1, 1)), date(2027, 1, 31))

    def test_yearly_leap_day_and_future_anchor(self):
        record = {"date": "2024-02-29", "period": "yearly"}
        self.assertEqual(next_payment(record, date(2025, 2, 1)), date(2025, 2, 28))
        self.assertEqual(next_payment(record, date(2028, 2, 1)), date(2028, 2, 29))
        self.assertEqual(next_payment({"date": "2027-10-15", "period": "monthly"}, date(2026, 9, 28)), date(2027, 10, 15))

    def test_unset_invalid_and_expired_dates_do_not_invent_payments(self):
        for record in (None, {}, {"date": "invalid"}, {"date": "2026-02-31", "period": "monthly"}, {"date": "2026-10-01", "period": "invalid"}):
            self.assertIsNone(next_payment(record, date(2026, 9, 28)))
        one_time = {"date": "2026-09-27", "period": "once"}
        self.assertEqual(next_payment(one_time, date(2026, 9, 28)), date(2026, 9, 27))
        self.assertIn("확인 필요", payment_summary(one_time, date(2026, 9, 28)))
        self.assertIn("오늘", payment_summary({"date": "2026-09-28", "period": "monthly"}, date(2026, 9, 28)))
        self.assertIn("미설정", payment_summary(None, date(2026, 9, 28)))
