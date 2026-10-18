// ODT-CC web dashboard. Same API as the Kit panel.
// Mode "kit": Kit's timeline is the clock; this page drives it through /command and mirrors /snapshot.
// Mode "standalone" (no Kit, or ?standalone=1): this page runs the clock and the pause rules itself.
"use strict";

const FORCE_STANDALONE = new URLSearchParams(location.search).get("standalone") === "1";
const VIEWS = ["Everyone", "LA Metro", "LA28 Venue Ops", "LA28 Transport", "LADOT", "LAPD"];
const MIN_PER_SEC = 4;   // same playback speed as the twin

const S = {
  snap: null, card: null, cardUsed: false, retiredCardId: 0, approved: null, levers: null, scenarios: null,
  view: "Everyone", myPlan: {}, myResult: null, saved: {}, compare: null, busy: null, status: "",
  planning: { attendance: 70000, stagger_share: 0, stagger_delay: 0, nearby_event_people: 0,
              nearby_event_minute: 10, train_interval: 4, train_capacity: 1000,
              close_vermont: false, close_exposition: false },
  local: { minute: 0, playing: false, lastTick: 0, fetched: -1, seen: new Set(), decided: new Set(), lastDecision: null },
};

// ---------------------------------------------------------------- helpers
const $ = (id) => document.getElementById(id);
// Only touch the DOM when a section really changed: re-creating buttons twice a second would eat clicks.
function setHTML(id, html) {
  const el = $(id);
  if (el._html === html) return false;
  el._html = html; el.innerHTML = html;
  return true;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtMin = (m) => (m === null || m === undefined ? "-" : `min ${m}`);

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" },
                                body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${r.status}`);
  return data;
}

function mode() { return S.kitAlive && !FORCE_STANDALONE ? "kit" : "standalone"; }
const minute = () => (mode() === "kit" ? (S.snap?.clock?.minute ?? 0) : S.local.minute);
const state = () => S.snap?.state;

function forView(actions, view) {
  if (view === "Everyone") return actions;
  return actions.filter((a) => a.startsWith("[") && a.slice(1, a.indexOf("]")).includes(view));
}

// Actions run one after another: a click while another action is in flight waits its turn
// (dropping it would leave e.g. the scenario dropdown showing a scenario that was never applied).
let chain = Promise.resolve();
function act(label, fn) {
  chain = chain.then(async () => {
    S.busy = label; render();
    try { await fn(); S.status = ""; } catch (e) { S.status = `${label} failed: ${e.message}`; }
    S.busy = null; render(); renderPlanning();
  });
  return chain;
}

// ---------------------------------------------------------------- clock
async function poll() {
  try {
    const snap = await api("GET", "/snapshot");
    S.kitAlive = snap.clock.kit_alive;
    if (mode() === "kit") {
      S.snap = snap;                     // Kit is the clock: mirror what it sees
      if (!snap.card) { S.card = null; S.cardUsed = false; }
    } else {                             // our own clock: the state comes from standaloneTick
      S.snap = { ...(S.snap || {}), clock: { ...snap.clock, minute: S.local.minute } };
    }
    if (snap.card && snap.card.card_id !== S.card?.card_id && snap.card.card_id > S.retiredCardId) {
      S.card = snap.card; S.cardUsed = false;
    }
    if (S.status.startsWith("server offline")) S.status = "";
  } catch (e) { S.status = `server offline: ${e.message}`; }
  render();
}

async function standaloneTick(now) {
  if (mode() !== "standalone") return;
  const L = S.local;
  // advance only once the current minute has been fetched, so no minute (and no alert) is skipped
  if (L.playing && L.fetched === L.minute && now - L.lastTick >= 1000 / MIN_PER_SEC) {
    L.lastTick = now;
    L.minute = Math.min(L.minute + 1, 119);
  }
  if (L.fetched !== L.minute && !S.fetching) {
    S.fetching = true;
    try {
      const st = await api("GET", `/state?minute=${L.minute}&client=dashboard`);
      L.fetched = st.minute;
      S.snap = { ...(S.snap || {}), state: st, clock: { ...(S.snap?.clock || {}), minute: L.minute } };
      const reason = pauseReason(st);
      if (reason && L.playing) {
        L.playing = false;
        S.status = `paused: ${reason}`;
        L.decided = new Set(st.alerts.map((a) => a.id));
        L.lastDecision = st.minute;
        await recommend();
      }
      render();
    } catch (e) { S.status = `server offline: ${e.message}`; }
    S.fetching = false;
  }
}

function pauseReason(st) {
  const L = S.local;
  let fresh = false;
  for (const r of st.reports || []) {
    const key = `${r.id}|${r.minute}|${r.status}`;
    if (!L.seen.has(key)) { L.seen.add(key); if (r.status === "new") fresh = true; }
  }
  if (fresh) return "new incident reported";
  const newAlerts = st.alerts.map((a) => a.id).filter((z) => !L.decided.has(z));
  if (newAlerts.length && (L.lastDecision === null || st.minute - L.lastDecision >= 5)) return `new alert at ${newAlerts.join(", ")}`;
  return null;
}

function retireCard() {
  // after a reset or scenario switch, the previous run's card must never come back
  S.retiredCardId = Math.max(S.retiredCardId, S.card?.card_id || 0, S.snap?.card?.card_id || 0);
  S.card = null; S.cardUsed = false; S.approved = null;
}

function resetLocal() {
  Object.assign(S.local, { minute: 0, playing: false, fetched: -1, seen: new Set(), decided: new Set(), lastDecision: null });
  S.card = null; S.cardUsed = false; S.approved = null; S.myResult = null;
}

// ---------------------------------------------------------------- actions
const command = (action, extra = {}) => api("POST", "/command", { action, ...extra });

async function play() { await act("play", async () => (mode() === "kit" ? command("play") : (S.local.playing = true))); }
async function pause() { await act("pause", async () => (mode() === "kit" ? command("pause") : (S.local.playing = false))); }
async function reset() {
  await act("reset", async () => {
    retireCard();
    if (mode() === "kit") await command("reset");
    else { await api("POST", "/reset"); resetLocal(); }
  });
}
async function switchScenario(id, extra = {}) {
  await act("switching scenario", async () => {
    retireCard();
    if (mode() === "kit") await command("scenario", { id, ...extra });
    else { await api("POST", "/scenario", { id, ...extra }); resetLocal(); }
    S.scenarios = await api("GET", "/scenarios");
  });
  renderScenarioSelect();
}
async function recommend() {
  for (let attempt = 0; attempt < 3; attempt++) {
    const card = await api("POST", "/recommend", { minute: minute() });
    if (!card.stale) { S.card = card; S.cardUsed = false; return; }
    // the run was reset or switched while this card was computed: ask again once it has settled
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error("the run kept changing; press Recommend again");
}
async function approve(plan) {
  await act("approving", async () => {
    const res = await api("POST", "/approve", { plan, minute: minute() });
    S.approved = res; S.cardUsed = true;
    if (mode() === "kit") await command("approved", { data: res });
    else S.local.playing = true;
  });
}
async function keepCurrent() {
  await act("keeping current plan", async () => {
    await api("POST", "/decision/keep", { minute: minute() });
    S.cardUsed = true;
    if (mode() === "kit") await command("play"); else S.local.playing = true;
  });
}
async function inject(kind) {
  await act("injecting", async () => {
    const ev = await api("POST", "/incident/inject", { minute: minute(), kind: kind === "random" ? null : kind });
    S.status = `injected: ${ev.report}`;
  });
}
async function simulateMine() {
  await act("simulating", async () => { S.myResult = await api("POST", "/simulate", { plan: S.myPlan, minute: minute() }); });
}
async function runCompare() {
  await act("comparing", async () => {
    const plans = Object.entries(S.saved).map(([name, plan]) => ({ name, plan }));
    S.compare = await api("POST", "/compare", { plans, minute: minute(), planning: S.planning });
  });
}

// ---------------------------------------------------------------- rendering: live part (every poll)
function render() {
  const st = state();
  $("minute").textContent = `Minute ${st ? minute() : "-"}`;
  const m = mode();
  const badge = $("mode");
  badge.className = `badge ${m}`;
  badge.textContent = m === "kit" ? "Following Kit (3D twin)" : (FORCE_STANDALONE ? "Standalone (forced)" : "Standalone clock");
  $("status").textContent = (S.busy ? `${S.busy}... ` : "") + (S.status || "");
  if (!st) return;

  const sens = st.sensors;
  $("sensors").innerHTML = `Bluetooth ${sens.bluetooth_online}/${sens.bluetooth_total} online | ` +
    `${sens.ticket_scans.toLocaleString()} scanned in | last train ${sens.last_train_minute ?? "none yet"}`;
  $("sensors").className = sens.bluetooth_online < sens.bluetooth_total ? "caution" : "muted";
  $("scenario-desc").textContent = st.scenario.description;
  if ($("scenario").value !== st.scenario.id) renderScenarioSelect();

  renderBanners(st);
  renderCard();
  renderZones(st);
  renderFeed(st);
  renderLog(st);
  const busy = !!S.busy;
  $("play").disabled = busy; $("pause").disabled = busy; $("reset").disabled = busy;
  $("save-ai").disabled = !S.card;
  $("copy-ai").disabled = !S.card;
}

function renderBanners(st) {
  const out = [];
  for (const inc of st.incidents || []) {
    if (inc.status === "active") out.push(`<div class="banner red">INCIDENT at ${esc(inc.label)} (reported minute ${inc.from}, expected until about minute ${inc.to})</div>`);
  }
  if (st.alerts.length) {
    const worst = st.alerts.reduce((a, b) => (b.predicted_density > a.predicted_density ? b : a));
    out.push(worst.alert_band === "danger"
      ? `<div class="banner red">ALERT: ${esc(worst.label)} predicted dangerous at minute ${worst.predicted_minute}</div>`
      : `<div class="banner amber">WARNING: ${esc(worst.label)} approaching danger (${worst.predicted_density} p/m²) by minute ${worst.predicted_minute}</div>`);
  } else if ((st.plans_in_force || []).length) {
    out.push(`<div class="banner green">Plan in force since minute ${st.approved_minute}: no alerts</div>`);
  }
  const c = S.card;
  if (c && !S.cardUsed) {
    if (c.approve_by_minute === null) out.push(`<div class="countdown late">No fully safe plan exists any more: act now to limit the danger</div>`);
    else {
      const left = c.approve_by_minute - minute();
      out.push(left >= 0 ? `<div class="countdown">Approve by minute ${c.approve_by_minute} (${left} min left)</div>`
                         : `<div class="countdown late">Approval deadline (minute ${c.approve_by_minute}) has passed</div>`);
    }
  }
  setHTML("banners", out.join(""));
}

function renderCard() {
  const c = S.card;
  if (!c) {
    if (setHTML("card", `<p class="muted">No recommendation yet. It appears when the forecast raises an alert or an incident is reported.</p>
      <div class="row"><button id="rec-now" ${S.busy ? "disabled" : ""}>Recommend now</button></div>`)) {
      $("rec-now").onclick = () => act("recommending", recommend);
    }
    return;
  }
  const replan = (c.plans_in_force || []).length > 0;
  const tasks = S.view === "Everyone" ? "" :
    `<p><b class="caution">Your tasks (${esc(S.view)})</b><br>${(forView(c.actions, S.view).map(esc).join("<br>")) || "No tasks for you in this plan"}</p>`;
  const opts = Object.entries(c.options).sort((a, b) => b[1].score_0_to_100 - a[1].score_0_to_100).map(([id, o]) => {
    const pick = id === c.recommended;
    const parts = Object.entries(o.score_parts || {}).map(([k, v]) => `${esc(k.replaceAll("_", " "))} ${v}`).join(" · ");
    const actions = forView(o.actions, S.view);
    return `<div class="card ${pick ? "pick" : ""}">
      <div class="head"><b>${esc(id.replaceAll("_", " "))}${pick ? " (AI pick)" : ""}</b><span class="score">${o.score_0_to_100}/100</span></div>
      <div class="muted">${parts}</div>
      <div class="${o.dangerous_zone_minutes ? "danger" : ""}">${o.dangerous_zone_minutes} dangerous minutes · peak ${o.peak_density.toFixed(2)} · clears ${fmtMin(o.stadium_cleared_by_min)} · approve by ${fmtMin(o.approve_by_minute)}</div>
      <div>${actions.length ? actions.map((a) => `- ${esc(a)}`).join("<br>") : `<span class="muted">(no tasks for ${esc(S.view)})</span>`}</div>
      <div class="row"><button class="${pick ? "primary" : ""}" data-approve="${esc(id)}" ${S.cardUsed || S.busy ? "disabled" : ""}>Approve this option</button></div>
    </div>`;
  }).join("");
  const why = c.why.map((w) => `<div class="why ${w.startsWith("ESCALATE") ? "escalate" : ""}">+ ${esc(w)}</div>`).join("");
  const base = (c.baseline_label || "doing nothing");
  const b = c.do_nothing, e = c.expected;
  const changed = setHTML("card", `
    <div class="card pick">
      <div class="head"><b>${replan ? "Re-plan" : "Recommendation"}: ${esc(c.recommended.replaceAll("_", " "))}</b>
        <span class="muted">${esc(c.source)}${c.latency_s ? ` (${c.latency_s} s)` : ""}</span></div>
      <p>${esc(c.briefing)}</p>${tasks}${why}
      <p class="muted">${esc(c.reason)}</p>
      <table><tr><th></th><th>${esc(base)}</th><th>If approved now</th></tr>
        <tr><td>Dangerous zone-minutes</td><td class="danger">${b.dangerous_zone_minutes}</td><td class="ok">${e.dangerous_zone_minutes}</td></tr>
        <tr><td>Peak density (p/m²)</td><td class="danger">${b.peak_density}</td><td class="ok">${e.peak_density}</td></tr>
        <tr><td>Stadium cleared by</td><td>${fmtMin(b.stadium_cleared_by_min)}</td><td>${fmtMin(e.stadium_cleared_by_min)}</td></tr></table>
      <div class="row">
        <button class="primary" id="approve-ai" ${S.cardUsed || S.busy || c.no_change ? "disabled" : ""}>${c.no_change ? "No change needed" : "Approve AI plan"}</button>
        ${replan ? `<button id="keep" ${S.cardUsed || S.busy ? "disabled" : ""}>Keep current plan</button>` : ""}
        <button id="rec-now">Recommend again</button>
      </div>
    </div>
    <h3>Options <small>scored 0-100: safety 45 + clear speed 20 + ease 20 + time to act 15</small></h3>${opts}`);
  if (!changed) return;
  $("approve-ai").onclick = () => approve(c.plan);
  if ($("keep")) $("keep").onclick = keepCurrent;
  $("rec-now").onclick = () => act("recommending", recommend);
  document.querySelectorAll("[data-approve]").forEach((btn) => { btn.onclick = () => approve(c.options[btn.dataset.approve].levers); });
}

function renderZones(st) {
  const rows = [...st.zones].sort((a, b) => b.predicted_density - a.predicted_density).map((z) => {
    const pct = Math.min(z.predicted_density / 6, 1) * 100;
    const colour = { danger: "var(--red)", caution: "var(--amber)", ok: "var(--green)" }[z.alert_band];
    const conf = { high: ["H", "ok"], medium: ["M", "caution"], low: ["L", "danger"] }[z.confidence] || ["H", "ok"];
    return `<tr><td>${esc(z.label)}</td>
      <td>${z.density_now === null ? '<span class="caution">no data</span>' : z.density_now.toFixed(2)}</td>
      <td class="${z.alert_band}">${z.predicted_density.toFixed(2)} ±${(z.error_band ?? 0).toFixed(1)}</td>
      <td class="${conf[1]}" title="forecast confidence: ${esc(z.confidence)}">${conf[0]}</td>
      <td class="barcell"><div class="bar-bg"><div class="bar-fill" style="width:${pct}%;background:${colour}"></div>
        <div class="bar-line" style="left:${(5 / 6) * 100}%"></div></div></td></tr>`;
  }).join("");
  setHTML("zones", `<table><tr><th>Zone</th><th>Now (est.)</th><th>+15 min</th><th>conf.</th><th>forecast vs danger line</th></tr>${rows}</table>
    <p class="muted">people/m², estimated from Bluetooth counts (~30% of phones seen). Danger at 5 (white line), alert at 4.5.</p>`);
}

function renderFeed(st) {
  const reports = st.reports || [];
  $("feed").className = "feed";
  setHTML("feed", reports.length
    ? [...reports].reverse().slice(0, 8).map((r) => `<div class="${r.status === "still ongoing" ? "still" : esc(r.status)}">min ${r.minute} · ${esc(r.source)}: ${esc(r.report)}</div>`).join("")
    : '<p class="muted">No incidents reported.</p>');
  const inj = $("inject");
  if (st.live && S.scenarios) {
    if (!inj.dataset.built) {
      const kinds = ["random", ...(S.scenarios.incident_kinds || [])];
      inj.innerHTML = `<select id="inject-kind">${kinds.map((k) => `<option value="${esc(k)}">${esc(k.replaceAll("_", " "))}</option>`).join("")}</select>
        <button id="inject-btn">Inject incident now</button>`;
      $("inject-btn").onclick = () => inject($("inject-kind").value);
      inj.dataset.built = "1";
    }
    inj.style.display = "";
  } else {
    inj.style.display = "none";
  }
}

function renderLog(st) {
  const entries = st.audit || [];
  $("log").className = "log";
  setHTML("log", entries.length
    ? [...entries].reverse().map((e) => `<div class="${esc(e.kind)}">${esc(e.time.slice(11))} · min ${e.minute} · ${esc(e.text)}</div>`).join("") +
      '<p class="muted">Full audit trail: data/audit_log.jsonl</p>'
    : '<p class="muted">No decisions yet.</p>');
}

// ---------------------------------------------------------------- rendering: planning part (on demand)
function renderScenarioSelect() {
  if (!S.scenarios) return;
  const items = [...S.scenarios.scenarios];
  const cur = state()?.scenario || { id: S.scenarios.current, title: S.scenarios.current_title };
  if (!items.some((s) => s.id === cur.id)) items.push({ id: cur.id, title: cur.title });
  $("scenario").innerHTML = items.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("");
  $("scenario").value = cur.id;
}

function leverRow(id, label, value, min, max, step, onInput, fmt = (v) => v) {
  return `<div class="lever"><label for="${id}">${esc(label)}</label>
    <input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${value}">
    <output id="${id}-out">${fmt(value)}</output></div>`;
}

function bindRange(id, onInput, fmt = (v) => v) {
  const el = $(id);
  el.oninput = () => { const v = Number(el.value); $(`${id}-out`).textContent = fmt(v); onInput(v); };
}

function renderPlanning() {
  if (!S.levers) return;
  // Your plan sliders
  $("levers").innerHTML = S.levers.map((l) => leverRow(`lv-${l.key}`, `${l.label} [${l.agency}]`, S.myPlan[l.key],
    l.min, l.max, l.integer ? 1 : 0.05)).join("");
  S.levers.forEach((l) => bindRange(`lv-${l.key}`, (v) => { S.myPlan[l.key] = v; S.myResult = null; renderMyResult(); }));
  renderMyResult();
  // planned event inputs
  const P = S.planning;
  $("planning-inputs").innerHTML = [
    leverRow("pl-attendance", "Tickets sold", P.attendance, 40000, 80000, 1000),
    leverRow("pl-stagger_share", "Held for a 2nd wave (share)", P.stagger_share, 0, 0.5, 0.05),
    leverRow("pl-stagger_delay", "2nd wave leaves after (min)", P.stagger_delay, 0, 30, 1),
    leverRow("pl-nearby_event_people", "Nearby event crowd (people)", P.nearby_event_people, 0, 20000, 500),
    leverRow("pl-nearby_event_minute", "Nearby event lets out at (min)", P.nearby_event_minute, 0, 40, 1),
    leverRow("pl-train_interval", "Timetable: train every (min)", P.train_interval, 2, 8, 1),
    leverRow("pl-train_capacity", "Timetable: people per train", P.train_capacity, 600, 1200, 50),
    `<label class="row"><input type="checkbox" id="pl-close_vermont" ${P.close_vermont ? "checked" : ""}> Road closure: Vermont Ave (parking exits 40%)</label>`,
    `<label class="row"><input type="checkbox" id="pl-close_exposition" ${P.close_exposition ? "checked" : ""}> Road closure: Exposition Blvd (shuttles 50%)</label>`,
  ].join("");
  ["attendance", "stagger_share", "stagger_delay", "nearby_event_people", "nearby_event_minute", "train_interval", "train_capacity"]
    .forEach((k) => bindRange(`pl-${k}`, (v) => { S.planning[k] = v; }));
  ["close_vermont", "close_exposition"].forEach((k) => { $(`pl-${k}`).onchange = (e) => { S.planning[k] = e.target.checked; }; });
  $("saved").textContent = `Saved: ${Object.keys(S.saved).join(", ") || "none yet (save the sliders, or add the AI's plan)"}`;
  $("compare").disabled = !Object.keys(S.saved).length || !!S.busy;
  renderCompare();
}

function renderMyResult() {
  const r = S.myResult;
  if (!r) { $("my-result").innerHTML = `<p class="muted">Scores the plan as if approved now (minute ${minute()}).</p>`; return; }
  const s = r.score;
  const head = r.verdict === "UNSAFE"
    ? `UNSAFE: ${s.dangerous_zone_minutes} dangerous zone-minutes, first at ${esc(r.first_danger.label)} (minute ${r.first_danger.minute})`
    : r.verdict === "MARGINAL" ? `MARGINAL: no danger, but peak ${s.peak_density.toFixed(2)} is close to 5`
    : `SAFE: no dangerous zone-minutes, peak ${s.peak_density.toFixed(2)}`;
  $("my-result").innerHTML = `<div class="card"><b class="${r.verdict}">${head}</b>
    <div class="muted">peak ${s.peak_density.toFixed(2)} at ${esc(r.peak_zone_label)} · clears ${fmtMin(s.stadium_cleared_by_min)} · effort ${r.cost.effort} · approve by ${fmtMin(r.approve_by_minute)}</div>
    <div>${r.actions.map((a) => `- ${esc(a)}`).join("<br>")}</div>
    <div class="row"><button id="approve-mine" ${r.verdict === "UNSAFE" || S.busy ? "disabled" : ""}>${r.verdict === "UNSAFE" ? "Unsafe: can't approve" : "Approve my plan"}</button></div></div>`;
  $("approve-mine").onclick = () => approve(r.plan);
}

function renderCompare() {
  const res = S.compare;
  if (!res) { $("compare-table").innerHTML = ""; return; }
  const rows = res.rows;
  const metric = (label, f, cls) => `<tr><td class="muted">${label}</td>${rows.map((r) => `<td class="${cls ? cls(r) : ""}">${f(r)}</td>`).join("")}</tr>`;
  const bars = rows.map((r) => {
    const pct = Math.min(r.peak_density / 6, 1) * 100;
    const colour = { SAFE: "var(--green)", MARGINAL: "var(--amber)", UNSAFE: "var(--red)" }[r.verdict];
    return `<tr><td>${esc(r.name)}</td><td class="barcell" style="width:60%"><div class="bar-bg"><div class="bar-fill" style="width:${pct}%;background:${colour}"></div>
      <div class="bar-line" style="left:${(5 / 6) * 100}%"></div></div></td><td>${r.peak_density.toFixed(2)}</td></tr>`;
  }).join("");
  $("compare-table").innerHTML = `
    <p class="muted">From minute ${res.minute}: ${esc((res.planning_notes || []).join("; "))}</p>
    <table><tr><th></th>${rows.map((r) => `<th>${esc(r.name)}</th>`).join("")}</tr>
      ${metric("Verdict", (r) => r.verdict, (r) => r.verdict)}
      ${metric("Dangerous zone-min", (r) => r.dangerous_zone_minutes)}
      ${metric("Zone-min above alert", (r) => r.alert_zone_minutes)}
      ${metric("Worst bottleneck", (r) => `${esc(r.worst_zone)} ${r.peak_density.toFixed(2)}`)}
      ${metric("Stadium clears", (r) => fmtMin(r.stadium_cleared_by_min))}
      ${metric("Trains / hour", (r) => r.trains_per_hour)}
      ${metric("Extra buses", (r) => r.extra_buses)}
      ${metric("Effort (0-5)", (r) => r.effort)}
      ${metric("Approve by", (r) => fmtMin(r.approve_by_minute))}
      <tr><td></td>${rows.map((r, i) => `<td>${r.actions[0] === "No change" ? "" :
        `<button data-cmp="${i}" ${r.verdict === "UNSAFE" ? "disabled" : ""}>Approve</button>`}</td>`).join("")}</tr>
    </table>
    <h4>Peak density vs danger line (5)</h4><table>${bars}</table>`;
  document.querySelectorAll("[data-cmp]").forEach((b) => { b.onclick = () => approve(rows[Number(b.dataset.cmp)].plan); });
}

// ---------------------------------------------------------------- wiring
async function init() {
  $("play").onclick = play; $("pause").onclick = pause; $("reset").onclick = reset;
  $("view").innerHTML = VIEWS.map((v) => `<option>${esc(v)}</option>`).join("");
  $("view").onchange = (e) => { S.view = e.target.value; render(); };
  $("scenario").onchange = (e) => switchScenario(e.target.value);
  $("simulate").onclick = simulateMine;
  $("copy-ai").onclick = () => { if (S.card) { S.myPlan = { ...defaults(), ...S.card.plan }; S.myResult = null; renderPlanning(); } };
  $("defaults").onclick = () => { S.myPlan = defaults(); S.myResult = null; renderPlanning(); };
  $("save-a").onclick = () => { S.saved["Plan A"] = { ...S.myPlan }; renderPlanning(); };
  $("save-b").onclick = () => { S.saved["Plan B"] = { ...S.myPlan }; renderPlanning(); };
  $("save-ai").onclick = () => { if (S.card) { S.saved[`AI: ${S.card.recommended.replaceAll("_", " ")}`] = { ...S.card.plan }; renderPlanning(); } };
  $("compare").onclick = runCompare;
  $("clear").onclick = () => { S.saved = {}; S.compare = null; renderPlanning(); };
  $("run-live").onclick = () => switchScenario("planned", { planning: { ...S.planning } });
  try {
    [S.levers, S.scenarios] = await Promise.all([api("GET", "/levers"), api("GET", "/scenarios")]);
  } catch (e) { S.status = `server offline: ${e.message}`; }
  S.myPlan = defaults();
  await poll();
  renderScenarioSelect();
  renderPlanning();
  setInterval(poll, 500);
  const loop = (now) => { standaloneTick(now); requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
}
const defaults = () => Object.fromEntries((S.levers || []).map((l) => [l.key, l.default]));
init();
