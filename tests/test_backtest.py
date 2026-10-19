"""Tests für den Rückblick (scripts/backtest.py). Ausführen: python -m unittest discover -s tests"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backtest  # noqa: E402
import update  # noqa: E402

CFG = update.CFG


def synthetic(n=900, seed=7):
    """Zufallsähnliche Kurse mit Aufwärtstrend; Gold, Silber, Realzins, Wechselkurse an denselben Handelstagen."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    gold = pd.Series(1500 * np.exp(np.cumsum(rng.normal(0.0004, 0.011, n))), index=idx)
    silver = pd.Series(18 * np.exp(np.cumsum(rng.normal(0.0003, 0.018, n))), index=idx)
    real = pd.Series(0.5 + np.cumsum(rng.normal(0, 0.03, n)), index=idx)
    eur = pd.Series(1.1 + np.cumsum(rng.normal(0, 0.002, n)), index=idx)
    tl = pd.Series(7 * np.exp(np.cumsum(rng.normal(0.0008, 0.004, n))), index=idx)
    return gold, silver, real, eur, tl


class SignalTest(unittest.TestCase):
    def test_band_series_matches_scalar_band(self):
        grid = np.linspace(-1, 120, 4001)
        for args in ((0.25, 0.15, 0.0, -0.08), (85, 70, 55, 45), (75, 70, 30, 25), (0.40, 0.15, -0.15, -0.40)):
            vec = backtest.band_series(pd.Series(grid), *args)
            ref = [update.band(v, *args) for v in grid]
            self.assertEqual(list(vec.astype(int)), ref, args)

    def test_nan_stays_nan(self):
        out = backtest.band_series(pd.Series([np.nan, 50.0]), 85, 70, 55, 45)
        self.assertTrue(np.isnan(out.iloc[0]))
        self.assertEqual(out.iloc[1], 1.0)   # 50 liegt zwischen 45 und 55 -> +1

    def test_matches_live_analyse_and_uses_no_future_data(self):
        """Das Rückblick-Signal eines Tages ist identisch zum Live-Signal, das man an dem Tag mit nur den Daten bis dahin berechnet hätte."""
        gold, silver, real, eur, tl = synthetic()
        df = backtest.signal_frame(gold, silver, real, eur, tl, CFG)
        valid = df[df.score.notna()]
        self.assertGreater(len(valid), 400)
        for pos in (10, 120, 300, len(valid) - 1):
            t = valid.index[pos]
            live = update.analyse(gold[:t], silver[:t], real[:t], eur[:t], tl[:t])
            self.assertEqual(int(valid.score.loc[t]), live["score"], str(t.date()))

    def test_future_changes_do_not_alter_past_signals(self):
        gold, silver, real, eur, tl = synthetic()
        full = backtest.signal_frame(gold, silver, real, eur, tl, CFG).score
        gold2 = gold.copy()
        gold2.iloc[-100:] *= 3   # radikal andere Zukunft
        changed = backtest.signal_frame(gold2, silver, real, eur, tl, CFG).score
        pd.testing.assert_series_equal(full.iloc[:-100], changed.iloc[:-100])

    def test_classify(self):
        self.assertEqual([backtest.classify(s, CFG) for s in (5, 4, 3, 2, 1, 0, -1, -2, -3, -4, -6)],
                         ["B", "B", "b", "b", "h", "h", "h", "s", "s", "S", "S"])


class SimulationTest(unittest.TestCase):
    def test_follows_signal_and_keeps_position_on_hold(self):
        prices = np.array([100.0, 110, 121, 121, 133.1, 133.1])
        groups = ["hold", "hold", "sell", "hold", "buy", "hold"]
        eq, pos = backtest.simulate(prices, groups)
        self.assertEqual(list(pos), [1, 1, 0, 0, 1, 1])
        # Tag 1,2: investiert (+10 %, +10 %); Tag 3: nach Verkauf Bargeld (0 %); Tag 4: noch Bargeld, Kauf wirkt erst ab Schluss; Tag 5: 0 %
        self.assertAlmostEqual(eq[2], 121.0)
        self.assertAlmostEqual(eq[3], 121.0)
        self.assertAlmostEqual(eq[4], 121.0)  # bis zum Kauf am Schluss von Tag 4 war man Bargeld
        self.assertAlmostEqual(eq[5], 121.0)

    def test_always_invested_equals_buy_and_hold(self):
        prices = np.array([100.0, 90, 120, 80, 100])
        eq, _ = backtest.simulate(prices, ["buy"] * 5)
        np.testing.assert_allclose(eq, prices)

    def test_max_drawdown(self):
        self.assertAlmostEqual(backtest.max_drawdown(np.array([100.0, 120, 60, 90])), -0.5)


class BuildTest(unittest.TestCase):
    def setUp(self):
        gold, silver, real, eur, tl = synthetic(1100)
        self.now = datetime(2023, 3, 1, 12, 0, tzinfo=timezone.utc)
        self.bt = backtest.build(gold, silver, real, eur, tl, CFG, now=self.now)

    def test_structure_and_sizes(self):
        bt = self.bt
        self.assertIn("1y", bt["periods"])
        self.assertIn("max", bt["periods"])
        p = bt["periods"]["1y"]
        n = len(p["t"])
        for c in backtest.CURRENCIES:
            self.assertEqual(len(p["strategy"][c]), n)
            self.assertEqual(len(p["hold"][c]), n)
        self.assertEqual(len(p["out"]), n)
        self.assertEqual(p["strategy"]["usd"][0], 100.0)
        self.assertEqual(p["hold"]["usd"][0], 100.0)
        s = bt["strip"]
        self.assertEqual(len(s["dates"]), len(s["sig"]))
        for key in ("usd_1m", "usd_3m", "try_1m", "eur_3m"):
            self.assertEqual(len(s["ok"][key]), len(s["dates"]))
            self.assertEqual(len(s["ret"][key]), len(s["dates"]))

    def test_recent_days_are_pending_older_days_are_scored(self):
        ok = self.bt["strip"]["ok"]["usd_1m"]
        self.assertEqual(ok[-21:], "-" * 21)        # die letzten 21 Handelstage sind noch offen
        self.assertNotIn("-", ok[:-21])
        self.assertEqual(self.bt["strip"]["ok"]["usd_3m"][-63:], "-" * 63)

    def test_stats_are_consistent(self):
        st = self.bt["periods"]["max"]["stats"]["usd"]["1m"]
        total = sum(st[g]["n"] for g in ("buy", "hold", "sell"))
        self.assertEqual(total, self.bt["periods"]["max"]["days"] - 21)   # Tage mit bekanntem Folgekurs
        for g in ("buy", "hold", "sell"):
            if st[g]["n"]:
                self.assertTrue(0 <= st[g]["hit"] <= 100)

    def test_unfinished_trading_day_is_excluded(self):
        gold, silver, real, eur, tl = synthetic(1100)
        last = gold.index[-1]
        before_close = datetime(last.year, last.month, last.day, 15, 0, tzinfo=timezone.utc)   # Handel läuft noch
        bt = backtest.build(gold, silver, real, eur, tl, CFG, now=before_close)
        self.assertLess(bt["end"], last.strftime("%Y-%m-%d"))
        after_close = datetime(last.year, last.month, last.day, 23, 45, tzinfo=timezone.utc)
        bt2 = backtest.build(gold, silver, real, eur, tl, CFG, now=after_close)
        self.assertEqual(bt2["end"], last.strftime("%Y-%m-%d"))


class DueTest(unittest.TestCase):
    def test_is_due(self):
        gen = "2026-10-19T23:40+00:00"
        t = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=timezone.utc)  # noqa: E731
        self.assertTrue(backtest.is_due(None, t(19, 12, 0)))
        self.assertFalse(backtest.is_due(gen, t(20, 12, 0)))      # neuer Tag, aber Handelsschluss noch nicht erreicht
        self.assertTrue(backtest.is_due(gen, t(20, 23, 31)))      # nach Handelsschluss
        self.assertFalse(backtest.is_due(gen, t(19, 23, 55)))     # am selben Tag nicht noch einmal
        self.assertTrue(backtest.is_due(gen, t(21, 12, 0)))       # älter als 36 Stunden -> auf jeden Fall nachholen


if __name__ == "__main__":
    unittest.main()
