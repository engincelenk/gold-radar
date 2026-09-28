// Test der Wecker-Logik ohne Netzwerk:  node worker/test.mjs
import assert from "node:assert/strict";
import worker, { dispatch } from "./index.js";

const calls = [];
const respond = (...statuses) => {
  let i = 0;
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    const status = statuses[Math.min(i++, statuses.length - 1)];
    return new Response(status === 204 ? null : JSON.stringify({ message: "x" }), { status });
  };
};
const reset = () => (calls.length = 0);

// 1) Erfolg: richtige URL, Token, Eingabe
respond(204);
await dispatch({ GITHUB_TOKEN: "t0ken" });
assert.equal(calls.length, 1);
assert.equal(calls[0].url, "https://api.github.com/repos/engincelenk/gold-radar/actions/workflows/daily.yml/dispatches");
assert.equal(calls[0].init.method, "POST");
assert.equal(calls[0].init.headers.Authorization, "Bearer t0ken");
assert.deepEqual(JSON.parse(calls[0].init.body), { ref: "main", inputs: { source: "cloudflare" } });

// 2) Fehlendes Secret
reset();
await assert.rejects(dispatch({}), /GITHUB_TOKEN fehlt/);
assert.equal(calls.length, 0);

// 3) Falsche Rechte -> verständliche Fehlermeldung, kein Retry bei 4xx
respond(403);
reset();
await assert.rejects(dispatch({ GITHUB_TOKEN: "t" }), /403 Token ohne Recht/);
assert.equal(calls.length, 1);

// 4) GitHub-Störung (500) -> ein Wiederholungsversuch, dann Erfolg
respond(500, 204);
reset();
await dispatch({ GITHUB_TOKEN: "t" });
assert.equal(calls.length, 2);

// 5) Cron-Handler ruft dispatch über waitUntil auf; fetch-Handler liefert 404
respond(204);
reset();
const pending = [];
await worker.scheduled({}, { GITHUB_TOKEN: "t" }, { waitUntil: (p) => pending.push(p) });
await Promise.all(pending);
assert.equal(calls.length, 1);
assert.equal((await worker.fetch()).status, 404);

console.log("Wecker-Tests: alle OK");
