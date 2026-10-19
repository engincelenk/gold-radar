# Gold-Radar

Tägliches Dashboard mit Kauf-/Verkaufssignal für Gold, basierend auf vier Werkzeugen:

| Werkzeug | Teuer (−) | Günstig (+) |
|---|---|---|
| Abstand zum 200-Tage-Schnitt | > +15 % / > +25 % | < 0 % / < −8 % |
| US-Realzins (Änderung 3 Monate) | steigt > 0,15 / 0,40 Pp. | fällt > 0,15 / 0,40 Pp. |
| Gold/Silber-Ratio | > 70 / > 85 | < 55 / < 45 |
| RSI 14 | > 70 / > 75 | < 30 / < 25 |

Summe ≥ +2 → **Kaufen**, ≥ +4 → **Stark kaufen**, ≤ −2 → **Verkaufen**, ≤ −4 → **Stark verkaufen**, sonst **Halten**.
Schwellen anpassen: `CFG` oben in `scripts/update.py`.

## Einrichtung (ca. 10 Minuten)

1. **Repo anlegen:** Neues GitHub-Repository erstellen (öffentlich, damit Pages kostenlos ist) und alle Dateien hochladen.
2. **Push-Nachrichten (ntfy, kostenlos):**
   - App „ntfy“ aus dem App Store installieren.
   - Einen schwer erratbaren Topic-Namen ausdenken, z. B. `goldradar-7f3k9q`, und in der App abonnieren.
   - Im Repo: *Settings → Secrets and variables → Actions → New repository secret* → Name `NTFY_TOPIC`, Wert = dein Topic.
   - Optional Telegram: Secrets `TELEGRAM_TOKEN` und `TELEGRAM_CHAT_ID`.
3. **Dashboard veröffentlichen:** *Settings → Pages → Source: Deploy from a branch → Branch `main`, Ordner `/docs`*.
4. **Erster Lauf:** *Actions → Gold-Radar Update → Run workflow*. Nach ca. 1 Minute kommt die erste Push-Nachricht, und das Dashboard ist unter `https://<dein-name>.github.io/<repo>/` erreichbar.

Danach läuft alles automatisch **alle 15 Minuten**. GitHubs eigener Zeitplan ist unzuverlässig (Läufe kommen verspätet oder gar nicht),
deshalb startet ein kleiner Cloudflare-Worker den Workflow auf die Minute genau (`worker/`, Einrichtung dort in der README).
Der GitHub-Zeitplan läuft als Reserve mit und überspringt sich, wenn die Daten frisch sind.
Eine Push-Nachricht kommt nur im Tagesabschluss-Lauf (22 Uhr UTC), und nur wenn sich das Signal seit der letzten Meldung geändert hat.
Für eine tägliche Zusammenfassung: *Settings → Secrets and variables → Actions → Variables* → `NOTIFY_DAILY` = `true`.

### Wie die Daten zur Seite kommen
- Jeder Lauf schreibt den aktuellen Stand als einzelnen Commit in den Branch **`data`** (wird überschrieben, die Historie wächst nicht).
  Die Seite lädt `https://raw.githubusercontent.com/engincelenk/gold-radar/data/data.json` und lädt sich alle 5 Minuten selbst neu.
- Einmal pro Tag (22 Uhr UTC) und bei jedem Code-Start committet der Bot zusätzlich `docs/data.json` nach `main`. Das ist der Rückfall der Seite, falls der `data`-Branch nicht erreichbar ist.
- Die Seite warnt, wenn die Daten älter als 3 Stunden sind. `python deploy.py --status` zeigt die Frische ebenfalls an.

## Spotpreis (optional, zusätzlich zum Future)
Der Future `GC=F` (Chart, Signale) weicht üblicherweise 0,2–1 % vom Spotpreis ab, den z. B. finanzen.net oder die
Deutsche Börse zeigen (Termin- vs. Kassamarkt). `scripts/spot.py` holt zusätzlich den echten Spotpreis von
[goldprice.dev](https://goldprice.dev) und zeigt ihn oben als Hauptzahl.

**Einrichtung (optional, ohne geht alles wie bisher mit dem Future als Hauptzahl):**
1. Kostenlosen Account auf https://goldprice.dev anlegen (keine Kreditkarte nötig) und einen API-Key erzeugen.
2. GitHub → Repo → *Settings → Secrets and variables → Actions → New repository secret* → Name `GOLDPRICE_API_KEY`, Wert = der Key.

Der Gratis-Plan erlaubt 1.000 Anrufe/Monat. `scripts/spot.py` (`should_fetch`) verteilt ein Budget von 950 gleichmäßig
über den Kalendermonat statt ein festes Intervall zu nutzen – das entspricht im Schnitt ca. 45–46 Minuten zwischen zwei
Abrufen bei einem Lauf alle 15 Minuten, holt nach einer Pause aber automatisch auf und überschreitet das Budget nie.
Getestet in `tests/test_spot.py`, u. a. mit einem simulierten 30-Tage-Monat im 15-Minuten-Takt.

## Rückblick: Lag das Signal richtig?
`scripts/backtest.py` rechnet die vier Werkzeuge mit denselben Schwellen (`CFG`) für jeden Handelstag der letzten ca. 9 Jahre nach
und prüft hinterher, ob das Signal gestimmt hat. Die Seite zeigt das im Abschnitt „Lag das Signal richtig?“:

- **Trefferquote** je Signal (Kaufen / Halten / Verkaufen) nach 1 und 3 Monaten, mit Vergleichswert (wie oft ein beliebiger Tag richtig gelegen hätte).
  Kaufen ist richtig, wenn der Kurs danach höher steht, Verkaufen, wenn er tiefer steht, Halten, wenn der Kurs ruhiger blieb als an einem typischen Tag.
- **Dem Signal gefolgt vs. Gold durchgehend gehalten** für 1, 3, 5 Jahre und Max, in Euro, Dollar und Lira. Start investiert, Kaufen = investiert,
  Verkaufen = Bargeld (ohne Zinsen), Halten = Position behalten. Ohne Gebühren, Spreads und Steuern.
- **Tagesband der letzten 12 Monate** mit Treffer-Markierung pro Tag.

Kein Blick in die Zukunft: Jedes Signal nutzt nur Kurse bis zu seinem Tag; `tests/test_backtest.py` prüft das und vergleicht die Rückrechnung
mit dem Live-Signal (`update.analyse`). Einmal täglich nach Handelsschluss (23:30 UTC) neu gerechnet, Ergebnis in `docs/backtest.json`
(liegt wie `data.json` im Branch `data`). Die Seite lädt die Datei getrennt, weil sie ~65 KB groß ist und sich nur täglich ändert.
Neu berechnen erzwingen: `python scripts/update.py --backtest`.

## Preisgrafik
Goldpreis (Future `GC=F`) für Heute, Woche, Monat, Jahr, 10 Jahre und Max, umschaltbar zwischen Euro, US-Dollar und Türkischer Lira.
Euro = Dollar-Kurs ÷ EUR/USD, Lira = Dollar-Kurs × USD/TRY, jeweils zum selben Zeitpunkt (vor Dezember 2003 gibt es keinen Euro-Kurs, vor Februar 2005 keinen Lira-Kurs).
Die Signale rechnen immer in Dollar; der Währungsschalter ändert nur die Anzeige. Yahoo-Kurse sind einige Minuten verzögert.

## Nachrichten-Analyse
`scripts/news.py` liest RSS-Feeds, sucht Meldungen mit möglichem Einfluss auf den Goldpreis und ordnet sie nach festen Regeln ein
(Zinsen, Anleiherenditen, Dollar, Inflation, Geopolitik, Konjunktur, Marktstress, Staatsschulden, Goldnachfrage). Pro Meldung gibt es Richtung
(Rückenwind/Gegenwind), Begründung und Thema; daraus entsteht eine Gesamteinschätzung. Sie fließt **nicht** in das Kauf-/Verkaufssignal ein.
Regeln anpassen: `RULES` in `scripts/news.py`, Tests: `python -m unittest discover -s tests`.

Quellen: `https://www.finanzen.net/rss/news` und `https://www.finanzen.net/rss/analysen` zuerst. finanzen.net sperrt Abrufe durch Skripte teilweise (HTTP 403).
Dann greifen Ersatzquellen (Google News, tagesschau, Handelsblatt); der Status jeder Quelle steht im Dashboard unter „Quellen und Status“.

## Datenquellen (alle ohne API-Key)
- Gold `GC=F`, Silber `SI=F`, `EURUSD=X`, `TRY=X` – Yahoo Finance (Fallback: Stooq)
- US-Realzins 10 J – US-Finanzministerium (identisch zu FRED `DFII10`), Fallback FRED
- Nachrichten – siehe oben

## Hinweis
Keine Anlageberatung. Die Signale sind eine regelbasierte Einordnung, keine Prognose.
