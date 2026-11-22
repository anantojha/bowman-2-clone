"use strict";
// Bowman 2 client: draws the duel on a canvas and talks to the server over one WebSocket.
// The server runs all physics. A shot message carries the arrow's whole path, and every client
// just replays it, so both players always see the same thing.
(() => {
  // ---- numbers shared with game/config.py ---------------------------------------------------
  const W = 1000, H = 520;
  const HEAD_R = 12, HEAD_Y = 62, TORSO_TOP = 52, LEGS_TOP = 24, SHOULDER_Y = 46;
  const MIN_ANGLE = -20, MAX_ANGLE = 85, MIN_POWER = 0.15;
  const GRAVITY = 700, MAX_SPEED = 900;
  // ---- client only --------------------------------------------------------------------------
  const PULL_MAX = 130; // px of drag for full power
  const WINDUP_MS = 350; // how long an opponent's bow is seen drawing
  const CONNECT_TIMEOUT_MS = 75000; // a sleeping free server can take about a minute to wake
  const TAU = Math.PI * 2;
  const COLORS = ["#3b82e0", "#e0524a"]; // left archer, right archer

  const $ = (id) => document.getElementById(id);
  const cv = $("view");
  const ctx = cv.getContext("2d");
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const rad = (d) => (d * Math.PI) / 180;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };

  // ---- sound --------------------------------------------------------------------------------
  let actx = null;
  let muted = store.get("bowman.muted") === "1";
  function audio() {
    if (muted) return null;
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      if (actx.state === "suspended") actx.resume();
      return actx;
    } catch (e) { return null; }
  }
  function tone(freq, dur, type, vol, to, delay) {
    const a = audio();
    if (!a) return;
    const t = a.currentTime + (delay || 0);
    const o = a.createOscillator(), g = a.createGain();
    o.type = type || "sine";
    o.frequency.setValueAtTime(freq, t);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t + dur);
    g.gain.setValueAtTime(vol || 0.1, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    o.connect(g).connect(a.destination);
    o.start(t);
    o.stop(t + dur + 0.03);
  }
  function swoosh(dur, vol) {
    const a = audio();
    if (!a) return;
    const n = Math.floor(a.sampleRate * dur), buf = a.createBuffer(1, n, a.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
    const src = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain();
    f.type = "bandpass";
    f.frequency.setValueAtTime(3200, a.currentTime);
    f.frequency.exponentialRampToValueAtTime(600, a.currentTime + dur);
    g.gain.value = vol;
    src.buffer = buf;
    src.connect(f).connect(g).connect(a.destination);
    src.start();
  }
  const sfx = {
    shoot() { swoosh(0.28, 0.35); tone(200, 0.1, "triangle", 0.06, 90); },
    stick() { tone(120, 0.1, "square", 0.05, 60); },
    hit() { tone(150, 0.2, "sawtooth", 0.08, 55); },
    head() { tone(150, 0.2, "sawtooth", 0.09, 55); tone(880, 0.3, "triangle", 0.1, 1400, 0.04); },
    turn() { tone(660, 0.12, "triangle", 0.08, 880); },
    tick() { tone(1000, 0.04, "square", 0.04); },
    win() { [523, 659, 784, 1047].forEach((f, i) => tone(f, 0.28, "triangle", 0.1, null, i * 0.12)); },
    lose() { [392, 330, 262].forEach((f, i) => tone(f, 0.35, "triangle", 0.1, null, i * 0.18)); },
  };
  const muteBtn = $("mute");
  const paintMute = () => { muteBtn.textContent = muted ? "🔇" : "🔊"; };
  muteBtn.onclick = () => { muted = !muted; store.set("bowman.muted", muted ? "1" : "0"); paintMute(); };
  paintMute();

  // ---- scene --------------------------------------------------------------------------------
  let G = demoScene();
  let screen = "menu"; // menu | wait | game | over

  function makeBackdrop() {
    const clouds = [];
    for (let i = 0; i < 6; i++) clouds.push({ x: Math.random() * W, y: 40 + Math.random() * 140, s: 0.6 + Math.random() * 0.9, v: 4 + Math.random() * 8 });
    return { clouds };
  }

  function baseScene(terrain, pos) {
    return {
      demo: false, you: 0, names: ["", ""], terrain, pos, hp: [100, 100], shown: [100, 100],
      turn: 0, wind: 0, turnStart: 0, turnEnd: 0, turnMs: 25000, anim: null, trail: [], stuck: [],
      parts: [], texts: [], shake: 0, over: null, pull: [0, 0], aim: [40, 40], drag: null, sent: false,
      dead: [false, false], banner: null, bg: makeBackdrop(), opponentGone: false, lastTick: -1,
    };
  }

  function demoScene() {
    const n = W / 4 + 1, terrain = [];
    for (let i = 0; i < n; i++) {
      const x = i * 4;
      terrain.push(380 + 50 * Math.sin(x / 150 + 1) + 20 * Math.sin(x / 61));
    }
    const flat = (cx) => { for (let i = 0; i < n; i++) if (Math.abs(i * 4 - cx) < 60) terrain[i] = terrain[Math.round(cx / 4)]; };
    flat(110); flat(890);
    const g = baseScene(terrain, [[110, terrain[28]], [890, terrain[222]]]);
    g.demo = true;
    return g;
  }

  function groundAt(x) {
    const t = G.terrain, f = clamp(x / 4, 0, t.length - 1), i = Math.min(Math.floor(f), t.length - 2), k = f - i;
    return t[i] * (1 - k) + t[i + 1] * k;
  }

  // ---- network ------------------------------------------------------------------------------
  let ws = null, connecting = null, pingTimer = null;
  const configured = ((window.BOWMAN_CONFIG || {}).server || "").trim();

  function wsUrl() {
    if (configured) return configured;
    return (location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + "/ws";
  }
  function healthUrl() {
    return wsUrl().replace(/^ws/, "http").replace(/\/ws$/, "/health");
  }
  function setConn(text) { $("conn").textContent = text || ""; }

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve();
    if (connecting) return connecting;
    if (location.hostname.endsWith("github.io") && !configured) {
      return Promise.reject(new Error("This page has no game server configured (see README)."));
    }
    connecting = new Promise((resolve, reject) => {
      const deadline = Date.now() + CONNECT_TIMEOUT_MS;
      const started = Date.now();
      let attempt = 0;
      const tryOnce = () => {
        setConn(Date.now() - started > 4000 ? "Waking the server… free hosting sleeps when idle, this can take up to a minute." : "Connecting…");
        let opened = false;
        const s = new WebSocket(wsUrl());
        s.onopen = () => { opened = true; ws = s; wire(s); setConn(""); resolve(); };
        s.onclose = () => {
          if (opened) return;
          if (Date.now() < deadline) setTimeout(tryOnce, Math.min(1000 + attempt++ * 500, 4000));
          else reject(new Error("Can't reach the game server. Try again in a moment."));
        };
      };
      tryOnce();
    }).finally(() => { connecting = null; });
    return connecting;
  }

  function wire(s) {
    s.onmessage = (e) => {
      let m;
      try { m = JSON.parse(e.data); } catch (err) { return; }
      onMessage(m);
    };
    s.onclose = () => {
      if (ws !== s) return;
      ws = null;
      clearInterval(pingTimer);
      if (screen !== "menu") { toast("Lost the connection to the server."); goMenu(false); }
    };
    clearInterval(pingTimer);
    pingTimer = setInterval(() => send({ t: "ping" }), 20000);
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  let busy = false;
  function request(msg) {
    if (busy) return;
    busy = true;
    audio(); // the first click unlocks sound
    const name = $("name").value.trim();
    store.set("bowman.name", name);
    connect().then(() => send(Object.assign({ name }, msg)), (err) => { setConn(err.message); }).finally(() => { busy = false; });
  }

  // ---- messages -----------------------------------------------------------------------------
  let waitBotTimer = null;
  function onMessage(m) {
    switch (m.t) {
      case "queued":
        showWait("Looking for an opponent…", "Someone will join in a moment. Or play the bot while you wait.", null);
        clearTimeout(waitBotTimer);
        waitBotTimer = setTimeout(() => $("waitBot").classList.remove("hidden"), 6000);
        break;
      case "room":
        showWait("Private room", "Send this code to a friend. The match starts when they join.", m.code);
        break;
      case "start": startMatch(m); break;
      case "shot": onShot(m); break;
      case "timeout": onTimeout(m); break;
      case "over": onOver(m); break;
      case "left":
        G.opponentGone = true;
        if (screen === "over") paintOver();
        break;
      case "rematch_offer":
        if (screen === "over") $("overSub").textContent = "Your opponent wants a rematch!";
        break;
      case "error": toast(m.msg || "Something went wrong."); if (screen === "wait") goMenu(false); break;
      case "full": toast(m.reason || "The server is full."); break;
    }
  }

  function startMatch(m) {
    clearTimeout(waitBotTimer);
    const g = baseScene(m.terrain, m.pos);
    g.you = m.you; g.names = m.names; g.hp = m.hp.slice(); g.shown = m.hp.slice();
    g.turn = m.turn; g.wind = m.wind; g.turnMs = m.turn_ms; g.isBot = m.bot;
    g.aim = [40, 40];
    const now = performance.now();
    g.turnStart = now + m.delay_ms;
    g.turnEnd = g.turnStart + m.turn_ms;
    G = g;
    setScreen("game");
    announceTurn();
  }

  function announceTurn() {
    const mine = G.turn === G.you;
    G.banner = { text: mine ? "YOUR TURN" : G.names[G.turn].toUpperCase() + "'S TURN", color: mine ? "#f2c14e" : "#dfe6ee", t: performance.now() };
    if (mine) setTimeout(() => { if (screen === "game") sfx.turn(); }, Math.max(0, G.turnStart - performance.now()));
  }

  function beginTurn(next) {
    const now = performance.now();
    G.turn = next.turn; G.wind = next.wind; G.sent = false; G.drag = null;
    G.turnStart = now + next.delay_ms; G.turnEnd = G.turnStart + next.turn_ms;
    announceTurn();
  }

  function onShot(m) {
    G.sent = true;
    G.turnEnd = 0;
    G.drag = null;
    G.aim[m.by] = m.angle;
    const mine = m.by === G.you;
    G.anim = { m, t0: performance.now() + (mine ? 0 : WINDUP_MS), released: false };
    G.trail = [];
  }

  function arrowAngle(path, i) {
    const a = path[Math.max(0, i - 1)], b = path[Math.min(path.length - 1, i)];
    return Math.atan2(b[1] - a[1], b[0] - a[0]);
  }

  function finishShot(a) {
    const m = a.m, path = m.path, end = path[path.length - 1];
    const ang = arrowAngle(path, path.length - 1);
    G.stuck.push({ x: end[0], y: end[1], a: ang });
    if (G.stuck.length > 16) G.stuck.shift();
    G.anim = null;
    G.trail = [];
    if (m.hit) {
      const head = m.hit.zone === "head";
      burst(end[0], end[1], head ? "#ffd467" : "#ff8a80", head ? 26 : 14);
      addText(end[0], end[1] - 18, head ? "HEADSHOT! -" + m.hit.dmg : "-" + m.hit.dmg, head ? "#ffd467" : "#ffffff", head ? 26 : 20);
      G.shake = head ? 12 : 6;
      (head ? sfx.head : sfx.hit)();
      G.hp = m.hp.slice();
      G.dead = [G.hp[0] <= 0, G.hp[1] <= 0];
    } else {
      burst(end[0], end[1], "#c9b48a", 8);
      sfx.stick();
      addText(end[0], end[1] - 14, "miss", "rgba(255,255,255,0.8)", 15);
    }
    if (m.winner !== null && m.winner !== undefined) {
      G.over = { winner: m.winner, reason: "kill" };
      setTimeout(showOver, 1300);
    } else if (m.next) {
      beginTurn(m.next);
    }
  }

  function onTimeout(m) {
    G.hp = m.hp.slice();
    const f = G.pos[m.by];
    addText(f[0], f[1] - 90, "Time's up!", "#ffffff", 20);
    beginTurn(m.next);
  }

  function onOver(m) {
    G.anim = null; G.drag = null;
    G.over = { winner: m.winner, reason: m.reason };
    if (m.reason === "left") G.opponentGone = true;
    setTimeout(showOver, 500);
  }

  // ---- screens ------------------------------------------------------------------------------
  const panels = ["menu", "wait", "over"];
  function setScreen(name) {
    screen = name;
    const panel = name === "game" ? null : name;
    for (const p of panels) $(p).classList.toggle("hidden", p !== panel);
    $("quit").classList.toggle("hidden", name !== "game");
  }

  function showWait(title, hint, code) {
    setScreen("wait");
    $("waitTitle").textContent = title;
    $("waitHint").textContent = hint;
    $("waitCode").textContent = code || "";
    $("waitCode").classList.toggle("hidden", !code);
    $("copy").classList.toggle("hidden", !code);
    $("waitBot").classList.add("hidden");
  }

  function goMenu(tellServer) {
    if (tellServer !== false) send({ t: "leave" });
    clearTimeout(waitBotTimer);
    G = demoScene();
    setScreen("menu");
    setConn("");
  }

  function showOver() {
    if (screen !== "game" || !G.over) return;
    setScreen("over");
    const won = G.over.winner === G.you;
    $("overTitle").textContent = won ? "Victory!" : "Defeated";
    $("overTitle").style.color = won ? "#f2c14e" : "#e0524a";
    (won ? sfx.win : sfx.lose)();
    const btn = $("rematch");
    btn.disabled = false;
    btn.textContent = "Rematch";
    paintOver();
  }

  function paintOver() {
    const r = G.over && G.over.reason, won = G.over && G.over.winner === G.you;
    let sub;
    if (r === "left") sub = "Your opponent left the match.";
    else if (r === "timeout") sub = won ? "Your opponent ran out of time." : "You ran out of time three turns in a row.";
    else sub = won ? "A clean win against " + G.names[1 - G.you] + "." : G.names[1 - G.you] + " got the better of you.";
    $("overSub").textContent = sub;
    $("rematch").classList.toggle("hidden", !!G.opponentGone);
  }

  let toastTimer = null;
  function toast(text) {
    const t = $("toast");
    t.textContent = text;
    t.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hidden"), 3500);
  }

  // ---- buttons ------------------------------------------------------------------------------
  $("quick").onclick = () => request({ t: "quick" });
  $("bot").onclick = () => request({ t: "bot", level: +$("level").value });
  $("create").onclick = () => request({ t: "create" });
  $("join").onclick = () => {
    const code = $("code").value.trim().toUpperCase();
    if (code.length !== 4) { toast("Type the 4-letter room code first."); return; }
    request({ t: "join", code });
  };
  $("code").addEventListener("keydown", (e) => { if (e.key === "Enter") $("join").click(); });
  $("name").addEventListener("keydown", (e) => { if (e.key === "Enter") $("quick").click(); });
  $("cancel").onclick = () => goMenu(true);
  $("waitBot").onclick = () => { send({ t: "bot", name: $("name").value.trim(), level: +$("level").value }); };
  $("copy").onclick = () => {
    const code = $("waitCode").textContent;
    if (navigator.clipboard) navigator.clipboard.writeText(code).then(() => toast("Code copied."), () => {});
  };
  $("quit").onclick = () => goMenu(true);
  $("toMenu").onclick = () => goMenu(true);
  $("rematch").onclick = () => {
    send({ t: "rematch" });
    $("rematch").disabled = true;
    $("rematch").textContent = "Waiting for opponent…";
  };
  $("name").value = store.get("bowman.name") || "";

  // ---- aiming -------------------------------------------------------------------------------
  function canPlay() {
    const now = performance.now();
    return screen === "game" && !G.over && !G.anim && !G.sent && G.turn === G.you && now >= G.turnStart && now < G.turnEnd;
  }

  function aimFrom(d) {
    const face = G.you === 0 ? 1 : -1;
    const vx = d.sx - d.cx, vy = d.sy - d.cy; // pulling back and down aims forward and up
    const power = clamp(Math.hypot(vx, vy) / PULL_MAX, 0, 1);
    const angle = clamp((Math.atan2(-vy, vx * face) * 180) / Math.PI, MIN_ANGLE, MAX_ANGLE);
    return { power, angle };
  }

  const view = { s: 1, ox: 0, oy: 0, dpr: 1 };
  function toWorld(e) {
    const r = cv.getBoundingClientRect();
    return { x: (e.clientX - r.left - view.ox) / view.s, y: (e.clientY - r.top - view.oy) / view.s };
  }

  cv.addEventListener("pointerdown", (e) => {
    audio();
    if (!canPlay()) return;
    const p = toWorld(e);
    G.drag = { sx: p.x, sy: p.y, cx: p.x, cy: p.y, id: e.pointerId };
    try { cv.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  });
  cv.addEventListener("pointermove", (e) => {
    if (!G.drag || e.pointerId !== G.drag.id) return;
    const p = toWorld(e);
    G.drag.cx = p.x; G.drag.cy = p.y;
  });
  const endDrag = (e, fire) => {
    const d = G.drag;
    if (!d || e.pointerId !== d.id) return;
    G.drag = null;
    G.pull[G.you] = 0;
    if (!fire || !canPlay()) return;
    const a = aimFrom(d);
    if (a.power < MIN_POWER - 0.03) return; // a tap, not a shot
    G.sent = true;
    G.aim[G.you] = a.angle;
    send({ t: "shoot", angle: Math.round(a.angle * 10) / 10, power: Math.round(a.power * 1000) / 1000 });
  };
  cv.addEventListener("pointerup", (e) => endDrag(e, true));
  cv.addEventListener("pointercancel", (e) => endDrag(e, false));

  // ---- effects ------------------------------------------------------------------------------
  function burst(x, y, color, n) {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * TAU, v = 60 + Math.random() * 220;
      G.parts.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 80, life: 0.5 + Math.random() * 0.5, max: 1, color, size: 2 + Math.random() * 3 });
    }
  }
  function addText(x, y, text, color, size) {
    G.texts.push({ x: clamp(x, 60, W - 60), y, text, color, size, life: 1.6 });
  }

  // ---- update -------------------------------------------------------------------------------
  function update(dt, now) {
    for (const c of G.bg.clouds) { c.x += c.v * dt; if (c.x > W + 120) c.x = -120; }
    for (let i = 0; i < 2; i++) G.shown[i] += (G.hp[i] - G.shown[i]) * Math.min(1, dt * 5);
    G.shake = Math.max(0, G.shake - dt * 30);
    for (const p of G.parts) { p.life -= dt; p.vy += 600 * dt; p.x += p.vx * dt; p.y += p.vy * dt; }
    G.parts = G.parts.filter((p) => p.life > 0);
    for (const t of G.texts) { t.life -= dt; t.y -= 28 * dt; }
    G.texts = G.texts.filter((t) => t.life > 0);

    if (G.drag) {
      if (!canPlay()) G.drag = null;
      else { const a = aimFrom(G.drag); G.pull[G.you] = a.power; G.aim[G.you] = a.angle; }
    }

    const a = G.anim;
    if (a) {
      const m = a.m, by = m.by;
      const el = (now - a.t0) / 1000;
      if (el < 0) {
        G.pull[by] = clamp(1 + el / (WINDUP_MS / 1000), 0, 1) * m.power;
      } else {
        G.pull[by] = 0;
        if (!a.released) { a.released = true; sfx.shoot(); }
        const f = el / m.step;
        if (f >= m.path.length - 1) { finishShot(a); }
        else {
          const i = Math.floor(f), k = f - i, p0 = m.path[i], p1 = m.path[i + 1];
          a.x = p0[0] + (p1[0] - p0[0]) * k; a.y = p0[1] + (p1[1] - p0[1]) * k;
          a.ang = Math.atan2(p1[1] - p0[1], p1[0] - p0[0]);
          G.trail.push({ x: a.x, y: a.y, t: now });
        }
      }
    }
    while (G.trail.length && now - G.trail[0].t > 350) G.trail.shift();

    // countdown ticks on your own turn
    if (screen === "game" && G.turn === G.you && now >= G.turnStart && now < G.turnEnd && !G.sent) {
      const left = Math.ceil((G.turnEnd - now) / 1000);
      if (left <= 5 && left !== G.lastTick) { G.lastTick = left; sfx.tick(); }
    }
  }

  // ---- drawing ------------------------------------------------------------------------------
  function resize() {
    view.dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cw = window.innerWidth, ch = window.innerHeight;
    cv.width = Math.round(cw * view.dpr); cv.height = Math.round(ch * view.dpr);
    cv.style.width = cw + "px"; cv.style.height = ch + "px";
    view.s = Math.min(cw / W, ch / H);
    view.ox = (cw - W * view.s) / 2; view.oy = (ch - H * view.s) / 2;
  }
  window.addEventListener("resize", resize);
  resize();

  function line(x1, y1, x2, y2) { ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }

  function drawSky() {
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "#5aa9e6"); g.addColorStop(0.7, "#bfe3f7"); g.addColorStop(1, "#eaf7ff");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    // sun
    const sg = ctx.createRadialGradient(840, 90, 6, 840, 90, 90);
    sg.addColorStop(0, "rgba(255,244,190,1)"); sg.addColorStop(0.35, "rgba(255,236,150,0.55)"); sg.addColorStop(1, "rgba(255,236,150,0)");
    ctx.fillStyle = sg; ctx.fillRect(740, 0, 200, 190);
    ctx.fillStyle = "#fff6c2"; ctx.beginPath(); ctx.arc(840, 90, 30, 0, TAU); ctx.fill();
    // far mountains
    ctx.fillStyle = "#9db9d6";
    ctx.beginPath(); ctx.moveTo(0, H);
    for (let x = 0; x <= W; x += 20) ctx.lineTo(x, 300 - 55 * Math.abs(Math.sin(x / 130 + 0.6)) - 25 * Math.sin(x / 47));
    ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#7fa1c2";
    ctx.beginPath(); ctx.moveTo(0, H);
    for (let x = 0; x <= W; x += 20) ctx.lineTo(x, 350 - 40 * Math.abs(Math.sin(x / 90 + 2)) - 14 * Math.sin(x / 33));
    ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
    // clouds
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    for (const c of G.bg.clouds) {
      ctx.beginPath();
      ctx.arc(c.x, c.y, 22 * c.s, 0, TAU); ctx.arc(c.x + 26 * c.s, c.y + 4, 28 * c.s, 0, TAU);
      ctx.arc(c.x + 56 * c.s, c.y + 6, 20 * c.s, 0, TAU); ctx.arc(c.x + 24 * c.s, c.y - 12 * c.s, 22 * c.s, 0, TAU);
      ctx.fill();
    }
  }

  function drawTerrain() {
    const t = G.terrain;
    const path = () => { ctx.beginPath(); ctx.moveTo(0, H + 10); for (let i = 0; i < t.length; i++) ctx.lineTo(i * 4, t[i]); ctx.lineTo(W, H + 10); ctx.closePath(); };
    const g = ctx.createLinearGradient(0, 220, 0, H);
    g.addColorStop(0, "#7a5a38"); g.addColorStop(1, "#3e2b1a");
    path(); ctx.fillStyle = g; ctx.fill();
    ctx.save(); path(); ctx.clip();
    ctx.fillStyle = "rgba(0,0,0,0.12)";
    for (let i = 0; i < 60; i++) { const x = (i * 197) % W, y = 250 + ((i * 89) % 260); ctx.beginPath(); ctx.ellipse(x, y, 10 + (i % 5) * 4, 4 + (i % 3) * 2, 0, 0, TAU); ctx.fill(); }
    ctx.restore();
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath(); for (let i = 0; i < t.length; i++) (i ? ctx.lineTo(i * 4, t[i]) : ctx.moveTo(0, t[0]));
    ctx.strokeStyle = "#3f9a45"; ctx.lineWidth = 9; ctx.stroke();
    ctx.strokeStyle = "#69c25f"; ctx.lineWidth = 4; ctx.translate(0, -2); ctx.stroke(); ctx.translate(0, 2);
  }

  function drawArrow(x, y, ang, len) {
    const dx = Math.cos(ang), dy = Math.sin(ang), tx = x - dx * len, ty = y - dy * len;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#5b3a1e"; ctx.lineWidth = 2.5; line(tx, ty, x, y);
    ctx.fillStyle = "#d9dde2"; // head
    ctx.beginPath(); ctx.moveTo(x + dx * 5, y + dy * 5); ctx.lineTo(x - dx * 4 - dy * 3.5, y - dy * 4 + dx * 3.5); ctx.lineTo(x - dx * 4 + dy * 3.5, y - dy * 4 - dx * 3.5); ctx.closePath(); ctx.fill();
    ctx.strokeStyle = "#f2f2f2"; ctx.lineWidth = 1.6; // fletching
    for (const s of [-1, 1]) line(tx, ty, tx - dx * 5 + -dy * 4 * s, ty - dy * 5 + dx * 4 * s);
  }

  function drawArcher(slot, x, y) {
    const face = slot === 0 ? 1 : -1, color = COLORS[slot], dead = G.dead[slot];
    ctx.save();
    ctx.translate(x, y);
    ctx.fillStyle = "rgba(0,0,0,0.18)"; ctx.beginPath(); ctx.ellipse(0, 1, 16, 4, 0, 0, TAU); ctx.fill();
    if (dead) ctx.rotate(-face * Math.PI * 0.49);
    ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#15202b"; ctx.lineWidth = 4;
    line(0, -LEGS_TOP, -7, 0); line(0, -LEGS_TOP, 7, 0); line(0, -LEGS_TOP, 0, -TORSO_TOP);
    ctx.fillStyle = "#f7f0e4"; ctx.beginPath(); ctx.arc(0, -HEAD_Y, HEAD_R - 1, 0, TAU); ctx.fill(); ctx.stroke();
    ctx.strokeStyle = color; ctx.lineWidth = 3.5; line(-HEAD_R + 3, -HEAD_Y - 4, HEAD_R - 3, -HEAD_Y - 4);
    ctx.fillStyle = "#15202b";
    if (dead) { ctx.font = "bold 9px system-ui"; ctx.textAlign = "center"; ctx.fillText("x x", 0, -HEAD_Y + 4); }
    else { ctx.beginPath(); ctx.arc(face * 5, -HEAD_Y, 1.8, 0, TAU); ctx.fill(); }

    if (!dead) {
      const pull = G.pull[slot] || 0, ang = G.aim[slot];
      const th = face === 1 ? -rad(ang) : Math.PI + rad(ang);
      const dx = Math.cos(th), dy = Math.sin(th), px = -dy, py = dx;
      const sx = 0, sy = -SHOULDER_Y, hx = sx + dx * 16, hy = sy + dy * 16;
      const nx = hx - dx * (pull * 24 + 2), ny = hy - dy * (pull * 24 + 2);
      const t1 = [hx + px * 26, hy + py * 26], t2 = [hx - px * 26, hy - py * 26];
      ctx.strokeStyle = "#15202b"; ctx.lineWidth = 4; line(sx, sy, hx, hy); line(sx, sy, nx, ny);
      ctx.strokeStyle = "#8a5a2b"; ctx.lineWidth = 3.5;
      ctx.beginPath(); ctx.moveTo(t1[0], t1[1]); ctx.quadraticCurveTo(hx + dx * (30 + pull * 16), hy + dy * (30 + pull * 16), t2[0], t2[1]); ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,0.85)"; ctx.lineWidth = 1.3;
      ctx.beginPath(); ctx.moveTo(t1[0], t1[1]); ctx.lineTo(nx, ny); ctx.lineTo(t2[0], t2[1]); ctx.stroke();
      const armed = !G.anim || G.anim.m.by !== slot || performance.now() < G.anim.t0;
      if (armed && !G.over) drawArrow(hx + dx * 14, hy + dy * 14, th, 14 + pull * 24 + 2 + 14);
    }
    ctx.restore();
  }

  function drawPlayers() {
    for (let i = 0; i < 2; i++) {
      drawArcher(i, G.pos[i][0], G.pos[i][1]);
      if (!G.demo && !G.dead[i]) {
        ctx.font = "700 13px system-ui, sans-serif"; ctx.textAlign = "center";
        const label = G.names[i] + (i === G.you ? " (you)" : "");
        ctx.lineWidth = 3; ctx.strokeStyle = "rgba(0,0,0,0.55)"; ctx.strokeText(label, G.pos[i][0], G.pos[i][1] - 92);
        ctx.fillStyle = "#fff"; ctx.fillText(label, G.pos[i][0], G.pos[i][1] - 92);
      }
    }
  }

  function drawAim() {
    if (!G.drag || screen !== "game") return;
    const d = G.drag, a = aimFrom(d), face = G.you === 0 ? 1 : -1, f = G.pos[G.you];
    ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.lineWidth = 2; ctx.setLineDash([4, 6]);
    line(d.sx, d.sy, d.cx, d.cy); ctx.setLineDash([]);
    ctx.fillStyle = "rgba(255,255,255,0.8)"; ctx.beginPath(); ctx.arc(d.sx, d.sy, 5, 0, TAU); ctx.fill();
    // a short dotted preview of the launch (wind is not shown: that is the skill)
    const o = [f[0] + face * 16, f[1] - SHOULDER_Y - 8], sp = MAX_SPEED * Math.max(a.power, 0.001);
    const vx = face * Math.cos(rad(a.angle)) * sp, vy = -Math.sin(rad(a.angle)) * sp;
    for (let i = 1; i <= 9; i++) {
      const t = i * 0.045;
      ctx.fillStyle = "rgba(255,255,255," + (0.9 - i * 0.08) + ")";
      ctx.beginPath(); ctx.arc(o[0] + vx * t, o[1] + vy * t + 0.5 * GRAVITY * t * t, 3, 0, TAU); ctx.fill();
    }
    ctx.font = "700 14px system-ui, sans-serif"; ctx.textAlign = "center";
    const txt = Math.round(a.angle) + "°  " + Math.round(a.power * 100) + "%";
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.strokeText(txt, f[0], f[1] - 110);
    ctx.fillStyle = "#fff"; ctx.fillText(txt, f[0], f[1] - 110);
  }

  function drawFlight(now) {
    if (G.trail.length > 1) {
      for (let i = 1; i < G.trail.length; i++) {
        const p = G.trail[i - 1], q = G.trail[i], k = 1 - (now - q.t) / 350;
        ctx.strokeStyle = "rgba(255,255,255," + 0.5 * clamp(k, 0, 1) + ")"; ctx.lineWidth = 2.5; line(p.x, p.y, q.x, q.y);
      }
    }
    const a = G.anim;
    if (a && a.x !== undefined && now >= a.t0) {
      drawArrow(a.x, a.y, a.ang, 30);
      if (a.y < -4) { // above the top of the screen: show where it is
        const x = clamp(a.x, 14, W - 14);
        ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.moveTo(x, 6); ctx.lineTo(x - 8, 20); ctx.lineTo(x + 8, 20); ctx.closePath(); ctx.fill();
        ctx.font = "700 11px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.fillText(Math.round(-a.y) + "", x, 34);
      }
    }
  }

  function outlined(text, x, y, size, fill, align) {
    ctx.font = "800 " + size + "px system-ui, sans-serif"; ctx.textAlign = align || "center";
    ctx.lineWidth = Math.max(3, size / 5); ctx.strokeStyle = "rgba(10,20,30,0.7)"; ctx.lineJoin = "round";
    ctx.strokeText(text, x, y); ctx.fillStyle = fill; ctx.fillText(text, x, y);
  }

  function drawHud(now) {
    if (G.demo) return;
    for (let i = 0; i < 2; i++) {
      const w = 250, x = i === 0 ? 18 : W - 18 - w, y = 30, hp = clamp(G.shown[i] / 100, 0, 1);
      ctx.fillStyle = "rgba(10,20,30,0.55)"; ctx.fillRect(x - 3, y - 3, w + 6, 22);
      ctx.fillStyle = COLORS[i];
      if (i === 0) ctx.fillRect(x, y, w * hp, 16); else ctx.fillRect(x + w * (1 - hp), y, w * hp, 16);
      outlined(G.names[i], i === 0 ? x : x + w, y - 8, 15, "#fff", i === 0 ? "left" : "right");
      outlined(String(Math.max(0, Math.round(G.hp[i]))), x + w / 2, y + 13, 13, "#fff");
    }
    // wind
    const cx = W / 2, wy = 28, wind = G.wind, mag = Math.abs(Math.round(wind * 10));
    outlined("WIND", cx, wy - 6, 12, "rgba(255,255,255,0.85)");
    if (mag === 0) outlined("calm", cx, wy + 16, 16, "#fff");
    else {
      const len = 14 + mag * 5, dir = wind > 0 ? 1 : -1;
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 4; ctx.lineCap = "round"; ctx.fillStyle = "#fff";
      line(cx - dir * len / 2, wy + 12, cx + dir * len / 2, wy + 12);
      ctx.beginPath(); ctx.moveTo(cx + dir * (len / 2 + 9), wy + 12); ctx.lineTo(cx + dir * (len / 2 - 2), wy + 5); ctx.lineTo(cx + dir * (len / 2 - 2), wy + 19); ctx.closePath(); ctx.fill();
      outlined(String(mag), cx, wy + 38, 14, "#fff");
    }
    // clock
    if (screen === "game" && !G.over && !G.anim && now >= G.turnStart && now < G.turnEnd) {
      const left = Math.ceil((G.turnEnd - now) / 1000);
      outlined(String(left), cx, 96, 30, left <= 5 ? "#ff7b72" : "#fff");
    }
    // banner
    const b = G.banner;
    if (b && now - b.t < 1800) {
      ctx.globalAlpha = clamp(1 - (now - b.t - 1200) / 600, 0, 1);
      outlined(b.text, cx, 150, 34, b.color);
      ctx.globalAlpha = 1;
    }
    if (screen === "game" && G.turn === G.you && !G.sent && !G.drag && !G.anim && now >= G.turnStart && !G.over && !G.stuck.length) {
      outlined("Drag back anywhere, then let go", cx, H - 18, 18, "#fff");
    }
    if (window.innerHeight > window.innerWidth) outlined("Rotate your phone for a bigger view", cx, H - 46, 16, "#ffd467");
  }

  function render(now) {
    ctx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
    ctx.fillStyle = "#101a26"; ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.setTransform(view.dpr * view.s, 0, 0, view.dpr * view.s, view.dpr * view.ox, view.dpr * view.oy);
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, W, H); ctx.clip();
    if (G.shake > 0) ctx.translate((Math.random() - 0.5) * G.shake, (Math.random() - 0.5) * G.shake);
    drawSky();
    drawTerrain();
    for (const s of G.stuck) drawArrow(s.x, s.y, s.a, 28);
    drawPlayers();
    drawFlight(now);
    for (const p of G.parts) { ctx.globalAlpha = clamp(p.life * 2, 0, 1); ctx.fillStyle = p.color; ctx.fillRect(p.x - p.size / 2, p.y - p.size / 2, p.size, p.size); }
    ctx.globalAlpha = 1;
    for (const t of G.texts) { ctx.globalAlpha = clamp(t.life * 1.5, 0, 1); outlined(t.text, t.x, t.y, t.size, t.color); }
    ctx.globalAlpha = 1;
    drawAim();
    ctx.restore();
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, W, H); ctx.clip();
    drawHud(now);
    ctx.restore();
  }

  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    update(dt, now);
    render(now);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ---- start up -----------------------------------------------------------------------------
  // Poke a sleeping free server awake while the player is still typing their name.
  if (configured) { try { fetch(healthUrl(), { mode: "no-cors" }).catch(() => {}); } catch (e) { /* ignore */ } }
  setScreen("menu");
  // A tiny hook for tests and debugging in the console.
  window.__bowman = { state: () => G, screen: () => screen };
})();
