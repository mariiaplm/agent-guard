// AgentGuard dashboard logic: polling the backend and rendering the page
const API = new URLSearchParams(location.search).get("api") || "http://localhost:8000";
const LOG_POLL_MS = 1500, STATS_POLL_MS = 3000, MAX_ROWS = 500;

let experiment = "";
let paused = false, lastLogId = 0, logs = [], onlyBlocked = false, runFilter = null, query = "";
const $ = id => document.getElementById(id);

// Attack emails are attacker-written text: escape everything before it goes into HTML.
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money = n => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const count = (rate, n) => Math.round((rate || 0) * (n || 0));

async function getJSON(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

//  results

function renderSide(el, label, color, d) {
  d = d || {};
  const blocked = d.calls_blocked || 0;
  el.innerHTML = `
    <h3><i style="background:${color}"></i>${label}</h3>
    <div class="frac ${d.attack_runs ? "" : "none"}">${d.attack_runs ? `${d.attacks_succeeded}<span class="den">/${d.attack_runs}</span>` : "–"}</div>    <p class="frac-label">${d.attack_runs ? "attacks got through" : "No attack runs yet"}</p>
    <dl class="facts">
      <div><dt>legit tasks done</dt><dd>${d.benign_runs ? `${d.benign_tasks_succeeded}<span class="den">/${d.benign_runs}</span>` : "–"}</dd></div>      <div><dt>tool ${blocked === 1 ? "call" : "calls"} blocked</dt><dd>${blocked}</dd></div>
      <div><dt>finished runs</dt><dd>${d.runs || 0}</dd></div>
    </dl>`;
}

function renderResults(s) {
  renderSide($("side-off"), "Guard off", "var(--off)", s.guard_off);
  renderSide($("side-on"), "Guard on", "var(--on)", s.guard_on);
}

//  runs
const TASK_NAMES = {
  pay_rent: "Pay rent", pay_electric: "Pay electric bill", summarize_latest: "Summarize latest email",
  reply_boss: "Reply to boss", forward_receipt: "Forward receipt",
};

function renderRuns(runs) {
  if (!runs.length) { $("runsBody").innerHTML = `<tr><td colspan="5" class="empty">No runs in this experiment yet.</td></tr>`; return; }
  $("runsBody").innerHTML = runs.map(r => {
    let outcome;
    if (r.status !== "finished") outcome = `<span class="plain">Running</span>`;
    else if (r.attack_succeeded) outcome = `<span class="badge">Hijacked</span>`;
    else if (r.attack_id) outcome = `<span class="badge blue">Defended</span>`;
    else outcome = `<span class="plain">No attack</span>`;
    const done = r.status !== "finished" ? `<span class="plain">–</span>`
      : r.task_succeeded ? `<span class="done">Yes</span>` : `<span class="notdone">No</span>`;
    return `<tr>
      <td class="id"><button class="runlink" data-run="${esc(r.run_id)}" title="Show this run's tool calls">${esc(r.run_id)}</button></td>
      <td>${esc(TASK_NAMES[r.task_id] || r.task_id)}</td>
      <td>${r.guard_enabled ? "On" : "Off"}</td>
      <td>${outcome}</td><td>${done}</td></tr>`;
  }).join("");
}

// ------------------------------------------------------------------ tool-call log
function describe(l) {
  const a = l.args || {};
  if (l.tool_name === "transfer_money") return `Send ${money(a.amount ?? 0)} to ${a.to_account ?? "?"}`;
  if (l.tool_name === "send_email") return `Email ${a.to ?? "?"}${a.subject ? `: “${a.subject}”` : ""}`;
  if (l.tool_name === "read_email") return "Read the inbox";
  return JSON.stringify(a);
}

// escape, then wrap search matches in <mark>
function hl(text) {
  const safe = esc(text);
  if (!query) return safe;
  const q = esc(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return safe.replace(new RegExp(q, "gi"), m => `<mark>${m}</mark>`);
}

function matches(l) {
  if (!query) return true;
  const hay = [l.run_id, l.tool_name, describe(l), JSON.stringify(l.args), l.reason, l.result,
               l.provenance === "untrusted_content" ? "email content" : "user",
               l.decision === "block" ? "blocked" : "allowed"].join(" ").toLowerCase();
  return hay.includes(query.toLowerCase());
}

function renderLogs(freshIds = new Set()) {
  let rows = logs;
  if (onlyBlocked) rows = rows.filter(l => l.decision === "block");
  if (runFilter) rows = rows.filter(l => l.run_id === runFilter);
  rows = rows.filter(matches);
  $("logCount").textContent = `${rows.length} ${rows.length === 1 ? "call" : "calls"}`;
  $("runChip").innerHTML = runFilter ? `Run <span class="id">${esc(runFilter)}</span><button id="clearRun">Show all runs</button>` : "";
  if (runFilter) $("clearRun").onclick = () => { runFilter = null; renderLogs(); };
  if (!rows.length) {
    $("logBody").innerHTML = `<tr><td colspan="9" class="empty">${logs.length ? "No calls match these filters." : "Tool calls show up here as the agent makes them."}</td></tr>`;
    return;
  }
  $("logBody").innerHTML = rows.map(l => {
    const blocked = l.decision === "block";
    const time = new Date(l.timestamp).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
    const decision = blocked
      ? `<span class="badge ${freshIds.has(l.id) ? "fresh" : ""}">Blocked</span>`
      : `<span class="allowed">Allowed</span>`;
    const from = l.provenance === "untrusted_content" ? `<span class="src-email">Email content</span>` : "User";
    return `<tr class="${blocked ? "void" : ""}">
      <td class="when">${esc(time)}</td>
      <td class="id"><button class="runlink" data-run="${esc(l.run_id)}" title="Show only this run">${hl(l.run_id)}</button></td>
      <td class="r">${l.step}</td>
      <td class="what">${hl(describe(l))}<span class="tool">${hl(l.tool_name)}</span></td>
      <td>${from}</td>
      <td>${decision}</td>
      <td class="why">${hl(l.reason)}</td>
      <td class="r">${Number(l.score).toFixed(2)}</td>
      <td class="result" title="${esc(l.result)}">${blocked ? "Not run" : hl(l.result ?? "–")}</td></tr>`;
  }).join("");
}

document.addEventListener("click", e => {
  const el = e.target.closest && e.target.closest("[data-run]");
  if (el) { runFilter = el.dataset.run; renderLogs(); }
});
document.querySelectorAll('input[name="show"]').forEach(r =>
  r.addEventListener("change", () => { onlyBlocked = r.value === "blocked" && r.checked; renderLogs(); }));
$("search").addEventListener("input", e => { query = e.target.value.trim(); renderLogs(); });
$("pauseBtn").onclick = () => {
  paused = !paused;
  $("pauseBtn").setAttribute("aria-pressed", String(paused));
  $("pauseBtn").textContent = paused ? "Resume updates" : "Pause updates";
};

// ------------------------------------------------------------------ polling
async function pollLogs() {
  if (paused) return;
  try {
    const exp = experiment ? `&experiment=${encodeURIComponent(experiment)}` : "";
    const fresh = await getJSON(`/logs?since_id=${lastLogId}&limit=200${exp}`); // newest first    if (!fresh.length) return;
    const firstLoad = lastLogId === 0;
    lastLogId = Math.max(lastLogId, ...fresh.map(l => l.id));
    logs = fresh.concat(logs).slice(0, MAX_ROWS);
    renderLogs(firstLoad ? new Set() : new Set(fresh.map(l => l.id)));
  } catch (e) { /* pollStats reports connection problems */ }
}

async function pollStats() {
  if (paused) return;
  try {
    const q = experiment ? `experiment=${encodeURIComponent(experiment)}` : "";
    const [health, stats, runs, exps] = await Promise.all([
      getJSON("/health"), getJSON(`/stats?${q}`), getJSON(`/runs?limit=50&${q}`),
      getJSON("/experiments").catch(() => []),
    ]);
    syncExperiments(exps);
    const guard = health.guard === "stub" ? "the stub guard (allows everything)" : `<b>${esc(health.guard)}</b>`;
    $("live").innerHTML = `<span class="pulse ok"></span>Live from <b>${esc(API.replace(/^https?:\/\//, ""))}</b>, using ${guard}.`;
    renderResults(stats); renderRuns(runs);
  } catch (e) {
    $("live").innerHTML = `<span class="pulse bad"></span>Can't reach ${esc(API)}. Start it with <b>uvicorn backend.main:app --port 8000</b>.`;
  }
}

function syncExperiments(exps) {
  const sel = $("expSel");
  const have = [...sel.options].map(o => o.value).filter(Boolean);
  if (JSON.stringify(have) === JSON.stringify(exps)) return;
  sel.innerHTML = `<option value="">All runs</option>` +
    exps.map(e => `<option value="${esc(e)}">${esc(e)}</option>`).join("");
  sel.value = exps.includes(experiment) ? experiment : "";
}
$("expSel").addEventListener("change", e => {
  experiment = e.target.value;
  $("runsBody").innerHTML = `<tr><td colspan="5" class="empty">Loading…</td></tr>`;
  logs = []; lastLogId = 0; runFilter = null; renderLogs();
  pollStats(); pollLogs();
});

renderResults({});
pollStats(); pollLogs();
setInterval(pollLogs, LOG_POLL_MS);
setInterval(pollStats, STATS_POLL_MS);