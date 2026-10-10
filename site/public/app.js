"use strict";

// The page works without this script: every command group is listed and the
// deck shows one still frame. This adds the group selector, the search box and
// the demo on the deck.

function setUpCommands() {
  const selector = document.getElementById("selector");
  const search = document.getElementById("command-search");
  const noMatch = document.getElementById("no-match");
  const keys = Array.from(selector.querySelectorAll("[data-group]"));
  const tables = Array.from(document.querySelectorAll("table.commands"));
  const notes = Array.from(document.querySelectorAll("[data-group-note]"));
  let current = "playback";

  function render() {
    const query = search.value.trim().toLowerCase();
    let shown = 0;
    for (const table of tables) {
      let rows = 0;
      for (const row of table.tBodies[0].rows) {
        row.hidden = query !== "" && !row.textContent.toLowerCase().includes(query);
        if (!row.hidden) rows += 1;
      }
      table.hidden = query === "" ? table.dataset.group !== current : rows === 0;
      if (!table.hidden) shown += 1;
    }
    for (const note of notes) {
      const table = tables.find((t) => t.dataset.group === note.dataset.groupNote);
      note.hidden = table.hidden;
    }
    noMatch.hidden = shown > 0;
  }

  for (const key of keys) {
    key.addEventListener("click", () => {
      current = key.dataset.group;
      for (const other of keys) other.setAttribute("aria-pressed", String(other === key));
      search.value = "";
      render();
    });
  }
  search.addEventListener("input", render);
  selector.hidden = false;
  render();
}

// Invented songs and lines: the demo must never show real lyrics.
const SONGS = [
  { title: "MIDNIGHT DRIVE", artist: "NEON COAST", length: 221,
    lines: ["we left the lights on", "and the radio kept on singing",
            "every red light turned to green", "nobody said a word", "the city hummed along"] },
  { title: "PAPER SATELLITES", artist: "THE LOW HOURS", length: 245,
    lines: ["we folded the map into a plane", "threw it out the window",
            "it never came down", "so we followed it home"] },
  { title: "COPPER SUMMER", artist: "JUNE ARCADE", length: 178,
    lines: ["the fan was on high", "and the record skipped twice",
            "we sang over the gap", "like it was part of the song"] },
];

const EFFECTS = {
  bass: { screen: "BASS · LOW END LIFTED", spoken: "Bass boost: the low end is lifted." },
  nightcore: { screen: "NIGHTCORE · 1.25× FASTER, HIGHER", spoken: "Nightcore: 1.25 times faster, higher pitch." },
  "8d": { screen: "8D · CIRCLES YOUR HEAD", spoken: "8D: the sound circles your head. Use headphones." },
  echo: { screen: "ECHO · A SECOND VOICE, 60 MS BEHIND", spoken: "Echo: a second voice, 60 milliseconds behind." },
};

const SECONDS_PER_LINE = 3;
const VU_LEVELS = 8;

function setUpDeck() {
  const deck = document.querySelector(".deck");
  const el = (id) => document.getElementById(id);
  const screen = { track: el("lcd-track"), time: el("lcd-time"), song: el("lcd-song"),
                   past: el("lcd-past"), now: el("lcd-now") };
  const bars = Array.from(el("vu").children);
  const status = el("deck-status");
  const toggle = deck.querySelector('[data-action="toggle"]');
  const knobs = Array.from(deck.querySelectorAll(".knob"));
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  // Matches the still frame in the HTML, so enabling the script changes nothing visible.
  const state = { song: 0, seconds: 92, playing: !reducedMotion.matches, onScreen: true,
                  effect: null, flashUntil: 0 };
  let clock = null;
  let meter = null;

  const pad = (n) => String(n).padStart(2, "0");
  const mmss = (s) => `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;

  function draw() {
    const song = SONGS[state.song];
    const line = Math.floor(state.seconds / SECONDS_PER_LINE) % song.lines.length;
    const fx = state.effect ? `FX ${state.effect.toUpperCase()}` : "FX OFF";
    screen.track.textContent = `TRK ${pad(state.song + 1)}/${pad(SONGS.length)} · ${fx}`;
    screen.time.textContent = `${mmss(state.seconds)} ▸ ${mmss(song.length)}`;
    screen.song.textContent = Date.now() < state.flashUntil
      ? EFFECTS[state.effect].screen
      : `${song.title} — ${song.artist}`;
    if (state.seconds === 0 && !state.playing) {
      screen.past.textContent = "";
      screen.now.textContent = "▸ stopped";
    } else {
      screen.past.textContent = song.lines[(line + song.lines.length - 1) % song.lines.length];
      screen.now.textContent = `▸ ${song.lines[line]}`;
    }
  }

  function drawMeter(quiet) {
    for (const bar of bars) {
      const level = quiet ? 0 : 1 + Math.floor(Math.random() * (VU_LEVELS - 1));
      bar.className = `lv-${level}`;
    }
  }

  function tick() {
    state.seconds += 1;
    if (state.seconds >= SONGS[state.song].length) {
      state.song = (state.song + 1) % SONGS.length;
      state.seconds = 0;
    }
    draw();
  }

  // Runs only while the demo plays, the deck is on screen and the tab is
  // visible, so nothing burns CPU in the background.
  function sync() {
    const run = state.playing && state.onScreen && !document.hidden;
    if (run && clock === null) clock = setInterval(tick, 1000);
    if (!run && clock !== null) { clearInterval(clock); clock = null; }
    const animate = run && !reducedMotion.matches;
    if (animate && meter === null) meter = setInterval(() => drawMeter(false), 140);
    if (!animate && meter !== null) { clearInterval(meter); meter = null; }
    if (!state.playing) drawMeter(true);
    toggle.classList.toggle("is-paused", !state.playing);
    toggle.classList.toggle("key-on", state.playing);
    toggle.setAttribute("aria-label", state.playing ? "Pause the demo" : "Play the demo");
  }

  function announce(message) {
    status.textContent = message;
  }

  function changeSong(step) {
    state.song = (state.song + step + SONGS.length) % SONGS.length;
    state.seconds = 0;
    const song = SONGS[state.song];
    announce(`Song ${state.song + 1} of ${SONGS.length}: ${song.title.toLowerCase()} by ${song.artist.toLowerCase()}.`);
  }

  const actions = {
    toggle() { state.playing = !state.playing; announce(state.playing ? "Demo playing." : "Demo paused."); },
    previous() { changeSong(-1); },
    next() { changeSong(1); },
    stop() { state.playing = false; state.seconds = 0; announce("Demo stopped."); },
  };

  for (const key of deck.querySelectorAll("[data-action]")) {
    key.addEventListener("click", () => { actions[key.dataset.action](); draw(); sync(); });
  }

  for (const knob of knobs) {
    knob.addEventListener("click", () => {
      const effect = knob.dataset.effect;
      const on = state.effect !== effect;
      state.effect = on ? effect : null;
      for (const other of knobs) other.setAttribute("aria-pressed", String(on && other === knob));
      state.flashUntil = on ? Date.now() + 2500 : 0;
      announce(on ? EFFECTS[effect].spoken : "Effects off.");
      draw();
      if (on) setTimeout(draw, 2600);
    });
  }

  new IntersectionObserver(([entry]) => { state.onScreen = entry.isIntersecting; sync(); })
    .observe(deck);
  document.addEventListener("visibilitychange", sync);
  reducedMotion.addEventListener("change", sync);

  for (const control of deck.querySelectorAll("button")) control.disabled = false;
  draw();
  sync();
}

setUpCommands();
setUpDeck();
