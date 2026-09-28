---
description: Gold-Radar auf GitHub deployen (commit, push, Workflow und Live-Seite prüfen)
argument-hint: "[Commit-Nachricht]"
---
Deploye das Projekt mit `deploy.py` im Projektverzeichnis:

1. Bei Code-Änderungen zuerst `python -m unittest discover -s tests` ausführen. Bei Fehlern stoppen und berichten.
2. Dann `python deploy.py -m "$ARGUMENTS" --co-author "Claude Sonnet 5 <noreply@anthropic.com>"` ausführen. Ist `$ARGUMENTS` leer, ohne `-m` aufrufen (die Nachricht wird automatisch erzeugt).
3. Ergebnis kurz berichten: Commit, Workflow-Status, Pages-Build, Live-Signal. Fehlermeldungen wörtlich wiedergeben.

Nie force-pushen. Bei Rebase-Konflikt oder fehlenden Rechten stoppen und nachfragen.