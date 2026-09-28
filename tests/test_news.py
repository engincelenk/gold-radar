"""Tests für das Nachrichten-Regelwerk. Ausführen: python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import news  # noqa: E402


def impact(title):
    hits = news.classify(title)
    return news.rate_item(hits)[0] if hits else None


class ClassifyTest(unittest.TestCase):
    def test_bullish(self):
        for t in [
            "Fed senkt Leitzins überraschend um 50 Basispunkte",
            "Zinssenkung im September rückt näher",
            "Dollar gibt nach - Euro legt zu",
            "Russischer Angriff in Ukraine: Drohnenattacke nahe Grenzübergang",
            "Rezessionsängste belasten die Märkte",
            "Zentralbanken stocken Goldreserven weiter auf",
            "Trump kritisiert Fed-Chef Powell scharf",
            "Börsenbeben: DAX bricht ein",
        ]:
            self.assertGreater(impact(t) or 0, 0, t)

    def test_bearish(self):
        for t in [
            "EZB erhöht den Leitzins erneut",
            "Zinssenkung vom Tisch: Fed bleibt hart",
            "Dollar legt zu und belastet Rohstoffe",
            "Rendite der zehnjährigen US-Staatsanleihen steigt auf Jahreshoch",
            "Waffenstillstand vereinbart - Friedensgespräche beginnen",
            "Starke Arbeitsmarktdaten dämpfen Zinshoffnungen",
        ]:
            self.assertLess(impact(t) or 0, 0, t)

    def test_deescalation_beats_escalation(self):
        self.assertLess(impact("Friedensgespräche nach schwerem Angriff vereinbart"), 0)

    def test_irrelevant_or_excluded(self):
        for t in [
            "Goldman Sachs hebt Kursziel für Allianz an",
            "Bundesliga: Bayern gewinnt nach Angriff über die Flügel",
            "Baufinanzierung: Bauzinsen steigen weiter",
            "Apple präsentiert neues iPhone",
        ]:
            self.assertIsNone(impact(t), t)

    def test_dollar_amounts_are_not_currency_moves(self):
        self.assertIsNone(impact("Umsatz steigt auf 3 Milliarden Dollar"))

    def test_gold_price_moves_are_unscored(self):
        hits = news.classify("Goldpreis steigt auf Rekordhoch")
        self.assertTrue(hits)
        self.assertFalse(any(r["scored"] for r in hits))

    def test_aggregate_caps_topic_and_labels(self):
        items = [{"_hits": news.classify("Angriff auf Ölanlage: Eskalation im Nahost-Konflikt")} for _ in range(6)]
        agg = news.aggregate(items)
        self.assertEqual(agg["score"], 3)  # Thema Geopolitik ist auf +3 gedeckelt
        self.assertEqual(agg["tone"], "up")
        self.assertEqual(news.aggregate([])["tone"], "flat")


if __name__ == "__main__":
    unittest.main()
