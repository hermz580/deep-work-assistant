/* DWA OS — one shell over the real local APIs. No mock data, no network beyond localhost. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = { dash: null, body: null, focusMinutes: 50, previewStream: null, breathTimer: null, breathMinutes: 2 };
  const COLUMN_NAMES = { backlog: "Backlog", ready: "Ready", in_progress: "In progress", review: "Review", done: "Done" };

  // ── helpers ──────────────────────────────────────────────
  async function api(path, options = {}) {
    const init = { method: options.method || "GET", headers: {} };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
    return data;
  }
  function toast(message) {
    const el = $("toast");
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.classList.remove("show"), 3200);
  }
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of children) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return node;
  }
  function facts(target, rows) {
    const dl = $(target);
    dl.replaceChildren(...rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v ?? "—")]));
  }
  const minutes = (seconds) => {
    const m = Math.round((seconds || 0) / 60);
    return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
  };
  const dot = (id, level) => { $(id).className = `dot ${level}`; };
  const when = (iso) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");

  // ── navigation ───────────────────────────────────────────
  function openApp(name) {
    document.querySelectorAll(".app").forEach((b) => b.classList.toggle("active", b.dataset.app === name));
    document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("active", s.id === name));
    if (name !== "body") stopPreview();
    try { localStorage.setItem("dwa-os-app", name); } catch (_) { /* storage may be blocked */ }
    history.replaceState(null, "", `#${name}`);
  }
  document.querySelectorAll(".app").forEach((b) => b.addEventListener("click", () => openApp(b.dataset.app)));
  window.addEventListener("hashchange", () => {
    const name = location.hash.slice(1);
    if (document.getElementById(name)?.classList.contains("screen")) openApp(name);
  });
  document.addEventListener("keydown", (event) => {
    if (event.target.closest("input, select, textarea") || event.ctrlKey || event.metaKey || event.altKey) return;
    const apps = [...document.querySelectorAll(".app")];
    const index = Number(event.key) - 1;
    if (index >= 0 && index < apps.length) openApp(apps[index].dataset.app);
  });

  // ── data ─────────────────────────────────────────────────
  async function refresh() {
    try {
      const [dash, body] = await Promise.all([api("/api/dashboard"), api("/api/body")]);
      state.dash = dash;
      state.body = body;
      $("linkState").textContent = "local · live";
      renderAll();
    } catch (error) {
      $("linkState").textContent = "offline";
      console.error(error);
    }
  }
  function renderAll() {
    renderPulse();
    renderToday();
    renderFocus();
    renderBody();
    renderBoard();
    renderEvidence();
    renderSystem();
  }

  // ── pulse + today ────────────────────────────────────────
  function renderPulse() {
    const { dash, body } = state;
    const running = dash.assistant?.running;
    const capture = dash.diagnostics?.windows_activity_capture;
    dot("pulseSense", running ? "ok" : capture ? "warn" : "bad");
    $("pulseSenseText").textContent = running ? (body.consent.granted && body.camera.ready ? "activity + posture" : "activity") : "assistant stopped";
    const evidence = dash.profile?.evidence_sessions || 0;
    dot("pulseModel", body.calibrated && evidence >= 3 ? "ok" : evidence || body.calibrated ? "warn" : "");
    $("pulseModelText").textContent = `${evidence} sessions · posture ${body.calibrated ? "personal" : "generic"}`;
    const pomo = dash.pomodoro || {};
    dot("pulseAct", pomo.state && pomo.state !== "idle" ? "ok" : running ? "ok" : "");
    $("pulseActText").textContent = pomo.state && pomo.state !== "idle" ? pomo.state.replace("_", " ") : running ? "reminders armed" : "standing by";
  }

  function nextAction() {
    const { dash, body } = state;
    const pomo = dash.pomodoro || {};
    if (!dash.assistant?.running && dash.diagnostics?.windows_activity_capture) {
      return { title: "Start the assistant", body: "Recovery timers only advance while DWA is watching real human activity.", label: "Start assistant", run: toggleAssistant };
    }
    if (!body.consent.granted) {
      return { title: "Set up your body baseline", body: "Posture guidance is off until you consent. It stays local and only stores angles.", label: "Open Body", run: () => openApp("body") };
    }
    if (!body.calibrated) {
      return { title: "Calibrate your neutral posture", body: "Right now alerts use a generic threshold. 20 seconds of calibration makes them yours.", label: "Calibrate", run: () => openApp("body") };
    }
    if (body.latest?.state === "forward") {
      return { title: "Sit back and reset your neck", body: `Your last reading was ${body.latest.fwd_head_deg}° — past your ${body.threshold_deg}° threshold.`, label: "Desk reset", run: () => openApp("recover") };
    }
    if (!pomo.state || pomo.state === "idle") {
      return { title: "Begin a focus block", body: `Your default is ${dash.settings?.default_focus_minutes || 50} minutes.`, label: "Begin focus", run: () => openApp("focus") };
    }
    return { title: "Stay with it", body: "Everything is running. DWA will interrupt only when your body needs it.", label: null };
  }

  function renderToday() {
    const { dash, body } = state;
    $("todayDate").textContent = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    const next = nextAction();
    $("nextTitle").textContent = next.title;
    $("nextBody").textContent = next.body;
    const button = $("nextButton");
    button.hidden = !next.label;
    if (next.label) { button.textContent = next.label; button.onclick = next.run; }

    const diag = dash.diagnostics || {};
    facts("senseFacts", [
      ["Windows activity", diag.windows_activity_capture ? "ready" : "not on this platform"],
      ["Assistant", dash.assistant?.running ? "watching" : "stopped"],
      ["Camera path", body.camera.ready ? "ready" : "not installed"],
      ["Posture consent", body.consent.granted ? "granted" : "not granted"],
      ["Human / agent", dash.activity_mix?.human_seconds || dash.activity_mix?.agent_seconds
        ? `${Math.round((dash.activity_mix.human_ratio || 0) * 100)}% / ${Math.round((dash.activity_mix.agent_ratio || 0) * 100)}%` : "no data"],
    ]);
    const profile = dash.profile || {};
    facts("modelFacts", [
      ["Work style", profile.flow_style || "learning"],
      ["Main category", profile.dominant_category || "learning"],
      ["Evidence", `${profile.evidence_sessions || 0} sessions${(profile.evidence_sessions || 0) < 3 ? " (calibrating)" : ""}`],
      ["Posture neutral", body.baseline ? `${body.baseline.fwd_head_median}° (${body.baseline.confidence})` : "not calibrated"],
      ["Streak", `${dash.streak?.current_streak || 0} days`],
    ]);
    const plan = dash.plan || {};
    facts("actFacts", [
      ["Hydration", plan.hydration_minutes ? `every ${plan.hydration_minutes}m` : "—"],
      ["Stretch", plan.stretch_minutes ? `every ${plan.stretch_minutes}m` : "—"],
      ["Meal", plan.eat_minutes ? `every ${plan.eat_minutes}m` : "—"],
      ["Posture alert", `${body.threshold_deg}° (${body.threshold_source})`],
      ["Productivity", `${dash.score?.overall ?? dash.score?.score ?? dash.weekly?.productivity_score ?? 0} / 100`],
    ]);

    const trend = (dash.trend || []).slice(-14);
    const peak = Math.max(30, ...trend.map((d) => d.focus_minutes));
    $("trendBars").replaceChildren(...trend.map((d) => {
      const bar = el("div", { title: `${d.date}: ${d.focus_minutes}m` }, el("span", {}, new Date(`${d.date}T12:00`).toLocaleDateString([], { weekday: "narrow" })));
      bar.style.height = `${Math.max(2, (d.focus_minutes / peak) * 100)}%`;
      return bar;
    }));
    $("weekLine").textContent = `${dash.weekly?.total_focus_minutes || 0} min this week · ${dash.weekly?.total_sessions || 0} sessions`;
  }

  // ── focus ────────────────────────────────────────────────
  function renderFocus() {
    const { dash } = state;
    const pomo = dash.pomodoro || {};
    const active = pomo.state && pomo.state !== "idle";
    const remaining = active ? pomo.remaining_minutes * 60 : state.focusMinutes * 60;
    const mm = Math.floor(remaining / 60), ss = Math.floor(remaining % 60);
    $("timerValue").textContent = `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
    $("timerPhase").textContent = active ? `${pomo.state.replace("_", " ")}${pomo.is_paused ? " · paused" : ""}` : "ready";
    const total = active ? pomo.phase_duration_minutes * 60 : remaining;
    const progress = total ? 1 - remaining / total : 0;
    $("ringFill").style.strokeDashoffset = String(553 * (1 - progress));
    $("focusStart").disabled = active;
    $("focusPause").disabled = !active;
    $("focusNext").disabled = !active;
    $("focusStop").disabled = !active;
    $("focusPause").textContent = pomo.is_paused ? "Resume" : "Pause";

    const select = $("focusCard");
    const current = select.value;
    const cards = (dash.board?.cards || []).filter((c) => c.column !== "done");
    select.replaceChildren(el("option", { value: "" }, "No linked card"), ...cards.map((c) => el("option", { value: c.card_id }, c.title)));
    select.value = active ? pomo.active_card_id || "" : current;

    const plan = dash.plan || {};
    facts("planFacts", [
      ["Hydration", `${plan.hydration_minutes || "—"} min`],
      ["Stretch", `${plan.stretch_minutes || "—"} min`],
      ["Meal", `${plan.eat_minutes || "—"} min`],
      ["Evidence", `${dash.profile?.evidence_sessions || 0} sessions`],
    ]);
  }
  document.querySelectorAll("[data-minutes]").forEach((pill) => pill.addEventListener("click", () => {
    document.querySelectorAll("[data-minutes]").forEach((p) => p.classList.toggle("active", p === pill));
    state.focusMinutes = Number(pill.dataset.minutes);
    if (state.dash) renderFocus();
  }));
  async function pomodoro(action, body = {}) {
    try {
      state.dash.pomodoro = await api(`/api/pomodoro/${action}`, { method: "POST", body });
      if (action === "stop") state.dash.pomodoro = { state: "idle" };
      renderFocus();
      renderPulse();
    } catch (error) { toast(error.message); }
  }
  $("focusStart").addEventListener("click", () => {
    const s = state.dash.settings || {};
    pomodoro("start", {
      work_minutes: state.focusMinutes,
      short_break_minutes: s.short_break_minutes || 5,
      long_break_minutes: s.long_break_minutes || 15,
      pomodoros_before_long: s.pomodoros_before_long || 4,
      card_id: $("focusCard").value,
    });
  });
  $("focusPause").addEventListener("click", () => pomodoro(state.dash.pomodoro.is_paused ? "resume" : "pause"));
  $("focusNext").addEventListener("click", () => pomodoro("next"));
  $("focusStop").addEventListener("click", () => pomodoro("stop"));

  // ── body (the real camera helper) ────────────────────────
  function renderBody() {
    const body = state.body;
    const consented = body.consent.granted;
    const step = !consented ? "consent" : !body.calibrated ? "calibrate" : "live";
    const order = ["consent", "frame", "calibrate", "live"];
    document.querySelectorAll("#bodySteps li").forEach((li) => {
      const i = order.indexOf(li.dataset.step), current = order.indexOf(step);
      li.className = i < current ? "done" : i === current ? "current" : "";
    });
    $("consentGrant").hidden = consented;
    $("consentRevoke").hidden = !consented;
    $("cameraReadiness").textContent = body.camera.ready
      ? "Camera path ready on this machine."
      : `Camera path not ready: ${body.camera.reason}. Install with pip install -e ".[vision]" then run: deep-work-assistant vision provision.`;
    $("calibrate").disabled = !consented;
    $("clearBaseline").hidden = !body.calibrated;
    const b = body.baseline;
    facts("baselineFacts", [
      ["Neutral forward-head", b ? `${b.fwd_head_median}°` : "not calibrated"],
      ["Spread", b ? `±${b.fwd_head_spread}°` : "—"],
      ["Confidence", b ? `${b.confidence} (${b.samples} samples)` : "—"],
      ["Alert threshold", `${body.threshold_deg}° · ${body.threshold_source}`],
      ["Calibrated", b ? when(b.calibrated_at) : "—"],
    ]);

    const latest = body.latest;
    const tag = $("postureTag");
    tag.className = `tag ${latest ? latest.state : ""}`;
    tag.textContent = latest ? `${latest.state} · ${latest.fwd_head_deg}°` : "no reading";
    $("loadValue").textContent = latest?.cervical_load_lbs ?? "—";
    $("loadNote").textContent = body.neutral_load_lbs
      ? `Your neutral is about ${body.neutral_load_lbs} lbs. Estimate from a forward-head proxy (Hansraj curve): motivation, not diagnosis.`
      : "Estimate from a forward-head proxy (Hansraj curve): motivation, not diagnosis.";
    $("inaccurate").disabled = !consented;
    $("inaccurateNote").textContent = body.inaccurate_reports
      ? `${body.inaccurate_reports} correction(s) recorded; threshold widened by ${body.inaccurate_reports * 3}°.` : "";
    drawChart(body);

    if (body.profile.height_cm && document.activeElement !== $("heightInput")) $("heightInput").value = body.profile.height_cm;
    $("workstationInput").value = body.profile.workstation;
    const desk = $("deskTargets");
    if (!body.workstation) {
      desk.replaceChildren(el("p", {}, "Add your height to get chair, desk, and monitor starting points."));
    } else {
      const t = body.workstation.targets_cm;
      const label = { seat_height_cm: "Seat height", desk_height_cm: "Desk / keyboard", eye_height_cm: "Seated eye level",
        monitor_top_cm: "Monitor top edge", viewing_distance_cm: "Viewing distance", standing_desk_height_cm: "Standing desk" };
      desk.replaceChildren(
        ...Object.entries(t).map(([k, v]) => el("div", {}, el("b", {}, `${v} cm`), el("span", {}, `${label[k]} · ${(v / 2.54).toFixed(1)}″`))),
        el("p", {}, `${body.workstation.tip} (${body.workstation.basis}.)`),
      );
    }
  }

  function drawChart(body) {
    const svg = $("postureChart");
    const ns = "http://www.w3.org/2000/svg";
    const points = body.series.filter((p) => p.fwd_head_deg !== null);
    svg.replaceChildren();
    const W = 400, H = 140, pad = 24;
    const top = Math.max(body.threshold_deg + 10, ...points.map((p) => p.fwd_head_deg));
    const y = (v) => H - pad - (v / top) * (H - 2 * pad);
    const line = (cls, v, label) => {
      const l = document.createElementNS(ns, "line");
      Object.entries({ x1: pad, x2: W - 4, y1: y(v), y2: y(v), class: cls }).forEach(([k, val]) => l.setAttribute(k, val));
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", 4); t.setAttribute("y", y(v) + 4); t.textContent = label;
      svg.append(l, t);
    };
    line("thr", body.threshold_deg, `${body.threshold_deg}°`);
    if (body.baseline) line("base", body.baseline.fwd_head_median, `${body.baseline.fwd_head_median}°`);
    if (points.length < 2) {
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", W / 2 - 70); t.setAttribute("y", H / 2); t.textContent = "No posture samples yet";
      svg.append(t);
      return;
    }
    const step = (W - pad - 8) / (points.length - 1);
    const path = document.createElementNS(ns, "polyline");
    path.setAttribute("class", "series");
    path.setAttribute("points", points.map((p, i) => `${pad + i * step},${y(p.fwd_head_deg)}`).join(" "));
    svg.append(path);
  }

  async function bodyCall(path, method = "POST", body = {}) {
    const result = await api(path, { method, body: method === "DELETE" ? undefined : body });
    state.body = result;
    renderBody();
    renderToday();
    renderPulse();
    return result;
  }
  $("consentGrant").addEventListener("click", () => bodyCall("/api/body/consent", "POST", { granted: true }).then(() => toast("Consent saved. Next: frame yourself and calibrate.")).catch((e) => toast(e.message)));
  $("consentRevoke").addEventListener("click", () => {
    if (!confirm("Revoke posture consent and delete your personal baseline?")) return;
    bodyCall("/api/body/consent", "POST", { granted: false }).then(() => toast("Consent revoked; baseline deleted.")).catch((e) => toast(e.message));
  });
  $("clearBaseline").addEventListener("click", () => bodyCall("/api/body/baseline", "DELETE").then(() => toast("Baseline reset; generic threshold restored.")).catch((e) => toast(e.message)));
  $("inaccurate").addEventListener("click", () => bodyCall("/api/body/inaccurate").then(() => toast("Thanks — alerts will be a little less sensitive.")).catch((e) => toast(e.message)));
  $("calibrate").addEventListener("click", async () => {
    stopPreview(); // the Python probe needs the camera; release the browser's hold first
    const button = $("calibrate");
    button.disabled = true;
    $("calibrateStatus").textContent = "Hold your comfortable upright posture… sampling locally.";
    try {
      await bodyCall("/api/body/calibrate", "POST", { samples: 8 });
      $("calibrateStatus").textContent = `Calibrated: neutral ${state.body.baseline.fwd_head_median}°, alerts at ${state.body.threshold_deg}°.`;
    } catch (error) {
      $("calibrateStatus").textContent = `Calibration did not finish: ${error.message}`;
    } finally {
      button.disabled = !state.body.consent.granted;
    }
  });
  $("deskForm").addEventListener("submit", (event) => {
    event.preventDefault();
    bodyCall("/api/body/profile", "PATCH", { height_cm: Number($("heightInput").value), workstation: $("workstationInput").value })
      .then(() => toast("Workstation saved.")).catch((e) => toast(e.message));
  });

  async function startPreview() {
    try {
      state.previewStream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
      $("preview").srcObject = state.previewStream;
      await $("preview").play();
      $("vfEmpty").hidden = true;
      $("previewToggle").textContent = "Stop preview";
    } catch (error) {
      toast(`Preview unavailable: ${error.message}`);
    }
  }
  function stopPreview() {
    if (!state.previewStream) return;
    state.previewStream.getTracks().forEach((t) => t.stop());
    state.previewStream = null;
    $("preview").srcObject = null;
    $("vfEmpty").hidden = false;
    $("previewToggle").textContent = "Start preview";
  }
  $("previewToggle").addEventListener("click", () => (state.previewStream ? stopPreview() : startPreview()));

  // ── board ────────────────────────────────────────────────
  function renderBoard() {
    const columns = state.dash.board?.columns || Object.keys(COLUMN_NAMES);
    const cards = state.dash.board?.cards || [];
    $("columns").replaceChildren(...columns.map((column, index) => {
      const mine = cards.filter((c) => c.column === column);
      return el("section", { class: "column", "aria-label": COLUMN_NAMES[column] || column },
        el("h3", {}, COLUMN_NAMES[column] || column, el("span", { class: "muted" }, mine.length)),
        ...mine.map((card) => el("article", { class: `task prio-${["low", "medium", "high"][card.priority] || "low"}` },
          el("p", {}, card.title),
          el("div", { class: "row" },
            index > 0 ? el("button", { class: "btn", "aria-label": `Move ${card.title} left`, onclick: () => moveCard(card, columns[index - 1]) }, "←") : "",
            index < columns.length - 1 ? el("button", { class: "btn", "aria-label": `Move ${card.title} right`, onclick: () => moveCard(card, columns[index + 1]) }, "→") : "",
            el("small", { class: "muted" }, card.session_time_seconds ? minutes(card.session_time_seconds) : ""),
          ))));
    }));
  }
  async function moveCard(card, column) {
    try { await api(`/api/cards/${card.card_id}/move`, { method: "POST", body: { column } }); await refresh(); }
    catch (error) { toast(error.message); }
  }
  $("cardForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = $("cardTitle").value.trim();
    if (!title) return;
    try { await api("/api/cards", { method: "POST", body: { title } }); $("cardTitle").value = ""; await refresh(); }
    catch (error) { toast(error.message); }
  });

  // ── recover ──────────────────────────────────────────────
  document.querySelectorAll("[data-breath]").forEach((pill) => pill.addEventListener("click", () => {
    document.querySelectorAll("[data-breath]").forEach((p) => p.classList.toggle("active", p === pill));
    state.breathMinutes = Number(pill.dataset.breath);
  }));
  $("breathStart").addEventListener("click", () => {
    if (state.breathTimer) { clearInterval(state.breathTimer); state.breathTimer = null; endBreath("Ready"); return; }
    const cues = ["Breathe in", "Hold", "Breathe out", "Hold"];
    const end = Date.now() + state.breathMinutes * 60000;
    let phase = 0;
    const tick = () => {
      if (Date.now() >= end) { clearInterval(state.breathTimer); state.breathTimer = null; endBreath("Done — welcome back"); return; }
      $("breathCue").textContent = cues[phase % 4];
      $("breath").classList.toggle("in", phase % 4 === 0 || phase % 4 === 1);
      phase += 1;
    };
    tick();
    state.breathTimer = setInterval(tick, 4000);
    $("breathStart").textContent = "Stop";
  });
  function endBreath(text) {
    $("breathCue").textContent = text;
    $("breath").classList.remove("in");
    $("breathStart").textContent = "Start box breathing";
  }

  // ── evidence ─────────────────────────────────────────────
  function renderEvidence() {
    const insights = state.dash.insights || [];
    $("insights").replaceChildren(...(insights.length ? insights : ["No insights yet — they appear after real sessions."]).map((t) => el("li", {}, t)));
    const sessions = state.dash.recent_sessions || [];
    $("sessionRows").replaceChildren(...(sessions.length
      ? sessions.map((s) => el("tr", {}, el("td", {}, when(s.started_at)), el("td", {}, s.primary_app || "—"), el("td", {}, minutes(s.duration_seconds)),
        el("td", {}, minutes(s.human_active_seconds)), el("td", {}, minutes(s.agent_active_seconds)), el("td", {}, s.ended_reason || "—")))
      : [el("tr", {}, el("td", { colspan: "6", class: "muted" }, "No completed sessions yet."))]));
  }

  // ── system ───────────────────────────────────────────────
  function renderSystem() {
    const a = state.dash.assistant || {};
    $("assistantLine").textContent = a.running ? `Running · pid ${a.pid} · since ${when(a.started_at)}` : "Stopped";
    $("assistantToggle").textContent = a.running ? "Stop assistant" : "Start assistant";
    $("assistantLog").textContent = (a.recent_log || []).join("\n") || "No log yet.";
    const d = state.dash.diagnostics || {};
    facts("diagFacts", [
      ["Status", d.status], ["Version", d.version], ["Python", d.python],
      ["Activity capture", d.windows_activity_capture ? "ready" : "unavailable"],
      ["Voice (cloud TTS)", d.voice_available ? "installed" : "not installed"],
      ["Vision", d.vision?.installed ? "installed" : "not installed"],
      ["Posture personalization", d.learning?.posture_personalization],
      ["Sessions / cards", `${d.counts?.sessions ?? 0} / ${d.counts?.cards ?? 0}`],
    ]);
  }
  async function toggleAssistant() {
    const running = state.dash.assistant?.running;
    try {
      state.dash.assistant = await api(`/api/assistant/${running ? "stop" : "start"}`, { method: "POST", body: {} });
      toast(running ? "Assistant stopped." : "Assistant started.");
      await refresh();
    } catch (error) { toast(error.message); }
  }
  $("assistantToggle").addEventListener("click", toggleAssistant);

  // ── boot ─────────────────────────────────────────────────
  function tickClock() {
    $("clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const pomo = state.dash?.pomodoro;
    if (pomo && pomo.state !== "idle" && !pomo.is_paused && pomo.remaining_minutes > 0) {
      pomo.remaining_minutes = Math.max(0, pomo.remaining_minutes - 1 / 60);
      renderFocus();
    }
  }
  let initial = location.hash.slice(1);
  if (!initial) { try { initial = localStorage.getItem("dwa-os-app") || ""; } catch (_) { initial = ""; } }
  if (document.getElementById(initial)?.classList.contains("screen")) openApp(initial);
  tickClock();
  setInterval(tickClock, 1000);
  refresh();
  setInterval(refresh, 15000);
})();
