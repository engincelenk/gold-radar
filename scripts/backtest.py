"""
Gold-Radar – Rückblick: Hätte man dem Signal folgen sollen?

Rechnet die vier Werkzeuge mit denselben Schwellen (`CFG` aus scripts/update.py) für jeden
Handelstag der letzten rund 10 Jahre nach und prüft hinterher, ob das Signal richtig lag:

  * Prüfzeitraum 1 und 3 Monate (21 / 63 Handelstage): Kaufen war richtig, wenn der Kurs danach höher
    stand; Verkaufen, wenn er tiefer stand; Halten, wenn der Kurs ruhiger blieb als an einem typischen
    Tag (Median der Kursbewegungen im selben Prüfzeitraum).
  * Strategie "Signal folgen": Ausgangslage investiert. Kaufen/Stark kaufen -> investiert, Verkaufen/Stark
    verkaufen -> Bargeld (ohne Zinsen), Halten -> Position behalten. Wirkt ab dem Schlusskurs des Signaltags.
    Vergleich: Gold einfach durchgehend halten.

Kein Blick in die Zukunft: Jedes Signal nutzt nur Kurse bis zu diesem Tag (gleitende Durchschnitte, RSI und
Zinsänderung sind rückwärts gerechnet; die Folgekurse dienen ausschließlich der Bewertung danach).
Nicht berücksichtigt: Gebühren, Spreads, Steuern, Zinsen auf Bargeld. Alle Werte stammen aus der Rückrechnung,
nicht aus echten Handelsentscheidungen.

Ergebnis: docs/backtest.json (wird einmal täglich neu berechnet, siehe `is_due`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HORIZONS = {"1m": 21, "3m": 63}               # Handelstage
PERIODS = {"1y": 252, "3y": 756, "5y": 1260, "max": None}
STRIP_DAYS = 252
CURRENCIES = ("usd", "eur", "try")
CLOSE_UTC = (23, 30)                           # danach gilt die Handelssitzung des Tages als abgeschlossen
MIN_WINDOW = 60                                # kürzere Zeiträume werden nicht ausgewertet


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def band_series(v: pd.Series, hi2: float, hi1: float, lo1: float, lo2: float) -> pd.Series:
    """Wie update.band(), nur für ganze Reihen: -2 teuer … +2 günstig, NaN bleibt NaN."""
    out = np.select([v >= hi2, v >= hi1, v <= lo2, v <= lo1], [-2.0, -1.0, 2.0, 1.0], default=0.0)
    return pd.Series(np.where(v.isna(), np.nan, out), index=v.index)


def signal_frame(gold: pd.Series, silver: pd.Series, real: pd.Series, eurusd: pd.Series, usdtry: pd.Series, cfg: dict) -> pd.DataFrame:
    """Tageswerte: Kurse in USD/EUR/TRY und das Punkte-Signal ("score") nur aus Daten bis zu diesem Tag."""
    df = pd.DataFrame({"gold": gold, "silver": silver}).dropna()
    df["eur"] = df.gold / eurusd.reindex(df.index, method="ffill")
    df["try"] = df.gold * usdtry.reindex(df.index, method="ffill")
    dev200 = df.gold / df.gold.rolling(200).mean() - 1
    r = real.dropna()
    rate_chg = r.diff(cfg["rate_lookback_days"]).reindex(df.index, method="ffill")
    gsr = df.gold / df.silver
    c = cfg["ma"]
    s_ma = band_series(dev200, c["exp2"], c["exp1"], c["cheap1"], c["cheap2"])
    c = cfg["rate"]
    s_rate = band_series(rate_chg, c["up2"], c["up1"], c["down1"], c["down2"])
    c = cfg["gsr"]
    s_gsr = band_series(gsr, c["exp2"], c["exp1"], c["cheap1"], c["cheap2"])
    c = cfg["rsi"]
    s_rsi = band_series(rsi(df.gold), c["exp2"], c["exp1"], c["cheap1"], c["cheap2"])
    df["score"] = s_ma + s_rate + s_gsr + s_rsi
    return df


def classify(score: float, cfg: dict) -> str:
    """B = Stark kaufen, b = Kaufen, h = Halten, s = Verkaufen, S = Stark verkaufen."""
    v = cfg["verdict"]
    if score >= v["strong"]:
        return "B"
    if score >= v["normal"]:
        return "b"
    if score <= -v["strong"]:
        return "S"
    if score <= -v["normal"]:
        return "s"
    return "h"


def to_group(ch: str) -> str:
    return {"B": "buy", "b": "buy", "S": "sell", "s": "sell"}.get(ch, "hold")


def simulate(prices: np.ndarray, groups: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Gleichgewichtskurve (Start 100) und Position (1 = investiert, 0 = Bargeld) je Tag."""
    n = len(prices)
    eq, pos, cur = np.empty(n), np.empty(n), 1.0
    for i in range(n):
        if groups[i] == "buy":
            cur = 1.0
        elif groups[i] == "sell":
            cur = 0.0
        pos[i] = cur
        eq[i] = 100.0 if i == 0 else eq[i - 1] * (1 + pos[i - 1] * (prices[i] / prices[i - 1] - 1))
    return eq, pos


def max_drawdown(curve: np.ndarray) -> float:
    peak = np.maximum.accumulate(curve)
    return float((curve / peak - 1).min())


def _weekly_idx(dates: pd.DatetimeIndex) -> list[int]:
    iso = dates.isocalendar()
    key = (iso.year.to_numpy() * 100 + iso.week.to_numpy())
    n = len(dates)
    idx = {0, n - 1} | {i for i in range(n - 1) if key[i] != key[i + 1]}
    return sorted(idx)


def _r(x: float, d: int = 1):
    return None if x is None or not np.isfinite(x) else round(float(x), d)


def _stats(group: np.ndarray, ret: np.ndarray, calm: float) -> dict:
    """Trefferquote und Ø-Kursänderung je Signalgruppe für einen Prüfzeitraum."""
    out = {}
    for g in ("buy", "hold", "sell"):
        m = (group == g) & np.isfinite(ret)
        n, days = int(m.sum()), int((group == g).sum())     # n: Tage mit bereits bekanntem Folgekurs, days: alle Tage mit diesem Signal
        if n == 0:
            out[g] = {"days": days, "n": 0, "hit": None, "avg": None}
            continue
        r = ret[m]
        hit = {"buy": r > 0, "sell": r < 0, "hold": np.abs(r) <= calm}[g]
        out[g] = {"days": days, "n": n, "hit": _r(hit.mean() * 100), "avg": _r(r.mean() * 100)}
    known = np.isfinite(ret)
    out["up"] = _r((ret[known] > 0).mean() * 100) if known.any() else None   # Basis: Anteil steigender Zeiträume
    return out


def _episodes(group: np.ndarray) -> dict:
    """Anzahl zusammenhängender Signalphasen je Gruppe (die Tage einer Phase sind keine unabhängigen Beobachtungen)."""
    out = {"buy": 0, "hold": 0, "sell": 0}
    prev = None
    for g in group:
        if g != prev:
            out[g] += 1
        prev = g
    return out


def is_due(generated: str | None, now: datetime | None = None) -> bool:
    """Einmal pro UTC-Tag nach Handelsschluss (23:30), spätestens nach 36 Stunden; ohne vorhandene Datei sofort."""
    now = now or datetime.now(timezone.utc)
    if not generated:
        return True
    gen = datetime.fromisoformat(generated)
    if now - gen > timedelta(hours=36):
        return True
    return gen.date() < now.date() and (now.hour, now.minute) >= CLOSE_UTC


def completed_cutoff(now: datetime | None = None):
    """Letzter Kalendertag, dessen Handelssitzung abgeschlossen ist (heutiger Balken zählt erst nach Handelsschluss)."""
    now = now or datetime.now(timezone.utc)
    day = now.date()
    return day if (now.hour, now.minute) >= CLOSE_UTC else day - timedelta(days=1)


def build(gold: pd.Series, silver: pd.Series, real: pd.Series, eurusd: pd.Series, usdtry: pd.Series, cfg: dict,
          now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = pd.Timestamp(completed_cutoff(now))
    gold, silver = gold[gold.index <= cutoff], silver[silver.index <= cutoff]
    df = signal_frame(gold, silver, real, eurusd, usdtry, cfg)
    prices = {"usd": df.gold, "eur": df.eur, "try": df["try"]}
    fwd = {(c, h): (prices[c].shift(-n) / prices[c] - 1).to_numpy() for c in CURRENCIES for h, n in HORIZONS.items()}
    valid = df.score.notna().to_numpy()
    sf = df[valid]
    if len(sf) < MIN_WINDOW:
        raise RuntimeError(f"zu wenig Signaltage für den Rückblick: {len(sf)}")
    chars = [classify(s, cfg) for s in sf.score]
    groups = np.array([to_group(c) for c in chars])
    fwd = {k: v[valid] for k, v in fwd.items()}
    px = {c: prices[c].to_numpy()[valid] for c in CURRENCIES}

    # "Ruhig": typische (mediane) Kursbewegung im Prüfzeitraum, über den gesamten Zeitraum je Währung
    calm = {(c, h): float(np.nanmedian(np.abs(fwd[(c, h)]))) for c in CURRENCIES for h in HORIZONS}

    # Rückblick-Balken der letzten Handelstage mit Trefferkennzeichen
    k = min(STRIP_DAYS, len(sf))
    strip = {"dates": [d.strftime("%Y-%m-%d") for d in sf.index[-k:]], "sig": "".join(chars[-k:]), "ok": {}, "ret": {}}
    for c in CURRENCIES:
        for h in HORIZONS:
            ret = fwd[(c, h)][-k:]
            g = groups[-k:]
            hit = np.where(g == "buy", ret > 0, np.where(g == "sell", ret < 0, np.abs(ret) <= calm[(c, h)]))
            strip["ok"][f"{c}_{h}"] = "".join("-" if not np.isfinite(r) else ("1" if x else "0") for r, x in zip(ret, hit))
            strip["ret"][f"{c}_{h}"] = [_r(r * 100) for r in ret]

    periods = {}
    for key, length in PERIODS.items():
        if length is not None and length > len(sf):
            continue
        lo = 0 if length is None else len(sf) - length
        w = slice(lo, len(sf))
        dates = sf.index[w]
        widx = _weekly_idx(dates)
        p = {"start": dates[0].strftime("%Y-%m-%d"), "end": dates[-1].strftime("%Y-%m-%d"), "days": len(dates),
             "t": [dates[i].strftime("%Y-%m-%d") for i in widx], "strategy": {}, "hold": {}, "summary": {}, "stats": {}}
        eq0, pos0 = simulate(px["usd"][w], list(groups[w]))                  # Position hängt nur vom Signal (USD) ab
        p["out"] = [int(pos0[i] == 0) for i in widx]
        p["episodes"] = _episodes(groups[w])
        for c in CURRENCIES:
            pr = px[c][w]
            if not np.isfinite(pr).all():
                continue
            eq, pos = simulate(pr, list(groups[w]))
            hold = 100 * pr / pr[0]
            p["strategy"][c] = [_r(eq[i]) for i in widx]
            p["hold"][c] = [_r(hold[i]) for i in widx]
            p["summary"][c] = {
                "strategy": _r(eq[-1] - 100), "hold": _r(hold[-1] - 100),
                "dd_strategy": _r(max_drawdown(eq) * 100), "dd_hold": _r(max_drawdown(hold) * 100),
                "trades": int((np.diff(pos) != 0).sum()), "invested": _r(pos.mean() * 100, 0),
            }
            p["stats"][c] = {h: _stats(groups[w], fwd[(c, h)][w], calm[(c, h)]) for h in HORIZONS}
        periods[key] = p

    return {
        "generated": now.isoformat(timespec="minutes"), "start": sf.index[0].strftime("%Y-%m-%d"), "end": sf.index[-1].strftime("%Y-%m-%d"),
        "horizons": HORIZONS, "calm": {f"{c}_{h}": _r(v * 100, 2) for (c, h), v in calm.items()},
        "strip": strip, "periods": periods,
    }
