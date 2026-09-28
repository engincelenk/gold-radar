"""
Gold-Radar – echter Spotpreis (goldprice.dev), budgetiert.

scripts/update.py holt den Goldpreis für Chart und Signale von Yahoo (`GC=F`, ein COMEX-Future).
Der Future weicht vom Spotpreis ab, den z. B. finanzen.net oder die Deutsche Börse zeigen
(üblich: 0,2–1 %, "Contango/Backwardation"). goldprice.dev liefert den echten Spotpreis
(XAU/USD), der Gratis-Plan erlaubt aber nur 1.000 Anrufe pro Monat.

Statt eines festen Intervalls verteilt `should_fetch()` ein Monatsbudget (Standard 950, Puffer
unter dem Limit von 1.000) gleichmäßig über den Kalendermonat: Läuft ein Update seltener als
geplant (Ausfall, Pause), holt die Steuerung automatisch auf, sobald wieder Zeit im Budget ist.
Im Schnitt ergibt das bei einem Lauf alle 15 Minuten (2.880 Läufe/Monat) einen Abstand von
rund 46 Minuten zwischen den Spotpreis-Abrufen; ganz ohne API-Key wird gar nicht erst versucht.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import requests

API_URL = "https://api.goldprice.dev/v1/prices"
SYMBOL = "XAU-USD-SPOT"
DEFAULT_BUDGET = 950  # < 1.000 (Gratis-Grenze), etwas Puffer für Wiederholungsversuche


def _month_key(now: datetime) -> str:
    return now.strftime("%Y-%m")


def should_fetch(usage: dict, now: datetime, budget: int = DEFAULT_BUDGET) -> tuple[bool, dict]:
    """Budget für den bisherigen Anteil des Monats: erst abrufen, wenn calls < budget * Monatsanteil."""
    usage = usage if usage.get("month") == _month_key(now) else {"month": _month_key(now), "calls": 0}
    days = calendar.monthrange(now.year, now.month)[1]
    seconds_in = (now.day - 1) * 86400 + now.hour * 3600 + now.minute * 60 + now.second
    elapsed_fraction = seconds_in / (days * 86400)
    target = budget * elapsed_fraction
    return usage["calls"] < budget and usage["calls"] < target, usage


def fetch(api_key: str) -> dict | None:
    r = requests.get(API_URL, params={"symbol": SYMBOL},
                      headers={"Authorization": f"Bearer {api_key}", "User-Agent": "gold-radar"}, timeout=15)
    r.raise_for_status()
    row = r.json()["symbols"][0]
    if row.get("is_stale") or not row.get("price"):
        raise RuntimeError(f"goldprice.dev liefert veralteten/leeren Preis: {row}")
    return {"usd_oz": round(float(row["price"]), 2), "computed_at": row.get("computed_at")}


def get_spot(prev: dict, api_key: str | None, now: datetime | None = None, budget: int = DEFAULT_BUDGET) -> tuple[dict | None, dict]:
    """Liefert (spot, usage). spot ist der neue oder (bei Skip/Fehler) der zuletzt bekannte Wert."""
    now = now or datetime.now(timezone.utc)
    spot = prev.get("spot")
    if not api_key:
        return spot, prev.get("spot_usage", {})

    ok, usage = should_fetch(prev.get("spot_usage", {}), now, budget)
    if not ok:
        print(f"  Spot (goldprice.dev): Budget diesen Monat ausgeschöpft für jetzt ({usage['calls']}/{budget}), übersprungen")
        return spot, usage

    try:
        fresh = fetch(api_key)
        usage = {**usage, "calls": usage["calls"] + 1}
        spot = {**fresh, "fetched_at": now.isoformat(timespec="minutes")}
        print(f"  Spot (goldprice.dev): {spot['usd_oz']} $/oz (Anruf {usage['calls']}/{budget} diesen Monat)")
    except Exception as e:  # noqa: BLE001
        print(f"  goldprice.dev fehlgeschlagen: {e}")
    return spot, usage
