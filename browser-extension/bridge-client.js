// Talks to the Rýchlik desktop app on 127.0.0.1. The secret token lives only in extension storage and is
// sent from the background script, never from a web page, so a page cannot use the app's local server.
const RYCHLIK_PORT = 17655;
const RYCHLIK_TOKEN_KEY = "rychlikToken";

async function rychlikBridgeFetch(api, path, body) {
  const stored = await api.storage.local.get(RYCHLIK_TOKEN_KEY);
  const token = (stored[RYCHLIK_TOKEN_KEY] || "").trim();
  if (!token) {
    const error = new Error("Vlož token z Rýchlika (Nastavenia → Browser extension) do okna tohto rozšírenia.");
    error.code = "no-token";
    throw error;
  }
  const payload = {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Rychlik-Token": token},
    body: JSON.stringify(body)
  };
  let response;
  try {
    response = await fetch(`http://127.0.0.1:${RYCHLIK_PORT}${path}`, payload);
  } catch (_) {
    response = await fetch(`http://localhost:${RYCHLIK_PORT}${path}`, payload);
  }
  const text = await response.text();
  let parsed = null;
  try { parsed = JSON.parse(text); } catch (_) {}
  if (!response.ok) {
    const error = new Error(response.status === 401
      ? "Token nesedí. Skopíruj nový token z Rýchlika."
      : (parsed?.error || text || "Aplikácia požiadavku odmietla."));
    error.code = response.status === 401 ? "bad-token" : "rejected";
    throw error;
  }
  return parsed || {};
}

if (typeof module !== "undefined") module.exports = {rychlikBridgeFetch, RYCHLIK_PORT, RYCHLIK_TOKEN_KEY};
