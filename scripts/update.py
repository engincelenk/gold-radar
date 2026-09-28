"""
Gold-Radar – tägliches Update.

Setzt die 4 Werkzeuge aus dem Video um:
  1. Abstand zum 50- und 200-Tage-Durchschnitt  (Preis vs. "fairer Wert")
  2. Zins-Gold-Beziehung (US-Realzins, 10J TIPS, FRED DFII10)
  3. Gold/Silber-Ratio (historisch gesund: 55–70)
  4. RSI 14 (>70 überhitzt, <30 Angst)

Dazu: Goldpreis-Verläufe (Heute … Max, USD und EUR) und eine Nachrichten-Analyse
(scripts/news.py). Schreibt docs/data.json für das Dashboard und verschickt bei
Signalwechsel eine Push-Nachricht (ntfy und/oder Telegram).
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import news
import spot as spotmod

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data.json"
GRAM_PER_OZ = 31.1034768
UA = {"User-Agent": "Mozilla/5.0 (gold-radar)"}

# ---------- Schwellenwerte (hier anpassen) ----------
CFG = {
    "ma": {"exp2": 0.25, "exp1": 0.15, "cheap1": 0.00, "cheap2": -0.08},
    "rate_lookback_days": 63,  # ~3 Monate Handelstage
    "rate": {"up2": 0.40, "up1": 0.15, "down1": -0.15, "down2": -0.40},
    "gsr": {"exp2": 85, "exp1": 70, "cheap1": 55, "cheap2": 45},
    "rsi": {"exp2": 75, "exp1": 70, "cheap1": 30, "cheap2": 25},
    "verdict": {"strong": 4, "normal": 2},
}

# Zeiträume der Preisgrafik: Schlüssel, Yahoo-Range, Intervall, max. Zeitabstand bei der EUR/USD-Zuordnung
RANGES = [
    ("day", "1d", "5m", "30min"),
    ("week", "5d", "15m", "1h"),
    ("month", "1mo", "60m", "3h"),
    ("year", "1y", "1d", "2D"),
    ("10y", "10y", "1wk", "4D"),
    ("max", "max", "1mo", "16D"),
]


# ---------- Datenquellen ----------
def yahoo(symbol: str, years: int = 5) -> pd.Series:
    """Tagesschlusskurse von Yahoo Finance (Chart-API, kein Key nötig)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    for attempt in range(3):  # Yahoo antwortet gelegentlich mit 429/5xx: kurz warten, erneut versuchen
        r = requests.get(url, params={"range": f"{years}y", "interval": "1d"}, headers=UA, timeout=30)
        if r.status_code < 400 or attempt == 2:
            break
        time.sleep(3 * (attempt + 1))
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    idx = pd.to_datetime(res["timestamp"], unit="s").normalize()
    s = pd.Series(res["indicators"]["quote"][0]["close"], index=idx, dtype="float64")
    return s[~s.index.duplicated(keep="last")].dropna()


def yahoo_range(symbol: str, rng: str, interval: str) -> pd.Series:
    """Kurse für einen Chart-Zeitraum (auch Intraday), Index in UTC. Bis zu 3 Versuche."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.get(url, params={"range": rng, "interval": interval}, headers=UA, timeout=30)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            idx = pd.to_datetime(res["timestamp"], unit="s", utc=True)
            s = pd.Series(res["indicators"]["quote"][0]["close"], index=idx, dtype="float64").dropna()
            return s[~s.index.duplicated(keep="last")].sort_index()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{symbol} {rng}/{interval}: {last_err}")


def stooq(symbol: str) -> pd.Series:
    """Fallback: Stooq CSV (z. B. xauusd, xagusd, eurusd, usdtry)."""
    r = requests.get("https://stooq.com/q/d/l/", params={"s": symbol, "i": "d"}, headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    s = pd.Series(df["Close"].values, index=pd.to_datetime(df["Date"]), dtype="float64")
    return s.dropna().iloc[-1300:]


def price(yahoo_sym: str, stooq_sym: str) -> pd.Series:
    for fn, sym in ((yahoo, yahoo_sym), (stooq, stooq_sym)):
        try:
            s = fn(sym)
            if len(s) > 250:
                print(f"  {sym}: {len(s)} Tage von {fn.__name__}")
                return s
        except Exception as e:  # noqa: BLE001
            print(f"  {fn.__name__}({sym}) fehlgeschlagen: {e}")
    raise RuntimeError(f"Keine Daten für {yahoo_sym}/{stooq_sym}")


def fred(series_id: str) -> pd.Series:
    """FRED CSV-Export, kein API-Key nötig."""
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id}, headers=UA, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    date_col = df.columns[0]  # 'observation_date' (neu) oder 'DATE' (alt)
    s = pd.to_numeric(df[series_id], errors="coerce")
    s.index = pd.to_datetime(df[date_col])
    return s.dropna()


def treasury_real(years: int = 3) -> pd.Series:
    """10J-Realrendite direkt vom US-Finanzministerium (identisch zu FRED DFII10)."""
    frames = []
    for y in range(datetime.now(timezone.utc).year - years + 1, datetime.now(timezone.utc).year + 1):
        r = requests.get(
            f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{y}/all",
            params={"type": "daily_treasury_real_yield_curve", "field_tdr_date_value": y, "page": "", "_format": "csv"},
            headers=UA, timeout=30,
        )
        r.raise_for_status()
        frames.append(pd.read_csv(io.StringIO(r.text)))
    df = pd.concat(frames)
    s = pd.to_numeric(df["10 YR"], errors="coerce")
    s.index = pd.to_datetime(df["Date"], format="%m/%d/%Y")
    return s.dropna().sort_index()


def real_yield() -> pd.Series:
    for fn in (treasury_real, lambda: fred("DFII10")):
        try:
            s = fn()
            if len(s) > 200:
                print(f"  Realzins: {len(s)} Tage")
                return s
        except Exception as e:  # noqa: BLE001
            print(f"  Realzins-Quelle fehlgeschlagen: {e}")
    raise RuntimeError("Keine Realzins-Daten (Treasury und FRED)")


# ---------- Indikatoren ----------
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def band(v: float, hi2: float, hi1: float, lo1: float, lo2: float) -> int:
    """+ = günstig (Kaufsignal), − = teuer (Verkaufsignal)."""
    if v >= hi2:
        return -2
    if v >= hi1:
        return -1
    if v <= lo2:
        return 2
    if v <= lo1:
        return 1
    return 0


def build_chart(rng: str, interval: str, tol: str) -> dict:
    """Goldpreis eines Zeitraums in USD, EUR und TRY (Umrechnung mit dem Wechselkurs zur selben Zeit)."""
    gold = yahoo_range("GC=F", rng, interval)
    tolerance = pd.Timedelta(tol)
    fx = yahoo_range("EURUSD=X", rng, interval)
    eur = gold / fx.reindex(gold.index, method="nearest", tolerance=tolerance)  # vor Dez. 2003 (max) ohne Kurs
    try:
        try_ = gold * yahoo_range("TRY=X", rng, interval).reindex(gold.index, method="nearest", tolerance=tolerance)  # ab Feb. 2005
    except Exception as e:  # noqa: BLE001
        print(f"    TRY-Kurs für {rng} fehlt: {e}")
        try_ = pd.Series(np.nan, index=gold.index)
    rnd = lambda s, d=2: [None if pd.isna(x) else round(float(x), d) for x in s]  # noqa: E731
    return {
        "interval": interval,
        "t": [int(ts.timestamp()) for ts in gold.index],
        "usd": rnd(gold), "eur": rnd(eur), "try": rnd(try_),
    }


def build_charts(prev: dict) -> dict:
    """Alle Zeiträume; schlägt einer fehl, bleibt der Stand des letzten Laufs erhalten."""
    charts = {}
    for key, rng, interval, tol in RANGES:
        try:
            charts[key] = build_chart(rng, interval, tol)
            print(f"  Chart {key}: {len(charts[key]['t'])} Punkte")
        except Exception as e:  # noqa: BLE001
            print(f"  Chart {key} fehlgeschlagen: {e}")
            if key in prev.get("charts", {}):
                charts[key] = prev["charts"][key]
    return charts


def analyse(gold: pd.Series, silver: pd.Series, real: pd.Series, eurusd: pd.Series, usdtry: pd.Series) -> dict:
    df = pd.DataFrame({"gold": gold, "silver": silver}).dropna()
    df["eur"] = df.gold / eurusd.reindex(df.index, method="ffill")
    df["try"] = df.gold * usdtry.reindex(df.index, method="ffill")
    df["sma50"] = df.gold.rolling(50).mean()
    df["sma200"] = df.gold.rolling(200).mean()
    for cur in ("eur", "try"):
        df[f"sma50_{cur}"] = df[cur].rolling(50).mean()
        df[f"sma200_{cur}"] = df[cur].rolling(200).mean()
    df["rsi"] = rsi(df.gold)
    df["gsr"] = df.gold / df.silver
    df["real"] = real.reindex(df.index, method="ffill")
    last = df.dropna(subset=["sma200"]).iloc[-1]

    dev50 = last.gold / last.sma50 - 1
    dev200 = last.gold / last.sma200 - 1
    c = CFG["ma"]
    s_ma = band(dev200, c["exp2"], c["exp1"], c["cheap1"], c["cheap2"])

    r = real.dropna()
    lb = CFG["rate_lookback_days"]
    rate_now = float(r.iloc[-1])
    rate_chg = float(r.iloc[-1] - r.iloc[-1 - lb]) if len(r) > lb else 0.0
    c = CFG["rate"]
    s_rate = band(rate_chg, c["up2"], c["up1"], c["down1"], c["down2"])

    c = CFG["gsr"]
    s_gsr = band(float(last.gsr), c["exp2"], c["exp1"], c["cheap1"], c["cheap2"])
    c = CFG["rsi"]
    s_rsi = band(float(last.rsi), c["exp2"], c["exp1"], c["cheap1"], c["cheap2"])

    score = s_ma + s_rate + s_gsr + s_rsi
    v = CFG["verdict"]
    if score >= v["strong"]:
        verdict = "STARK KAUFEN"
    elif score >= v["normal"]:
        verdict = "KAUFEN"
    elif score <= -v["strong"]:
        verdict = "STARK VERKAUFEN"
    elif score <= -v["normal"]:
        verdict = "VERKAUFEN"
    else:
        verdict = "HALTEN"

    indicators = [
        {
            "key": "ma", "name": "Abstand zum Durchschnitt", "score": s_ma,
            "value": round(dev200 * 100, 1), "unit": "% über 200T",
            "extra": f"{dev50 * 100:+.1f} % zum 50-Tage-Schnitt",
            "text": text_ma(dev200),
        },
        {
            "key": "rate", "name": "Zinsen (US-Realzins 10J)", "score": s_rate,
            "value": round(rate_now, 2), "unit": "%",
            "extra": f"{rate_chg:+.2f} Pp. in 3 Monaten",
            "text": text_rate(rate_chg),
        },
        {
            "key": "gsr", "name": "Gold/Silber-Ratio", "score": s_gsr,
            "value": round(float(last.gsr), 1), "unit": "",
            "extra": "gesunder Bereich 55–70",
            "text": text_gsr(float(last.gsr)),
        },
        {
            "key": "rsi", "name": "RSI 14", "score": s_rsi,
            "value": round(float(last.rsi), 1), "unit": "",
            "extra": "über 70 überhitzt, unter 30 Angst",
            "text": text_rsi(float(last.rsi)),
        },
    ]

    tail = df.iloc[-400:]
    series = {
        "dates": [d.strftime("%Y-%m-%d") for d in tail.index],
        **{out: [None if pd.isna(x) else round(float(x), 2) for x in tail[col]]
           for out, col in (("gold", "gold"), ("sma50", "sma50"), ("sma200", "sma200"),
                            ("gold_eur", "eur"), ("sma50_eur", "sma50_eur"), ("sma200_eur", "sma200_eur"),
                            ("gold_try", "try"), ("sma50_try", "sma50_try"), ("sma200_try", "sma200_try"),
                            ("rsi", "rsi"), ("gsr", "gsr"), ("real", "real"))},
    }
    prev_row = df.iloc[-2]
    return {
        "verdict": verdict, "score": int(score),
        "indicators": indicators, "series": series,
        "as_of": df.index[-1].strftime("%Y-%m-%d"),
        "gold_usd_oz": round(float(last.gold), 2),
        "fair_band_usd": [round(float(last.sma200), 0), round(float(last.sma50), 0)],
        "fair_band_eur": [round(float(last.sma200_eur), 0), round(float(last.sma50_eur), 0)],
        "fair_band_try": [round(float(last.sma200_try), 0), round(float(last.sma50_try), 0)],
        "change": {cur: round(float(df[col].iloc[-1] / prev_row[col] - 1) * 100, 2)
                   for cur, col in (("usd", "gold"), ("eur", "eur"), ("try", "try"))},
    }


def text_ma(d):
    if d >= 0.25: return "Weit über dem langfristigen Schnitt – teuer, Rücksetzer wahrscheinlich."
    if d >= 0.15: return "Deutlich über dem Schnitt – eher teuer."
    if d <= -0.08: return "Klar unter dem Schnitt – günstig."
    if d <= 0: return "Unter dem 200-Tage-Schnitt – eher günstig."
    return "Nahe am fairen Bereich."


def text_rate(c):
    if c >= 0.15: return "Realzins steigt – zinslose Anlagen wie Gold geraten unter Druck."
    if c <= -0.15: return "Realzins fällt – Gold wird attraktiver."
    return "Realzins seitwärts – kein Signal."


def text_gsr(g):
    if g >= 70: return "Gold teuer im Vergleich zu Silber."
    if g <= 55: return "Gold günstig im Vergleich zu Silber."
    return "Im historisch gesunden Bereich."


def text_rsi(r):
    if r >= 70: return "Markt überhitzt (Gier)."
    if r <= 30: return "Markt verängstigt (Angst)."
    return "Neutral."


# ---------- Benachrichtigung ----------
def notify(title: str, body: str, tags: str) -> None:
    topic = os.getenv("NTFY_TOPIC")
    if topic:
        server = os.getenv("NTFY_SERVER", "https://ntfy.sh")
        try:
            requests.post(f"{server}/{topic}", data=body.encode(),
                          headers={"Title": title.encode(), "Tags": tags, "Priority": "high"}, timeout=20)
            print("  ntfy gesendet")
        except Exception as e:  # noqa: BLE001
            print(f"  ntfy-Fehler: {e}")
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": f"{title}\n\n{body}"}, timeout=20)
            print("  Telegram gesendet")
        except Exception as e:  # noqa: BLE001
            print(f"  Telegram-Fehler: {e}")


def main() -> int:
    prev = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    notify_ok = "--notify" in sys.argv  # Push-Nachrichten nur im Tagesabschluss-Lauf, sonst würde das Signal tagsüber flackern

    print("Lade Daten …")
    gold = price("GC=F", "xauusd")
    silver = price("SI=F", "xagusd")
    eurusd = price("EURUSD=X", "eurusd")
    usdtry = price("TRY=X", "usdtry")
    real = real_yield()

    res = analyse(gold, silver, real, eurusd, usdtry)
    g = res["gold_usd_oz"]
    eur, tl = float(eurusd.iloc[-1]), float(usdtry.iloc[-1])
    res["prices"] = {
        "usd_oz": g,
        "usd_g": round(g / GRAM_PER_OZ, 2),
        "eur_oz": round(g / eur, 2),
        "eur_g": round(g / eur / GRAM_PER_OZ, 2),
        "try_oz": round(g * tl, 2),
        "try_g": round(g * tl / GRAM_PER_OZ, 2),
        "eurusd": round(eur, 4),
        "usdtry": round(tl, 4),
    }

    # Echter Spotpreis (goldprice.dev) zusätzlich zum Future GC=F oben – siehe scripts/spot.py.
    # Ohne GOLDPRICE_API_KEY oder bei ausgeschöpftem Monatsbudget bleibt der zuletzt bekannte Wert stehen.
    spot_price, spot_usage = spotmod.get_spot(prev, os.getenv("GOLDPRICE_API_KEY"))
    res["spot_usage"] = spot_usage
    if spot_price:
        s = spot_price["usd_oz"]
        res["spot"] = {
            **spot_price,
            "eur_oz": round(s / eur, 2), "eur_g": round(s / eur / GRAM_PER_OZ, 2),
            "usd_g": round(s / GRAM_PER_OZ, 2),
            "try_oz": round(s * tl, 2), "try_g": round(s * tl / GRAM_PER_OZ, 2),
        }
    res["updated"] = datetime.now(timezone.utc).isoformat(timespec="minutes")

    print("Lade Preisverläufe …")
    res["charts"] = build_charts(prev)

    print("Lade Nachrichten …")
    try:
        res["news"] = news.collect()
        if res["news"]["stats"]["fetched"] == 0 and prev.get("news"):  # alle Quellen down: letzten Stand behalten
            res["news"] = {**prev["news"], "sources": res["news"]["sources"], "stale": True}
    except Exception as e:  # noqa: BLE001
        print(f"  News-Analyse fehlgeschlagen: {e}")
        if prev.get("news"):
            res["news"] = {**prev["news"], "stale": True}

    history = prev.get("history", [])
    history = [h for h in history if h["date"] != res["as_of"]]
    history.append({"date": res["as_of"], "verdict": res["verdict"], "score": res["score"]})
    res["history"] = history[-180:]

    last_notified = prev.get("notified_verdict", prev.get("verdict"))  # Signal, das zuletzt gemeldet wurde
    changed = last_notified != res["verdict"]
    force = os.getenv("NOTIFY_DAILY", "false").lower() == "true"
    res["notified_verdict"] = res["verdict"] if notify_ok else last_notified
    if notify_ok and (changed or force):  # erster Lauf meldet sich auch (Test, dass Push funktioniert)
        lines = [f"{i['name']}: {i['value']}{(' ' + i['unit']) if i['unit'] else ''} ({i['score']:+d})"
                 for i in res["indicators"]]
        nw = res.get("news")
        news_line = f"\n\nNews: {nw['label']} – {nw['summary']}" if nw else ""
        body = (f"Gold {res['prices']['eur_g']:.2f} €/g · {g:,.0f} $/oz · {res['prices']['try_g']:,.0f} ₺/g\n"
                f"Score {res['score']:+d} (vorher: {last_notified or '–'})\n\n" + "\n".join(lines)
                + news_line + "\n\nKeine Anlageberatung.")
        tag = {"KAUFEN": "green_circle", "STARK KAUFEN": "green_circle",
               "VERKAUFEN": "red_circle", "STARK VERKAUFEN": "red_circle"}.get(res["verdict"], "yellow_circle")
        notify(f"Gold-Radar: {res['verdict']}", body, tag)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Fertig: {res['verdict']} (Score {res['score']:+d}), Stand {res['as_of']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
