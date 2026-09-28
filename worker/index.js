/**
 * Gold-Radar Wecker (Cloudflare Worker mit Cron-Trigger).
 *
 * GitHubs eigener Zeitplan ist "Best Effort" und lässt Läufe verspätet oder gar nicht starten.
 * Dieser Worker startet den Workflow "Gold-Radar Update" stattdessen auf die Minute genau,
 * per GitHub-API (workflow_dispatch). Der Cron-Ausdruck steht in wrangler.toml bzw. im Cloudflare-Dashboard.
 *
 * Nötig: Secret GITHUB_TOKEN (Fine-grained Token, nur dieses Repo, Berechtigung "Actions: Read and write").
 */
const REPO = "engincelenk/gold-radar";
const WORKFLOW = "daily.yml";
const REF = "main";

async function dispatch(env) {
  if (!env.GITHUB_TOKEN) throw new Error("Secret GITHUB_TOKEN fehlt (Worker → Settings → Variables and Secrets)");
  const url = `https://api.github.com/repos/${env.REPO || REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const init = {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "gold-radar-wecker",
      "Content-Type": "application/json",
    },
    // source=cloudflare: der Workflow behandelt den Start wie einen geplanten Lauf (keine Push-Nachricht, kein Commit-Snapshot)
    body: JSON.stringify({ ref: REF, inputs: { source: "cloudflare" } }),
  };

  let res;
  for (let attempt = 1; attempt <= 2; attempt++) {
    res = await fetch(url, init);
    if (res.status < 500) break; // bei GitHub-Störung (5xx) einmal nach 5 s wiederholen
    await new Promise((r) => setTimeout(r, 5000));
  }
  if (res.status === 204) {
    console.log("Workflow gestartet");
    return;
  }
  const detail = (await res.text()).slice(0, 300);
  const hint = { 401: "Token ungültig oder abgelaufen", 403: "Token ohne Recht 'Actions: Read and write' oder Rate-Limit",
    404: "Repo/Workflow nicht gefunden oder Token hat keinen Zugriff auf das Repo", 422: "Branch/Workflow-Eingabe ungültig" }[res.status] || "";
  throw new Error(`GitHub antwortete ${res.status} ${hint}: ${detail}`);
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  // Keine Web-Schnittstelle: der Worker ist nur ein Wecker.
  async fetch() {
    return new Response("Gold-Radar Wecker: nur Cron-Trigger, keine Web-Schnittstelle.\n", { status: 404 });
  },
};

export { dispatch };
