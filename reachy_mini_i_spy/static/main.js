"use strict";

const $ = (id) => document.getElementById(id);
let currentState = "not_started";
let currentGame = {};
let epoch = 0;
let busy = false;
let stopping = false;
let offline = true;
let shown = true;
let poll = 0;
let readPending = null;
let scene = null;
let recognition = null;
let toastTimer = 0;
let configInitialized = false;
let localSetupBusy = false;

class RequestError extends Error {
  constructor(message, status = 0) { super(message); this.status = status; }
}

function toast(message) {
  $("toast").textContent = message;
  $("toast").style.display = "block";
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { $("toast").style.display = "none"; }, 4500);
}

async function request(path, options = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, { cache: "no-store", ...options, signal: controller.signal });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new RequestError(
      typeof data.detail === "string" ? data.detail : "The app could not complete the request.", response.status,
    );
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new RequestError("The app did not confirm in time. Check status before retrying.");
    throw error;
  } finally { window.clearTimeout(timeout); }
}

function abortRecognition() {
  const old = recognition;
  recognition = null;
  try { old?.abort(); } catch (_) { /* browser may already have ended capture */ }
}

function controls() {
  const guessing = !offline && !busy && !stopping && currentState === "guessing";
  $("guess").disabled = !guessing;
  $("send").disabled = !guessing;
  $("hint").disabled = !guessing;
  $("give-up").disabled = !guessing;
  $("mic").disabled = !guessing || !("SpeechRecognition" in window || "webkitSpeechRecognition" in window);
  $("start").disabled = offline || busy || stopping || currentGame.cleanup_pending === true
    || ["searching", "selecting", "guessing"].includes(currentState);
  $("start").textContent = currentState === "reveal" ? "Play another round" : "Start / next round";
  // Stop deliberately remains reachable during reads, writes, errors and disconnection.
}

function draw(mode = "") {
  scene?.update(window.ISpyScene.view(currentGame, mode));
}

function unconfirmed(mode = "offline") {
  offline = true;
  abortRecognition();
  $("guess").value = "";
  $("state").textContent = mode === "stopping" ? "stopping" : "connection lost";
  $("camera-badge").textContent = "Camera unconfirmed";
  $("camera-badge").className = "badge off";
  draw(mode);
  controls();
}

function render(game) {
  if (!game || typeof game.state !== "string") throw new Error("Game status unavailable");
  if (game.state !== "guessing" || game.generation !== currentGame.generation) {
    abortRecognition();
    $("guess").value = "";
  }
  currentGame = game;
  currentState = game.state;
  offline = false;
  $("state").textContent = game.state.replaceAll("_", " ");
  $("message").textContent = game.message || "";
  const dutch = game.language === "nl";
  $("hint").textContent = dutch ? "Een hint" : "Hint";
  $("give-up").textContent = dutch ? "Ik geef op" : "I give up";
  const count = Number.isInteger(game.guess_count) && game.guess_count >= 0 && game.guess_count <= 3
    ? String(game.guess_count) : "—";
  $("attempts").textContent = dutch
    ? `Pogingen: ${count} / 3. Een hint kost geen poging.`
    : `Guesses: ${count} / 3. Hints do not use a guess.`;
  const active = game.camera_active === true;
  $("camera-badge").textContent = active ? "● Camera allowed" : "Camera off";
  $("camera-badge").className = `badge ${active ? "on" : "off"}`;
  draw();
  controls();
}

async function refresh() {
  if (!shown || busy || readPending) return;
  const token = {};
  readPending = token;
  const admitted = epoch;
  try {
    const data = await request("/api/status");
    if (admitted !== epoch || !shown || busy) return;
    // A failed Stop stays unconfirmed until a fresh status proves a non-active state.
    if (stopping) {
      if (!["stopped", "idle", "error"].includes(data.game?.state)) return;
      stopping = false;
    }
    render(data.game);
    const config = data.config || {};
    // Ordinary polling must not overwrite an owner's unsaved provider selection.
    if (!configInitialized) {
      $("provider").value = config.provider || "openai";
      configInitialized = true;
    }
    $("compute-profile").textContent = data.deployment_profile === "wireless"
      ? "Wireless: vision and provider calls run on Reachy's onboard CM4."
      : "Lite: vision and provider calls run on this connected Mac/PC.";
    const localAssets = config.local_assets || {};
    if (!localSetupBusy) {
      $("local-state").textContent = localAssets.ready ? "Local models installed and verified" : "Local models not installed";
      $("config-state").textContent = config.provider === "local"
        ? (localAssets.ready ? "Local mode ready" : "Install local models first")
        : (config.api_key_configured ? "API key configured" : "API key required");
    }
  } catch (_) {
    if (admitted === epoch && shown && !busy) unconfirmed();
  } finally {
    if (readPending === token) readPending = null;
  }
}

async function gameWrite(path, payload) {
  if (busy || stopping || offline) return;
  const admitted = ++epoch;
  readPending = null;
  busy = true;
  abortRecognition();
  controls();
  try {
    const data = await request(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (admitted !== epoch || !shown) return;
    render(data.game);
  } catch (error) {
    if (admitted === epoch && shown) {
      if (!(error instanceof RequestError && error.status >= 400 && error.status < 500)) unconfirmed();
      toast(error.message);
    }
  } finally {
    if (admitted === epoch) { busy = false; controls(); }
  }
}

$("start").addEventListener("click", () => {
  void gameWrite("/api/game/start", {
    language: $("language").value, age_band: $("age").value,
    camera_consent: $("camera-consent").checked,
  });
});

$("stop").addEventListener("click", async () => {
  // Invalidate old status, Start, guess and speech callbacks before any await.
  const admitted = ++epoch;
  busy = true;
  stopping = true;
  readPending = null;
  $("camera-consent").checked = false;
  unconfirmed("stopping");
  try {
    const data = await request("/api/game/stop", { method: "POST" });
    if (admitted !== epoch || !shown) return;
    if (data.game?.state !== "stopped") throw new Error("Stop has not been confirmed. Please retry Stop.");
    stopping = false;
    render(data.game);
  } catch (error) {
    if (admitted === epoch && shown) { unconfirmed(); toast(error.message); }
  } finally {
    if (admitted === epoch) { busy = false; controls(); }
  }
});

$("guess-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if ($("send").disabled) return;
  const text = $("guess").value.trim();
  if (!text) return;
  $("guess").value = "";
  void gameWrite("/api/game/guess", { text });
});

for (const [id, en, nl] of [["hint", "hint", "een hint"], ["give-up", "I give up", "ik geef op"]]) {
  $(id).addEventListener("click", () => {
    if (!$(id).disabled) void gameWrite("/api/game/guess", { text: currentGame.language === "nl" ? nl : en });
  });
}

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = { provider: $("provider").value };
  if ($("api-key").value) payload.api_key = $("api-key").value;
  try {
    const data = await request("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    $("api-key").value = "";
    $("config-state").textContent = data.config.provider === "local"
      ? (data.config.local_assets.ready ? "Saved; local mode ready" : "Saved; install local models first")
      : (data.config.api_key_configured ? "Saved; API key configured" : "API key required");
  } catch (error) { toast(error.message); }
});

$("local-setup").addEventListener("click", async () => {
  if (localSetupBusy) return;
  localSetupBusy = true;
  $("local-setup").disabled = true;
  $("local-state").textContent = "Downloading and verifying local models…";
  try {
    const data = await request("/api/local/setup", { method: "POST" }, 180000);
    $("local-state").textContent = data.assets.ready ? "Local models installed and verified" : "Local model setup incomplete";
    $("config-state").textContent = data.assets.ready ? "Local mode ready" : "Install local models first";
  } catch (error) {
    $("local-state").textContent = "Local model setup is unconfirmed. Check model status before retrying.";
    toast(error.message);
  } finally {
    localSetupBusy = false;
    $("local-setup").disabled = false;
  }
});

$("mic").addEventListener("click", () => {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition || $("mic").disabled) return;
  abortRecognition();
  const admitted = epoch;
  const generation = currentGame.generation;
  const active = new Recognition();
  recognition = active;
  const current = () => recognition === active && admitted === epoch && shown && !offline
    && !stopping && !busy && currentState === "guessing" && generation === currentGame.generation;
  active.lang = currentGame.language === "nl" ? "nl-NL" : "en-US";
  active.interimResults = false;
  active.maxAlternatives = 1;
  active.onresult = (event) => {
    if (current()) $("guess").value = event.results[0][0].transcript.slice(0, 80);
  };
  active.onerror = () => { if (current()) toast("Speech input was unavailable. You can type your guess."); };
  active.onend = () => { if (recognition === active) recognition = null; };
  try { active.start(); } catch (_) { abortRecognition(); toast("Speech input unavailable. Type your guess."); }
});

function openPage() {
  shown = true;
  if (!scene) scene = window.HomebodyScene?.mount($("game-scene"), { label: "I Spy" });
  draw("offline");
  void refresh();
  if (!poll) poll = window.setInterval(() => { void refresh(); }, 1000);
}

window.addEventListener("pagehide", () => {
  shown = false;
  epoch += 1;
  busy = false;
  readPending = null;
  abortRecognition();
  $("guess").value = "";
  $("api-key").value = "";
  window.clearInterval(poll);
  window.clearTimeout(toastTimer);
  poll = 0;
  scene?.destroy();
  scene = null;
  offline = true;
  controls();
});
window.addEventListener("pageshow", () => { if (!shown) openPage(); });
openPage();
