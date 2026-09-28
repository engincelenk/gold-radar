# Gold-Radar Wecker (Cloudflare Worker)

GitHubs eigener Zeitplan ist Best Effort: Läufe starten verspätet oder gar nicht. Dieser Worker startet den
Workflow „Gold-Radar Update“ alle 15 Minuten auf die Minute genau per GitHub-API. Kosten: 0 € (Cloudflare Free-Tarif,
96 Aufrufe pro Tag bei 100.000 erlaubten).

Der Zeitplan in `.github/workflows/daily.yml` (`7,22,37,52 * * * *`) bleibt als Reserve. Er überspringt sich selbst, wenn der Wecker
funktioniert und die Daten jünger als 10 Minuten sind.

## Einrichtung (einmalig, ca. 5 Minuten)

### 1. GitHub-Token erstellen
GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token

- Name: `gold-radar-wecker`
- Expiration: z. B. 1 Jahr (danach neu erstellen und Schritt 4 wiederholen)
- Repository access: **Only select repositories** → `gold-radar`
- Repository permissions → **Actions: Read and write** (Metadata: Read-only kommt automatisch)
- Token kopieren. Er wird nur einmal angezeigt.

### 2. Worker bereitstellen
```powershell
cd C:\Projects\Gold-Radar\worker
npx wrangler login        # öffnet den Browser, bei Cloudflare anmelden und erlauben
npx wrangler deploy       # legt den Worker "gold-radar-wecker" mit Cron */15 an
```

### 3. Token als Secret hinterlegen
```powershell
npx wrangler secret put GITHUB_TOKEN     # Token einfügen und Enter; er erscheint nicht im Klartext
```

### Alternative ohne Konsole (Cloudflare-Dashboard)
Workers & Pages → Create → Create Worker → Name `gold-radar-wecker` → Deploy → Edit code → Inhalt von `index.js` einfügen → Deploy.
Danach Settings → Variables and Secrets → Add → Typ **Secret**, Name `GITHUB_TOKEN`, Wert = Token.
Settings → Trigger Events → Cron Triggers → Add → `*/15 * * * *`.

### 4. Prüfen
- Nach spätestens 15 Minuten erscheint unter GitHub → Actions ein Lauf „Gold-Radar Update“ mit „Manually run by …“ (source=cloudflare).
- Cloudflare → Workers → gold-radar-wecker → Logs zeigt „Workflow gestartet“. Bei Fehlern steht dort der Grund (401 = Token falsch/abgelaufen,
  403 = Recht „Actions: Read and write“ fehlt, 404 = Token hat keinen Zugriff auf das Repo).
- `python deploy.py --status` zeigt die Frische der Daten.

## Test der Logik ohne Netzwerk
```
node worker/test.mjs
```

## Sicherheit
Der Token darf nur Actions dieses einen Repos steuern. Er liegt ausschließlich als Cloudflare-Secret, nie im Repo. Der Worker hat keine
Web-Schnittstelle (Antwort 404) und keine öffentliche workers.dev-Adresse.
