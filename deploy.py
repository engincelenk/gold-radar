#!/usr/bin/env python3
"""
Gold-Radar – Deploy auf GitHub in einem Befehl (nur Standardbibliothek).

    python deploy.py                    # alles committen, pushen, Workflow + Live-Seite prüfen
    python deploy.py -m "Text"          # eigene Commit-Nachricht
    python deploy.py --no-wait          # nach dem Push beenden
    python deploy.py --status           # nur Stand anzeigen, nichts ändern
    python deploy.py --dry-run          # zeigen, was passieren würde

Ablauf: Änderungen stagen -> committen -> vom Server holen (rebase, damit Bot-Commits
mit neuen Daten kein Problem sind) -> pushen -> auf den GitHub-Workflow und den Pages-Build
warten -> prüfen, dass https://www.gr.immofuchs.info die neuen Daten ausliefert.
Nie force-push. Bei Konflikten oder Rechte-Fehlern wird abgebrochen und erklärt.
"""
from __future__ import annotations

import argparse
import fnmatch
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "engincelenk/gold-radar"
BRANCH = "main"
SITE = "www.gr.immofuchs.info"
PAGES_IP = "185.199.108.153"  # GitHub Pages, Fallback wenn der lokale DNS-Cache die neue Domain noch nicht kennt
WORKFLOW = "Gold-Radar Update"
PAGES_RUN = "pages build and deployment"
FALLBACK_IDENTITY = ("Gold-Radar Setup", "setup@users.noreply.github.com")
SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*secret*", "*token*", "*.pfx")
POLL = 10


class Abort(Exception):
    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


def say(msg: str = "") -> None:
    print(msg, flush=True)


# ---------- git ----------
def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},  # nie auf Eingaben warten
    )
    if check and p.returncode:
        raise Abort(f"git {' '.join(args)} fehlgeschlagen:\n{(p.stderr or p.stdout).strip()}")
    return p


def out(*args: str) -> str:
    return git(*args).stdout.strip()


def ensure_repo() -> None:
    try:
        top = out("rev-parse", "--show-toplevel")
    except (Abort, FileNotFoundError) as e:
        raise Abort(f"Kein Git-Repository in {ROOT} (oder git fehlt): {e}") from e
    if not os.path.samefile(top, ROOT):  # nie versehentlich ein übergeordnetes Repo (z. B. C:\) anfassen
        raise Abort(f"{ROOT} ist nicht die Wurzel eines eigenen Git-Repos (gefunden: {top}). Abbruch.")
    if "engincelenk/gold-radar" not in out("remote", "get-url", "origin"):
        raise Abort("Remote 'origin' zeigt nicht auf engincelenk/gold-radar. Abbruch.")
    branch = out("rev-parse", "--abbrev-ref", "HEAD")
    if branch != BRANCH:
        raise Abort(f"Aktueller Branch ist '{branch}', deployed wird nur '{BRANCH}'. Bitte zuerst wechseln.")


# ---------- GitHub / Web ----------
def api_runs() -> list[dict]:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/runs?per_page=20",
        headers={"User-Agent": "gold-radar-deploy", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["workflow_runs"]


def find_run(name: str, sha: str) -> dict | None:
    try:
        return next((r for r in api_runs() if r["name"] == name and r["head_sha"] == sha), None)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        say(f"  (GitHub-API nicht erreichbar: {e})")
        return None


class _IPConn(http.client.HTTPSConnection):
    """HTTPS direkt zur GitHub-Pages-IP, aber mit dem richtigen Hostnamen (SNI + Zertifikatsprüfung)."""

    def connect(self):
        sock = socket.create_connection((PAGES_IP, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def site_get(path: str) -> tuple[int, bytes]:
    sep = "&" if "?" in path else "?"
    path = f"{path}{sep}x={int(time.time())}"
    headers = {"User-Agent": "gold-radar-deploy", "Cache-Control": "no-cache"}
    try:
        req = urllib.request.Request(f"https://{SITE}{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError):  # z. B. DNS-Cache des Routers kennt die Domain noch nicht
        conn = _IPConn(SITE, timeout=20, context=ssl.create_default_context())
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        return r.status, r.read()


def live_report() -> bool:
    try:
        code, body = site_get("/data.json")
        if code != 200:
            say(f"  Live-Seite: data.json liefert HTTP {code}")
            return False
        d = json.loads(body.decode("utf-8"))
        n = d.get("news") or {}
        say(f"  Live-Seite https://{SITE}: OK")
        say(f"    Signal: {d['verdict']} (Score {d['score']:+d}), Stand {d['as_of']}, aktualisiert {d.get('updated')}")
        say(f"    Charts: {', '.join(d.get('charts', {})) or 'keine'} | News: {n.get('label', 'keine')}")
        return True
    except Exception as e:  # noqa: BLE001
        say(f"  Live-Seite nicht prüfbar: {e}")
        return False


def wait_run(name: str, sha: str, appear: int, limit: int) -> dict | None:
    """Wartet, bis ein Lauf für den Commit erscheint (appear Sekunden) und fertig ist (limit Sekunden)."""
    start, run = time.time(), None
    while time.time() - start < appear and not run:
        run = find_run(name, sha)
        if not run:
            time.sleep(POLL)
    if not run:
        return None
    say(f"  {name}: {run['html_url']}")
    while run["status"] != "completed" and time.time() - start < limit:
        time.sleep(POLL)
        run = find_run(name, sha) or run
        say(f"    Status: {run['status']}")
    return run


# ---------- Kommandos ----------
def status() -> int:
    git("fetch", "origin", check=False)
    say(f"Branch {BRANCH}, Repo {REPO}")
    changes = out("status", "--porcelain")
    say("Lokale Änderungen:\n" + (changes or "  keine"))
    ahead = out("rev-list", "--count", f"origin/{BRANCH}..HEAD")
    behind = out("rev-list", "--count", f"HEAD..origin/{BRANCH}")
    say(f"Vor origin: {ahead} Commit(s), hinter origin: {behind} Commit(s)")
    say("Letzte Commits:\n" + out("log", "--oneline", "-5"))
    try:
        for r in [x for x in api_runs() if x["name"] in (WORKFLOW, PAGES_RUN)][:4]:
            say(f"  {r['name']}: {r['status']} {r['conclusion'] or ''} ({r['created_at']})")
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        say(f"  (GitHub-API nicht erreichbar: {e})")
    live_report()
    return 0


def auto_message(files: list[str]) -> str:
    names = ", ".join(files[:5]) + (f" und {len(files) - 5} weitere" if len(files) > 5 else "")
    return f"Update {date.today().isoformat()}: {names}"


def commit(message: str | None, co_authors: list[str]) -> bool:
    if not out("status", "--porcelain"):
        return False
    git("add", "-A")
    staged = out("diff", "--cached", "--name-only").splitlines()
    risky = [f for f in staged if any(fnmatch.fnmatch(Path(f).name.lower(), p) for p in SECRET_PATTERNS)]
    if risky:
        git("reset", check=False)
        raise Abort("Diese Dateien sehen nach Zugangsdaten aus und werden NICHT deployed:\n  " + "\n  ".join(risky)
                    + "\nEntfernen oder in .gitignore aufnehmen und erneut starten.")
    say(f"Committe {len(staged)} Datei(en):\n  " + "\n  ".join(staged))
    msg = message or auto_message(staged)
    if co_authors:
        msg += "\n\n" + "\n".join(f"Co-Authored-By: {c}" for c in co_authors)
    ident = []
    if not git("config", "user.name", check=False).stdout.strip() or not git("config", "user.email", check=False).stdout.strip():
        ident = ["-c", f"user.name={FALLBACK_IDENTITY[0]}", "-c", f"user.email={FALLBACK_IDENTITY[1]}"]
    git(*ident, "commit", "-m", msg)
    return True


def sync() -> None:
    p = git("pull", "--rebase", "--autostash", "origin", BRANCH, check=False)
    if p.returncode:
        git("rebase", "--abort", check=False)
        raise Abort("Zusammenführen mit dem Server ist gescheitert (Konflikt?). Rebase wurde abgebrochen, "
                    "nichts wurde gepusht.\n" + (p.stderr or p.stdout).strip(), 2)


def push() -> None:
    p = git("push", "origin", BRANCH, check=False)
    if p.returncode:
        err = (p.stderr or p.stdout).strip()
        hint = ""
        if "403" in err or "denied" in err.lower() or "Authentication" in err:
            hint = ("\n\nKeine Schreibrechte: Der gespeicherte GitHub-Login hat keinen Zugriff. Neuen Token (Scopes repo + workflow) "
                    "anlegen, alten Eintrag löschen und einmal in einer Konsole pushen:\n"
                    '  "protocol=https`nhost=github.com`n" | git credential-manager erase   (PowerShell)\n'
                    "  git push origin main")
        raise Abort(f"Push fehlgeschlagen:\n{err}{hint}", 3)


def deploy(args: argparse.Namespace) -> int:
    git("fetch", "origin", check=False)
    changes = out("status", "--porcelain")
    ahead = int(out("rev-list", "--count", f"origin/{BRANCH}..HEAD"))
    if args.dry_run:
        say("Trockenlauf – es würde passieren:")
        say("  Lokale Änderungen:\n" + ("    " + changes.replace("\n", "\n    ") if changes else "    keine"))
        say(f"  Ungepushte Commits: {ahead}")
        return 0

    committed = commit(args.message, args.co_author)
    sync()
    ahead = int(out("rev-list", "--count", f"origin/{BRANCH}..HEAD"))
    if ahead == 0:
        say("Nichts zu deployen: lokal und GitHub sind identisch.")
        live_report()
        return 0
    say(f"Pushe {ahead} Commit(s) nach origin/{BRANCH} …")
    push()
    sha = out("rev-parse", "HEAD")
    say(f"Gepusht: {sha[:7]} {out('log', '-1', '--format=%s')}" + ("" if committed else "  (nur bereits vorhandene Commits)"))
    if args.no_wait:
        return 0

    final = sha
    say("Warte auf den Workflow …")
    run = wait_run(WORKFLOW, sha, appear=45, limit=360)
    if run is None:
        say("  Kein Workflow-Lauf nötig (keine Änderung an scripts/, requirements.txt oder daily.yml).")
    elif run["status"] != "completed":
        say("  Workflow läuft noch – ich warte nicht länger. Später prüfen mit: python deploy.py --status")
    elif run["conclusion"] != "success":
        say(f"  WORKFLOW FEHLGESCHLAGEN ({run['conclusion']}). Log: {run['html_url']}")
        return 4
    else:
        say("  Workflow erfolgreich. Hole die vom Bot erzeugten Daten …")
        for _ in range(6):
            git("fetch", "origin", check=False)
            if out("rev-parse", f"origin/{BRANCH}") != sha:
                break
            time.sleep(5)
        git("pull", "--ff-only", "origin", BRANCH, check=False)
        final = out("rev-parse", "HEAD")

    say("Warte auf GitHub Pages …")
    pages = wait_run(PAGES_RUN, final, appear=90, limit=240)
    if pages is None:
        say("  Kein Pages-Build gefunden (evtl. verzögert).")
    elif pages["status"] == "completed" and pages["conclusion"] != "success":
        say(f"  PAGES-BUILD FEHLGESCHLAGEN ({pages['conclusion']}): {pages['html_url']}")
        return 5
    else:
        say(f"  Pages: {pages['status']} {pages['conclusion'] or ''}")
    ok = live_report()
    say("\nDeploy abgeschlossen." if ok else "\nDeploy gepusht, Live-Prüfung aber ohne Erfolg – bitte später erneut mit --status prüfen.")
    return 0 if ok else 6


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Gold-Radar auf GitHub deployen.")
    ap.add_argument("-m", "--message", help="Commit-Nachricht (sonst automatisch aus den Dateinamen)")
    ap.add_argument("--co-author", action="append", default=[], metavar='"Name <mail>"', help="Co-Authored-By-Zeile (mehrfach möglich)")
    ap.add_argument("--no-wait", action="store_true", help="nach dem Push nicht auf Workflow/Pages warten")
    ap.add_argument("--status", action="store_true", help="nur Stand anzeigen")
    ap.add_argument("--dry-run", action="store_true", help="nur anzeigen, was passieren würde")
    args = ap.parse_args()
    try:
        ensure_repo()
        return status() if args.status else deploy(args)
    except Abort as e:
        say(f"\nABBRUCH: {e}")
        return e.code
    except KeyboardInterrupt:
        say("\nAbgebrochen.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
