/* Standalone categorical adapter for the vendored HomebodyScene renderer.
   No targets, guesses, generated text or device actions belong in a scene. */
(() => {
  const COPY = {
    en: {
      preview: ["Ready", "A little detective desk", "A grown-up can allow the camera to start a round."],
      searching: ["Searching", "Looking around", "Reachy is searching for an object."],
      selecting: ["Choosing", "Thinking it over", "Choosing a safe object from this search."],
      guessing: ["Your turn", "What could it be?", "Type a guess. Speak is optional when available."],
      reveal: ["Round finished", "That round is done", "Read Reachy's reply. Another round starts only when you choose."],
      stopped: ["Stopped", "Game stopped", "Check the camera and motor status before starting again."],
      stopping: ["Stopping", "Stop requested", "Waiting for the app to confirm. Stop remains available."],
      error: ["Needs attention", "The game could not continue", "Read the game message before trying again."],
      offline: ["Offline", "Connection lost", "Camera and robot state are unconfirmed. Stop remains available."],
      unknown: ["Unconfirmed", "Waiting for game status", "No game activity is confirmed."],
    },
    nl: {
      preview: ["Klaar", "Een klein speurdersbureau", "Een volwassene kan de camera toestaan om te beginnen."],
      searching: ["Zoeken", "Even rondkijken", "Reachy zoekt een voorwerp."],
      selecting: ["Kiezen", "Even nadenken", "Een veilig voorwerp uit deze zoekronde kiezen."],
      guessing: ["Jouw beurt", "Wat zou het zijn?", "Typ je gok. Spreken kan als je browser dit ondersteunt."],
      reveal: ["Ronde klaar", "Deze ronde is afgelopen", "Lees Reachy's antwoord. Jij kiest of er nog een ronde komt."],
      stopped: ["Gestopt", "Spel gestopt", "Controleer de camera- en motorstatus voordat je opnieuw begint."],
      stopping: ["Stoppen", "Stop aangevraagd", "Wachten op bevestiging. De stopknop blijft beschikbaar."],
      error: ["Even opletten", "Het spel kon niet doorgaan", "Lees het spelbericht voordat je het opnieuw probeert."],
      offline: ["Offline", "Verbinding verbroken", "De camera- en robotstatus zijn onbekend. Stop blijft beschikbaar."],
      unknown: ["Onbekend", "Wachten op de spelstatus", "Er is nog geen spelactiviteit bevestigd."],
    },
  };
  const PHASE = {
    idle: "preview", searching: "searching", selecting: "thinking", guessing: "waiting",
    reveal: "ended", stopped: "ended", error: "trouble", stopping: "ending", offline: "offline",
  };
  function view(game = {}, mode = "") {
    const state = mode || game.state;
    const key = state === "idle" ? "preview" : (PHASE[state] ? state : "unknown");
    const [chip, title, detail] = COPY[game.language === "nl" ? "nl" : "en"][key];
    return { scene: "ispy", phase: PHASE[state] || "paused", chip, title, detail };
  }
  window.ISpyScene = Object.freeze({ view });
})();
