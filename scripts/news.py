"""
Gold-Radar – Nachrichten-Analyse.

Holt RSS-Feeds (finanzen.net News/Analysen + Ersatzquellen), sucht Meldungen mit
möglichem Einfluss auf den Goldpreis und ordnet sie mit einem festen Regelwerk ein
(Schlagwörter im Titel -> Richtung + Begründung). Keine KI, kein API-Key: dieselbe
Meldung ergibt immer dieselbe Einordnung. Regeln unten in RULES anpassbar.
"""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

UA = {
    "User-Agent": "gold-radar/1.0 (+https://github.com/engincelenk/gold-radar; RSS-Leser)",
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
}
MAX_AGE = timedelta(hours=48)
MAX_ITEMS = 14


def _google(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query + ' when:2d')}&hl=de&gl=DE&ceid=DE:de"


# finanzen.net zuerst (gewünscht). Die Seite blockt Skripte teils per Bot-Schutz (HTTP 403);
# dann greifen die Ersatzquellen, und der Status steht im Dashboard.
SOURCES = [
    {"name": "finanzen.net News", "group": "news", "url": "https://www.finanzen.net/rss/news"},
    {"name": "finanzen.net Analysen", "group": "analysen", "url": "https://www.finanzen.net/rss/analysen"},
    {"name": "Google News: Goldpreis", "group": "news", "url": _google("Goldpreis")},
    {"name": "Google News: Zinsen, Dollar, Inflation", "group": "news",
     "url": _google("Fed Zinsen Dollar Inflation Anleiherenditen")},
    {"name": "Google News: Gold-Prognosen", "group": "analysen", "url": _google("Gold Prognose Analysten")},
    {"name": "tagesschau Wirtschaft", "group": "news", "url": "https://www.tagesschau.de/wirtschaft/index~rss2.xml"},
    {"name": "tagesschau Ausland", "group": "news", "url": "https://www.tagesschau.de/ausland/index~rss2.xml"},
    {"name": "Handelsblatt Finanzen", "group": "news", "url": "https://www.handelsblatt.com/contentexport/feed/finanzen"},
]

# ---------- Regelwerk ----------
UP = r"steigt|steigen|stieg|klettert|klettern|legt zu|legen zu|zieht an|ziehen an|springt|springen|schießt|schießen"
DOWN = r"fällt|fallen|fiel|sinkt|sinken|sank|gibt nach|geben nach|rutscht|rutschen|verliert|verlieren|sackt|sacken|schwächelt"
_NUM = r"(?<!milliarden )(?<!millionen )(?<!mrd\. )(?<!mio\. )"

# dir: +1 = stützt den Goldpreis, -1 = belastet ihn, 0 = beobachten. w = Gewicht.
# scored=False: wird angezeigt, geht aber nicht in die Gesamtwertung ein (Kursmeldungen, Termine).
# beats: verdrängt die genannten Regeln, wenn beide zutreffen ("Zinssenkung vom Tisch" schlägt "Zinssenkung").
# also: zusätzlich muss dieses Muster im Titel vorkommen (Kontext).
RULES = [
    dict(id="zins_senk", topic="Zinsen & Notenbanken", dir=+1, w=2,
         pat=r"zinssenk\w*|zinswende|rate cut|zinsschritt nach unten|dovish|taubenhaft|geldpolitische lockerung|lockerung der geldpolitik"
             r"|senk\w*\s+(?:den\s+|die\s+|ihren\s+|seinen\s+)?(?:leit|schlüssel)?zins\w*"
             r"|zinsen?.{0,15}\b(?:sinken|fallen|runter)\b",
         why="Sinkende Zinsen senken die Kosten, zinsloses Gold zu halten – Rückenwind."),
    dict(id="zins_hawk", topic="Zinsen & Notenbanken", dir=-1, w=2, beats=["zins_senk"],
         pat=r"zinserhöh\w*|(?:erhöh\w*|hebt|heben|hob)\s+(?:den\s+|die\s+|ihren\s+|seinen\s+)?(?:leit|schlüssel)?zins\w*"
             r"|zinsen?.{0,15}\b(?:steigen|klettern|ziehen an)\b|höhere[nr]? zinsen|hawkish|falkenhaft|higher for longer"
             r"|keine zinssenk\w*|zinssenk\w*.{0,30}(?:vom tisch|unwahrscheinlich|verschoben|in weite ferne)"
             r"|straffere? geldpolitik|zinsangst|zinssorgen",
         why="Höhere oder länger hohe Zinsen machen zinslose Anlagen wie Gold weniger attraktiv – Gegenwind."),
    dict(id="rendite_up", topic="Anleiherenditen", dir=-1, w=1,
         pat=rf"rendite\w*.{{0,60}}?\b(?:{UP})\b", also=r"anleihe\w*|treasur\w*|bund\w*|staatspapier\w*",
         why="Steigende Anleiherenditen erhöhen die Konkurrenz zu Gold, das keine Zinsen zahlt."),
    dict(id="rendite_down", topic="Anleiherenditen", dir=+1, w=1,
         pat=rf"rendite\w*.{{0,60}}?\b(?:{DOWN})\b", also=r"anleihe\w*|treasur\w*|bund\w*|staatspapier\w*",
         why="Fallende Anleiherenditen verringern die Konkurrenz zu Gold."),
    dict(id="dollar_up", topic="US-Dollar", dir=-1, w=1,
         pat=rf"{_NUM}\bdollar(?:-index|kurs|-kurs)?\b.{{0,40}}?\b(?:{UP})\b|dollarstärke|starke[rn]? dollar|dollar-?erholung",
         why="Ein stärkerer Dollar verteuert Gold für Käufer außerhalb der USA – Gegenwind."),
    dict(id="dollar_down", topic="US-Dollar", dir=+1, w=1, beats=["dollar_up"],
         pat=rf"{_NUM}\bdollar(?:-index|kurs|-kurs)?\b.{{0,40}}?\b(?:{DOWN})\b|dollarschwäche|schwache[rn]? dollar",
         why="Ein schwächerer Dollar macht Gold für Käufer außerhalb der USA günstiger – Rückenwind."),
    dict(id="euro_up", topic="US-Dollar", dir=+1, w=1,
         pat=rf"{_NUM}\beuro\b.{{0,40}}?\b(?:{UP})\b", also=r"dollar|devisen|wechselkurs|eur/usd|eurusd",
         why="Steigt der Euro zum Dollar, gibt der Dollar nach – meist Rückenwind für Gold."),
    dict(id="euro_down", topic="US-Dollar", dir=-1, w=1,
         pat=rf"{_NUM}\beuro\b.{{0,40}}?\b(?:{DOWN})\b", also=r"dollar|devisen|wechselkurs|eur/usd|eurusd",
         why="Fällt der Euro zum Dollar, legt der Dollar zu – meist Gegenwind für Gold."),
    dict(id="infl_up", topic="Inflation", dir=+1, w=1,
         pat=rf"inflation\w*.{{0,50}}?\b(?:{UP}|beschleunigt sich|beschleunigen sich|höher als erwartet)\b"
             r"|inflationssorgen|inflationsangst|inflationsdruck|preise ziehen an",
         why="Steigende Inflation macht Gold als Wertspeicher gefragter (höhere Zinserwartungen können aber bremsen)."),
    dict(id="infl_down", topic="Inflation", dir=0, w=0, scored=False,
         pat=rf"inflation\w*.{{0,50}}?\b(?:{DOWN}|lässt nach|geht zurück|gehen zurück|kühlt ab|schwächt sich ab|verlangsamt sich)\b|disinflation",
         why="Nachlassende Inflation stützt Zinssenkungshoffnungen (positiv), schwächt aber das Inflationsschutz-Argument (negativ) – Wirkung gemischt."),
    dict(id="geo_esc", topic="Geopolitik", dir=+1, w=1,
         pat=r"eskalation|eskaliert|eskalier\w+|angriff\w*|drohnenangriff\w*|drohnenattacke\w*|raketen\w*|beschuss|beschossen|kriegsangst"
             r"|\bkrieg\b|kriegs\w+|militärschlag|militäroperation|invasion|konflikt\w*|spannungen|nahost|gaza|\biran\b|taiwan|nordkorea"
             r"|zolldrohung\w*|handelskrieg|zollstreit|neue zölle|sanktionen",
         why="Geopolitische Spannungen treiben die Nachfrage nach Gold als sicherem Hafen."),
    dict(id="geo_deesc", topic="Geopolitik", dir=-1, w=1, beats=["geo_esc"],
         pat=r"waffenstillstand|waffenruhe|\bfrieden\w*|deeskalation|entspannung|zolleinigung|einigung im (?:zoll|handels)streit|handelsabkommen|handelsdeal|kriegsende",
         why="Entspannung mindert die Nachfrage nach dem sicheren Hafen Gold."),
    dict(id="konj_schwach", topic="Konjunktur", dir=+1, w=1,
         pat=r"rezession\w*|konjunktursorgen|konjunkturangst|konjunkturflaute|wirtschaftsabschwung|konjunktureinbruch|abschwung|stagflation"
             r"|arbeitsmarkt.{0,30}(?:schwächelt|schwächer|trübt sich ein)|schwache[rn]? (?:arbeitsmarkt|jobzahlen|konjunkturdaten|us-daten)"
             r"|arbeitslosigkeit.{0,20}(?:steigt|steigen|klettert)|konjunkturdaten.{0,25}enttäusch\w+",
         why="Konjunktursorgen wecken Zinssenkungshoffnung und die Flucht in sichere Anlagen – Rückenwind."),
    dict(id="konj_stark", topic="Konjunktur", dir=-1, w=1, beats=["konj_schwach"],
         pat=r"starke[rn]? (?:arbeitsmarkt\w*|jobzahlen|konjunkturdaten|us-daten|us-wirtschaft)|robuste[rn]? (?:us-)?(?:arbeitsmarkt|wirtschaft|konjunktur)"
             r"|arbeitsmarkt.{0,30}(?:überrascht positiv|robust|übertrifft)|(?:übertrifft|übertreffen|übertroffen).{0,30}erwartungen.{0,40}(?:arbeitsmarkt|konjunktur|wirtschaft)",
         why="Starke Wirtschaftsdaten dämpfen Zinssenkungshoffnungen – Gegenwind."),
    dict(id="stress", topic="Finanzmarkt-Stress", dir=+1, w=1,
         pat=r"börsenbeben|börsencrash|kurscrash|marktcrash|crash\b.{0,30}(?:börse|märkte|aktien|dax|wall street)|ausverkauf an den|marktturbulenzen|turbulenzen an den"
             r"|(?:dax|dow jones|s&p 500|nasdaq|wall street|aktienmärkte|börsen)\b.{0,30}\b(?:bricht ein|brechen ein|sackt ab|sacken ab|stürzt ab|stürzen ab|rutscht ab|rutschen ab)\b"
             r"|bankenkrise|bankenpleite|risikoaversion|flucht in sichere häfen|sicherer hafen|safe haven|angst an den (?:märkten|börsen)",
         why="Marktstress und Risikoscheu lenken Anleger in sichere Häfen wie Gold."),
    dict(id="schulden", topic="Staatsschulden & Politik", dir=+1, w=1,
         pat=r"haushaltsstreit|shutdown|schuldenobergrenze|schuldenkrise|staatsschuldenkrise|rekordschulden|verschuldung.{0,20}rekord"
             r"|herabstufung.{0,40}(?:bonität|rating|kreditwürdigkeit)|(?:bonität|rating|kreditwürdigkeit).{0,40}herabgestuft"
             r"|fed.{0,30}unabhängigkeit|(?:trump|weißes haus).{0,50}(?:fed\b|powell|notenbank).{0,50}(?:kritik|kritisiert|angriff|entlass\w+|feuern|ersetz\w+|druck)"
             r"|(?:trump|weißes haus).{0,20}(?:kritisiert|attackiert|greift|drängt|drängen).{0,30}(?:fed\b|powell|notenbank)"
             r"|de-?dollarisierung",
         why="Zweifel an Staatsfinanzen oder Notenbank-Unabhängigkeit stärken Gold als Wertspeicher."),
    dict(id="nachfrage_plus", topic="Goldnachfrage", dir=+1, w=2,
         pat=r"(?:zentralbank\w*|notenbank\w*|china|indien|polen|türkei|russland).{0,60}\b(?:kauf\w*|stock\w+|erwirb\w+|aufgestockt|erwarb|zukäufe)\b.{0,40}\bgold\w*"
             r"|goldkäufe|gold-?etf\w*.{0,50}(?:zuflüsse|zufluss|aufgestockt|nachfrage|rekord)"
             r"|(?:zentralbank\w*|notenbank\w*).{0,40}goldreserven.{0,30}(?:aufgestockt|erhöht|ausgebaut|steigen)",
         why="Käufe von Zentralbanken und ETFs erhöhen die physische Nachfrage nach Gold."),
    dict(id="nachfrage_minus", topic="Goldnachfrage", dir=-1, w=2, beats=["nachfrage_plus"],
         pat=r"(?:zentralbank\w*|notenbank\w*|china|indien|russland|türkei).{0,60}\b(?:verkauf\w*|verkauft|verkaufen|reduzier\w+|abgestoßen)\b.{0,40}\bgold\w*"
             r"|gold-?etf\w*.{0,50}(?:abflüsse|abfluss|abgebaut|verkäufe)|goldreserven.{0,30}(?:verkauft|reduziert|abgebaut)",
         why="Verkäufe von Zentralbanken oder ETFs verringern die Nachfrage nach Gold."),
    dict(id="gold_up", topic="Goldpreis aktuell", dir=+1, w=1, scored=False,
         pat=rf"\bgold(?!man)\w*.{{0,60}}?\b(?:{UP}|rekord\w*|erholt sich|erholen sich)\b|rekordhoch.{{0,30}}gold|gold.{{0,30}}rekordhoch",
         why="Kursbewegung, die bereits passiert ist – zeigt die Stimmung im Markt, ist aber kein neuer Auslöser."),
    dict(id="gold_down", topic="Goldpreis aktuell", dir=-1, w=1, scored=False,
         pat=rf"\bgold(?!man)\w*.{{0,60}}?\b(?:{DOWN}|gewinnmitnahmen|unter druck)\b",
         why="Kursbewegung, die bereits passiert ist – zeigt die Stimmung im Markt, ist aber kein neuer Auslöser."),
    dict(id="termin", topic="Termine & Daten", dir=0, w=0, scored=False,
         pat=r"fed-?sitzung|fomc|zinsentscheid\w*|notenbanksitzung|ezb-?(?:ratssitzung|sitzung)|arbeitsmarktbericht|non-?farm|us-arbeitsmarktdaten"
             r"|verbraucherpreise|inflationsdaten|us-inflation|pce-?(?:preis|daten|index)|jackson hole"
             r"|(?:powell|lagarde).{0,30}(?:rede|auftritt|äußert|sagt)",
         why="Wichtiger Termin oder Datenpunkt: kann Zinserwartungen und damit den Goldpreis bewegen."),
    dict(id="gold_generic", topic="Gold direkt", dir=0, w=0, scored=False,
         pat=r"\bgold(?!man)\w*|\bsilber(?:preis|unze)\w*|edelmetall\w*",
         why="Direkte Meldung zu Gold oder Edelmetallen."),
]

# Meldungen, die mit dem Goldpreis nichts zu tun haben, werden gar nicht erst bewertet.
EXCLUDE = re.compile(
    r"bundesliga|fußball|champions league|europa league|formel 1|tennis|olympi|handball|\bnfl\b|\bnba\b|horoskop|lotto"
    r"|bauzins\w*|baufinanz\w*|tagesgeld|festgeld|immobilienkredit\w*"
)

for _r in RULES:
    _r["re"] = re.compile(_r["pat"])
    _r["also_re"] = re.compile(_r["also"]) if _r.get("also") else None
    _r.setdefault("scored", True)
    _r.setdefault("beats", [])

LABELS = [  # (Mindestscore, Text, Ton)
    (4, "Klar positiv", "up"),
    (1, "Leicht positiv", "up"),
    (0, "Neutral / gemischt", "flat"),
    (-3, "Leicht negativ", "down"),
    (-99, "Klar negativ", "down"),
]


# ---------- Einordnung ----------
def classify(title: str) -> list[dict]:
    """Regeln, die auf den Titel zutreffen (nach Anwendung von 'beats')."""
    text = title.lower()
    if EXCLUDE.search(text):
        return []
    hits = [r for r in RULES if r["re"].search(text) and (not r["also_re"] or r["also_re"].search(text))]
    beaten = {b for r in hits for b in r["beats"]}
    hits = [r for r in hits if r["id"] not in beaten]
    # Die allgemeine Gold-Regel nur, wenn nichts Genaueres passt.
    if len(hits) > 1:
        hits = [r for r in hits if r["id"] != "gold_generic"]
    return hits


def rate_item(hits: list[dict]) -> tuple[int, str, list[str]]:
    impact = sum(r["dir"] * r["w"] for r in hits)
    impact = max(-2, min(2, impact))
    top = sorted(hits, key=lambda r: (-abs(r["dir"] * r["w"]), not r["scored"]))
    why = " ".join(dict.fromkeys(r["why"] for r in top[:2]))
    topics = list(dict.fromkeys(r["topic"] for r in top))
    return impact, why, topics


def aggregate(items: list[dict]) -> dict:
    per: dict[str, dict] = {}
    for it in items:
        for r in it["_hits"]:
            if not r["scored"] or r["dir"] == 0:
                continue
            t = per.setdefault(r["topic"], {"sum": 0, "count": 0, "why": {}})
            t["sum"] += r["dir"] * r["w"]
            t["count"] += 1
            t["why"][r["dir"]] = r["why"]
    themes, score = [], 0
    for topic, t in per.items():
        effect = max(-3, min(3, t["sum"]))
        score += effect
        d = 1 if t["sum"] > 0 else -1 if t["sum"] < 0 else 0
        themes.append({"topic": topic, "dir": d, "effect": effect, "count": t["count"],
                       "why": t["why"].get(d, next(iter(t["why"].values())))})
    themes.sort(key=lambda x: (-abs(x["effect"]), -x["count"]))
    label, tone = next((txt, tn) for lim, txt, tn in LABELS if score >= lim)

    def names(sign):
        return ", ".join(f"{t['topic']} ({t['count']})" for t in themes if t["dir"] == sign)

    parts = []
    if names(1):
        parts.append(f"Stützend für den Goldpreis: {names(1)}.")
    if names(-1):
        parts.append(f"Belastend: {names(-1)}.")
    if not parts:
        parts.append("Keine Meldungen mit erkennbarem Einfluss auf den Goldpreis gefunden.")
    return {"score": score, "label": label, "tone": tone, "summary": " ".join(parts), "themes": themes}


# ---------- Abruf ----------
def _clean(s: str | None) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(content: bytes, source: dict) -> list[dict]:
    root = ET.fromstring(content)
    out = []
    for it in root.iter("item"):
        title = _clean(it.findtext("title"))
        publisher = _clean(it.findtext("source"))
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3]
        if not title:
            continue
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "")
            pub = pub.astimezone(timezone.utc) if pub.tzinfo else pub.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pub = None
        out.append({
            "title": title,
            "link": (it.findtext("link") or "").strip(),
            "source": f"{source['name']} · {publisher}" if publisher else source["name"],
            "group": source["group"],
            "published": pub,
            "summary": "" if "news.google.com" in source["url"] else _clean(it.findtext("description"))[:300],
        })
    return out


def fetch(source: dict) -> tuple[list[dict], dict]:
    status = {"name": source["name"], "group": source["group"], "ok": False, "count": 0, "error": None}
    for attempt in range(2):
        try:
            r = requests.get(source["url"], headers=UA, timeout=20)
            if r.status_code != 200:
                status["error"] = f"HTTP {r.status_code}" + (" (Zugriff für Skripte gesperrt)" if r.status_code == 403 else "")
                break  # blockierte Anfrage nicht erneut versuchen
            items = parse_feed(r.content, source)
            status.update(ok=True, count=len(items), error=None)
            return items, status
        except Exception as e:  # noqa: BLE001
            status["error"] = f"{type(e).__name__}: {e}"[:160]
            time.sleep(2)
    return [], status


def collect(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    raw, sources = [], []
    for src in SOURCES:
        items, status = fetch(src)
        raw += items
        sources.append(status)
        print(f"  News {src['name']}: " + (f"{status['count']} Meldungen" if status["ok"] else f"FEHLER {status['error']}"))

    seen, items = set(), []
    for it in sorted(raw, key=lambda x: x["published"] or now, reverse=True):
        if it["published"] and now - it["published"] > MAX_AGE:
            continue
        key = re.sub(r"[^a-z0-9äöüß]", "", it["title"].lower())[:70]
        if key in seen:
            continue
        seen.add(key)
        hits = classify(it["title"])
        if not hits:
            continue
        impact, why, topics = rate_item(hits)
        items.append({**it, "_hits": hits, "impact": impact, "why": why, "topics": topics,
                      "scored": any(r["scored"] and r["dir"] for r in hits)})

    result = aggregate(items)
    items.sort(key=lambda x: (-abs(x["impact"]), not x["scored"], -(x["published"] or now).timestamp()))
    shown, per_topic = [], {}
    for i in items:  # höchstens 4 Meldungen je Hauptthema, damit die Liste nicht von einem Thema dominiert wird
        n = per_topic.get(i["topics"][0], 0)
        if n < 4:
            per_topic[i["topics"][0]] = n + 1
            shown.append(i)
    result["items"] = [{
        "title": i["title"], "link": i["link"], "source": i["source"], "group": i["group"],
        "published": i["published"].isoformat(timespec="minutes") if i["published"] else None,
        "impact": i["impact"], "topics": i["topics"], "why": i["why"], "scored": i["scored"],
    } for i in shown[:MAX_ITEMS]]
    result["stats"] = {"fetched": len(raw), "considered": len(seen), "relevant": len(items)}
    result["sources"] = sources
    result["updated"] = now.isoformat(timespec="minutes")
    return result
