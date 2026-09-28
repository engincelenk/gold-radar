"""Tests für die Budget-Steuerung des Spotpreis-Abrufs. Ausführen: python -m unittest discover -s tests"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import spot  # noqa: E402


class BudgetTest(unittest.TestCase):
    def test_start_of_month_fetches_immediately(self):
        now = datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc)
        ok, usage = spot.should_fetch({}, now)
        self.assertTrue(ok)
        self.assertEqual(usage, {"month": "2026-09", "calls": 0})

    def test_ahead_of_schedule_skips(self):
        now = datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc)  # <1 Promille des Monats vergangen
        ok, _ = spot.should_fetch({"month": "2026-09", "calls": 5}, now)
        self.assertFalse(ok)

    def test_never_exceeds_budget_even_with_frequent_calls(self):
        """Simuliert einen Lauf alle 15 Minuten für einen vollen 30-Tage-Monat: Aufrufe bleiben unter dem Budget."""
        now = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
        end = now + timedelta(days=30)
        usage, calls = {}, 0
        while now < end:
            ok, usage = spot.should_fetch(usage, now, budget=950)
            if ok:
                calls += 1
                usage = {**usage, "calls": usage["calls"] + 1}
            now += timedelta(minutes=15)
        self.assertLessEqual(calls, 950)
        self.assertGreater(calls, 900)  # Budget wird auch tatsächlich weitgehend ausgeschöpft, nicht nur eingehalten

    def test_recovers_after_outage(self):
        """Nach einer Downtime (keine Aufrufe für Tage) holt die Steuerung zügig wieder auf, statt für immer zu pausieren."""
        usage = {"month": "2026-09", "calls": 0}
        now = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)  # 20/30 Tage vergangen, 0 Aufrufe -> weit im Rückstand
        ok, _ = spot.should_fetch(usage, now, budget=950)
        self.assertTrue(ok)

    def test_new_month_resets_counter(self):
        usage = {"month": "2026-08", "calls": 950}  # Budget im August ausgeschöpft
        now = datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc)
        ok, new_usage = spot.should_fetch(usage, now)
        self.assertTrue(ok)
        self.assertEqual(new_usage["month"], "2026-09")
        self.assertEqual(new_usage["calls"], 0)

    def test_budget_exhausted_for_month_blocks_further_calls(self):
        now = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)
        ok, _ = spot.should_fetch({"month": "2026-09", "calls": 950}, now)
        self.assertFalse(ok)


class GetSpotTest(unittest.TestCase):
    def test_no_api_key_never_calls_network(self):
        spot.fetch = lambda key: (_ for _ in ()).throw(AssertionError("fetch() darf ohne Key nicht aufgerufen werden"))
        result, usage = spot.get_spot({"spot": {"usd_oz": 4100.0}}, api_key=None)
        self.assertEqual(result, {"usd_oz": 4100.0})
        self.assertEqual(usage, {})

    def test_fetch_failure_keeps_previous_value(self):
        spot.fetch = lambda key: (_ for _ in ()).throw(RuntimeError("Netzwerkfehler"))
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        result, usage = spot.get_spot({"spot": {"usd_oz": 4100.0}}, api_key="x", now=now)
        self.assertEqual(result, {"usd_oz": 4100.0})
        self.assertEqual(usage["calls"], 0)  # fehlgeschlagener Versuch zählt nicht gegen das Budget

    def test_successful_fetch_updates_value_and_usage(self):
        spot.fetch = lambda key: {"usd_oz": 4200.5, "computed_at": "2026-09-01T12:00:00Z"}
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        result, usage = spot.get_spot({}, api_key="x", now=now)
        self.assertEqual(result["usd_oz"], 4200.5)
        self.assertEqual(result["fetched_at"], "2026-09-01T12:00+00:00")
        self.assertEqual(usage["calls"], 1)


if __name__ == "__main__":
    unittest.main()
