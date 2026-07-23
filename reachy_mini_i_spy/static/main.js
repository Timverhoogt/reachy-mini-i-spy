"use strict";

const $ = (id) => document.getElementById(id);
let currentState = "not_started";
let csrfToken = "";

function toast(message) {
  $("toast").textContent = message;
  $("toast").style.display = "block";
  window.setTimeout(() => { $("toast").style.display = "none"; }, 4500);
}

async function request(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Request failed safely");
  return data;
}

async function authorizedRequest(path, options = {}) {
  const caregiverToken = $("caregiver-token").value;
  if (!caregiverToken) throw new Error("Enter the caregiver access token first");
  if (!csrfToken) {
    const session = await request("/api/caregiver/session", {
      method: "POST", headers: { "Authorization": `Bearer ${caregiverToken}` },
    });
    csrfToken = session.csrf_token;
  }
  const headers = {
    ...(options.headers || {}),
    "Authorization": `Bearer ${caregiverToken}`,
    "X-I-Spy-CSRF": csrfToken,
  };
  try {
    const data = await request(path, { ...options, headers });
    csrfToken = data.csrf_token || "";
    return data;
  } catch (error) {
    csrfToken = "";
    throw error;
  }
}

function render(game) {
  currentState = game.state;
  $("state").textContent = String(game.state).replaceAll("_", " ");
  $("message").textContent = game.message || "";
  const active = game.camera_active === true;
  $("camera-badge").textContent = active ? "● Camera active" : "Camera off";
  $("camera-badge").className = `badge ${active ? "on" : "off"}`;
  const guessing = game.state === "guessing";
  $("guess").disabled = !guessing;
  $("send").disabled = !guessing;
  $("mic").disabled = !guessing || !("SpeechRecognition" in window || "webkitSpeechRecognition" in window);
  $("start").textContent = game.state === "reveal" ? "Play another round" : "Start / next round";
}

async function refresh() {
  try {
    const data = await request("/api/status");
    render(data.game);
    const config = data.config || {};
    $("provider").value = config.provider || "openai";
    $("compute-profile").textContent = data.deployment_profile === "wireless"
      ? "Wireless: vision and provider calls run on Reachy's onboard CM4."
      : "Lite: vision and provider calls run on this connected Mac/PC.";
    const localAssets = config.local_assets || {};
    $("local-state").textContent = localAssets.ready
      ? "Local models installed and verified"
      : "Local models not installed";
    $("config-state").textContent = config.provider === "local"
      ? (localAssets.ready ? "Local mode ready" : "Install local models first")
      : (config.api_key_configured ? "API key configured" : "API key required");
  } catch (error) { toast(error.message); }
}

$("start").addEventListener("click", async () => {
  try {
    const data = await authorizedRequest("/api/game/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        language: $("language").value, age_band: $("age").value,
        camera_consent: $("camera-consent").checked,
      }),
    });
    render(data.game);
  } catch (error) { toast(error.message); }
});

$("stop").addEventListener("click", async () => {
  try {
    const data = await request("/api/game/stop", { method: "POST" });
    $("camera-consent").checked = false;
    render(data.game);
  } catch (error) { toast(error.message); }
});

$("guess-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("guess").value.trim();
  if (!text) return;
  $("guess").value = "";
  try {
    await authorizedRequest("/api/game/guess", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    });
  } catch (error) { toast(error.message); }
});

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = { provider: $("provider").value };
  if ($("api-key").value) payload.api_key = $("api-key").value;
  try {
    const data = await authorizedRequest("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    $("api-key").value = "";
    $("config-state").textContent = data.config.provider === "local"
      ? (data.config.local_assets.ready ? "Saved; local mode ready" : "Saved; install local models first")
      : (data.config.api_key_configured ? "Saved; API key configured" : "API key required");
  } catch (error) { toast(error.message); }
});

$("local-setup").addEventListener("click", async () => {
  $("local-state").textContent = "Downloading and verifying local models…";
  try {
    const data = await authorizedRequest("/api/local/setup", { method: "POST" });
    $("local-state").textContent = data.assets.ready
      ? "Local models installed and verified"
      : "Local model setup incomplete";
    $("config-state").textContent = data.assets.ready ? "Local mode ready" : "Install local models first";
  } catch (error) {
    $("local-state").textContent = "Local model setup failed";
    toast(error.message);
  }
});

$("mic").addEventListener("click", () => {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition || currentState !== "guessing") return;
  const recognition = new Recognition();
  recognition.lang = $("language").value === "nl" ? "nl-NL" : "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.onresult = (event) => { $("guess").value = event.results[0][0].transcript.slice(0, 80); };
  recognition.onerror = () => toast("Speech input was unavailable. You can type your guess.");
  recognition.start();
});

refresh();
window.setInterval(async () => {
  try { const data = await request("/api/status"); render(data.game); } catch (_) { /* next poll retries */ }
}, 1000);