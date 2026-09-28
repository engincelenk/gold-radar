# Gold-Radar

Statisches Dashboard (GitHub Pages, `docs/`) mit täglichem Update per GitHub Actions.
Repo: https://github.com/engincelenk/gold-radar · Live: https://www.gr.immofuchs.info · Branch: `main`

## Deploy (auf Zuruf: „deploy“, „bring das live“, „push das“)

Immer über das Skript, nie von Hand mit git:

```
python deploy.py -m "<kurze Beschreibung der Änderung>" --co-author "Claude Sonnet 5 <noreply@anthropic.com>"
```

- Committet alle Änderungen, holt Bot-Commits per Rebase, pusht, wartet auf Workflow und Pages-Build und prüft die Live-Seite.
- Die Ausgabe dem Nutzer kurz berichten (Commit, Workflow-Ergebnis, Live-Signal). Bei Fehlern die Meldung wörtlich wiedergeben.
- Nur Stand ansehen: `python deploy.py --status`. Vorschau: `python deploy.py --dry-run`.
- Nie force-pushen, keine Hooks umgehen. Bei Rebase-Konflikt (Exit-Code 2) oder fehlenden Rechten (Exit-Code 3) stoppen und den Nutzer fragen.
- Vor dem Deploy von Code-Änderungen die Tests laufen lassen: `python -m unittest discover -s tests`.

## Wichtig

- `docs/data.json` erzeugt der Workflow (`scripts/update.py`). Nicht von Hand ändern. Der aktuelle Stand liegt im Branch `data` (alle 15 Minuten überschrieben, die Seite lädt ihn von dort); nach `main` committet der Bot nur einen Tages-Snapshot (22 Uhr UTC) als Rückfall.
- Der Workflow startet: alle 15 Minuten (Cron), manuell (Actions → Run workflow) und bei Push auf `scripts/**`, `requirements.txt`, `daily.yml`. Push-Nachrichten (`--notify`) nur im 22-Uhr-Lauf.
- Vor `git pull`/`push` von Hand bedenken: `main` bekommt Bot-Commits; `deploy.py` rebased automatisch.
- Nachrichten-Regeln: `RULES` in `scripts/news.py`. Kauf-/Verkaufssignal: `CFG` in `scripts/update.py`.
- Lokal testen: Daten mit `python scripts/update.py` erzeugen (Abhängigkeiten aus `requirements.txt`), dann `docs/` mit `python -m http.server` ansehen. Das schreibt `docs/data.json`; diese Änderung nicht mitcommitten, wenn sie nur lokaler Test ist.
- Unter Windows kann der lokale DNS-Cache neue Domains verzögert kennen; `deploy.py` umgeht das bei der Live-Prüfung.
