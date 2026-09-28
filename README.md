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

Danach läuft alles automatisch Mo–Fr um 21:30 UTC. Eine Nachricht kommt nur, wenn sich das Signal ändert.
Für eine tägliche Zusammenfassung: *Settings → Secrets and variables → Actions → Variables* → `NOTIFY_DAILY` = `true`.

## Datenquellen (alle ohne API-Key)
- Gold `GC=F`, Silber `SI=F`, `EURUSD=X`, `TRY=X` – Yahoo Finance (Fallback: Stooq)
- US-Realzins 10 J (`DFII10`) – FRED, Federal Reserve Bank of St. Louis

## Hinweis
Keine Anlageberatung. Die Signale sind eine regelbasierte Einordnung, keine Prognose.
