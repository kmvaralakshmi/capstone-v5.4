/* ESG Intelligence System - single-page app (vanilla JS, no build step). */
const $ = (s, r = document) => r.querySelector(s);
const API = "";
const PAGES = [
  ["analyze", "Analyze Company"], ["dashboard", "Dashboard"], ["performance", "ESG Performance"],
  ["market", "Market Analysis"], ["news", "News Intelligence"], ["env", "Environmental Risk"],
  ["greenwashing", "Greenwashing Detection"], ["agents", "Agent Activity"], ["timing", "Performance"],
  ["sources", "Sources & Evidence"], ["validation", "Validation & Research"], ["cohort", "Cohort Ranking"]];
const PAGE_CAPABILITY = {
  dashboard: "dashboard", performance: "performance", market: "market", news: "news",
  env: "environment", greenwashing: "greenwashing", agents: "agents", timing: "timing",
  sources: "sources", validation: "validation", cohort: "cohort"
};
const S = { page: "analyze", report: null, job: null, timer: null, batchTimer: null,
  mode: (localStorage.getItem("esg_mode") === "dynamic" ? "dynamic" : "offline"), health: null,
  capabilities: null };
const q = () => "mode=" + S.mode;
const MODE_INFO = {
  offline: { title: "OFFLINE", how: "Bundled datasets only, no LLM. Deterministic and reproducible: every result on every page was produced by plain code on the bundled data. Fast (seconds to a couple of minutes).",
             banner: "OFFLINE mode — only bundled data and deterministic code are used." },
  dynamic: { title: "DYNAMIC", how: "Live prices, news and air-quality data are used when available; each source is labeled, and unavailable feeds use an explicit cached or bundled source. The LLM (if available) judges selected items and may write the explanation.",
             banner: "" } };

const esc = v => String(v ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (v, d = 1) => (v === null || v === undefined || v === "" || isNaN(v)) ? "—" : Number(v).toFixed(d);
const badge = (t, k) => `<span class="badge b-${k}">${esc(t)}</span>`;
const riskBadge = r => badge(r || "n/a", { Low: "good", Medium: "warn", High: "bad" }[r] || "blue");
const llmUsageLabel = u => {
  if (!u) return "";
  const v = u.verdicts || {}, n = u.narrations || {};
  return `LLM verdicts ${v.llm || 0}/${v.total || 0}, LLM narrations ${n.llm || 0}/${n.total || 0}, fallbacks ${v.rule_fallback || 0}`;
};
const bar = (v, max = 100) => `<div class="bar"><i style="width:${Math.max(0, Math.min(100, (v / max) * 100))}%"></i></div>`;
const cite = t => {
  const safe = esc(t)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/\[([A-Z]\d+)\]/g, '<span class="cite">[$1]</span>');
  return safe.replace(/(?:<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`);
};
async function get(u) { const r = await fetch(API + u); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText); return r.json(); }
async function post(u, b) { const r = await fetch(API + u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) }); if (!r.ok) throw new Error(await r.text()); return r.json(); }

function nav() {
  if (!pageAvailable(S.page)) S.page = S.capabilities && S.capabilities.dashboard ? "dashboard" : "analyze";
  $("#nav").innerHTML = PAGES.filter(([k]) => pageAvailable(k))
    .map(([k, t]) => `<a href="#${k}" class="${S.page === k ? "on" : ""}">${t}</a>`).join("");
  const r = S.report;
  $("#ctxTitle").innerHTML = r ? `<h2>${esc(r.scores.Company_Name)} <small class="muted">${esc(r.company)}</small></h2><p class="muted">FY 2024-25 · run: ${esc(r.run)}</p>`
    : `<h2>${esc(PAGES.find(p => p[0] === S.page)[1])}</h2><p class="muted">BRSR · XBRL · Market · News · Air quality · Agents</p>`;
}
function pageAvailable(page) {
  const capability = PAGE_CAPABILITY[page];
  return !capability || !S.capabilities || S.capabilities[capability] !== false;
}
function paintMode() {
  document.querySelectorAll("#modeSwitch button").forEach(b => b.classList.toggle("on", b.dataset.m === S.mode));
  const h = S.health;
  $("#llmInfo").textContent = S.mode === "offline" ? "LLM: not used in offline mode" : "LLM: " + (h ? h.llm : "…");
  $("#ctxInfo").textContent = "Mode: " + MODE_INFO[S.mode].title;
}
function setMode(m) {
  if (m === S.mode) return;
  S.mode = m; localStorage.setItem("esg_mode", m);
  S.report = null; S.capabilities = null; S.job = null; clearTimeout(S.timer); clearTimeout(S.batchTimer);
  paintMode(); loadCodes(); nav(); render();
}
function sourceKindLabel(kind) {
  return ({ live: "live", cache: "cached", bundled: "bundled", static: "bundled" })[kind] || "unavailable";
}
function sourceDate(records) {
  const dates = records.map(x => x.as_of || x.date_max).filter(Boolean).sort();
  return dates.length ? dates[dates.length - 1] : "";
}
function sourceBannerLabel(category, records) {
  const matching = records.filter(x => x.category === category);
  if (!matching.length) return `${category.replace("_", " ")} not recorded`;
  const counts = {};
  matching.forEach(x => { counts[x.kind] = (counts[x.kind] || 0) + 1; });
  const kinds = Object.keys(counts);
  const kindText = matching.every(x => x.status === "failed") ? "failed"
    : kinds.length === 1 ? sourceKindLabel(kinds[0])
      : `mixed (${kinds.map(k => `${sourceKindLabel(k)} ${counts[k]}`).join(", ")})`;
  const date = sourceDate(matching);
  const notes = matching.map(x => x.note || "").join(" ").toLowerCase();
  if (category === "prices") {
    const vendor = matching.some(x => String(x.source).toLowerCase().includes("yfinance")) ? "Yahoo" : "bundled source";
    if (matching.some(x => x.kind === "bundled") && /yfinance|yahoo/.test(notes))
      return "prices bundled (Yahoo failed)";
    return `prices ${kindText} (${vendor}${date ? `, as of ${date}` : ""})`;
  }
  if (category === "news") {
    if (matching.some(x => x.kind === "bundled") && notes.includes("gdelt returned no articles"))
      return "news bundled corpus (GDELT returned no articles)";
    if (matching.some(x => x.kind === "bundled") && notes.includes("gdelt failed"))
      return "news bundled corpus (GDELT failed)";
    if (matching.some(x => x.kind === "bundled"))
      return `news bundled corpus${date ? ` (${date})` : ""}`;
    const failedQueries = matching.filter(x => x.kind === "live" && x.status === "failed").length;
    if (failedQueries) return `news ${kindText} (GDELT; ${failedQueries} failed queries)`;
    return `news ${kindText} (GDELT${date ? `, as of ${date}` : ""})`;
  }
  if (category === "air_quality") {
    const keyMissing = notes.includes("data_gov_in_api_key not set");
    const liveFailed = notes.includes("data.gov.in fetch failed") || notes.includes("data.gov.in returned no rows");
    const source = keyMissing ? "no API key" : liveFailed ? "data.gov.in failed; CPCB snapshot"
      : matching.some(x => x.kind === "live") ? "data.gov.in" : "CPCB snapshot";
    return `air quality ${kindText} (${source}${date ? `, as of ${date}` : ""})`;
  }
  return `${category.replace("_", " ")} ${kindText}`;
}
function modeBannerText(mode, records, llmMode) {
  const sources = Array.isArray(records) ? records : [];
  const labels = ["prices", "news", "air_quality"].map(k => sourceBannerLabel(k, sources));
  if (mode === "offline") return `OFFLINE: ${labels.join(" | ")} | LLM off`;
  const liveSucceeded = sources.some(x => x.kind === "live" && x.status !== "failed" && Number(x.rows) > 0);
  const liveFailed = sources.some(x => x.kind === "live" && x.status === "failed");
  if (liveFailed && !liveSucceeded) labels.push("no live feeds succeeded");
  labels.push(`LLM ${llmMode ? "on" : "off"}`);
  return `DYNAMIC: ${labels.join(" | ")}`;
}
function banner() {
  const records = S.report ? S.report.data_sources : (S.health?.modes?.[S.mode]?.data_sources || []);
  const llmMode = S.report ? S.report.llm_mode : Boolean(S.health?.modes?.[S.mode]?.llm_mode);
  return `<div class="modebar ${S.mode}">${esc(modeBannerText(S.mode, records, llmMode))}</div>`;
}
async function loadCodes() {
  const mode = S.mode;
  try {
    const c = await get(`/api/cohort?mode=${mode}`);
    if (mode !== S.mode) return;
    const oldPage = S.page;
    S.capabilities = c.capabilities || null;
    $("#codes").innerHTML = c.companies.map(r => `<option value="${esc(r.Company_Code)}">${esc(r.Company_Name)}</option>`).join("");
    nav();
    if (oldPage !== S.page) render();
  }
  catch (e) { $("#codes").innerHTML = ""; }
}
function route() { S.page = (location.hash || "#analyze").slice(1); if (!PAGES.find(p => p[0] === S.page)) S.page = "analyze"; nav(); render(); }
window.addEventListener("hashchange", route);

async function openCompany(code, run) {
  try {
    S.report = run ? await get(`/api/report?${q()}&run=${encodeURIComponent(run)}&company=${encodeURIComponent(code)}`) : await get(`/api/company/${encodeURIComponent(code)}?${q()}`);
    S.capabilities = S.report.capabilities || S.capabilities;
    location.hash = "#dashboard"; route();
  }
  catch (e) { alert(`${code} has no ${S.mode.toUpperCase()}-mode results yet.\nRun "Analyze" in ${S.mode} mode (or the ${S.mode} batch) first.\n\n(${e.message})`); }
}
$("#quickBtn").onclick = () => $("#quick").value && openCompany($("#quick").value.trim().toUpperCase());

/* ------------------------------------------------------------ pages ----- */
const need = () => `<div class="card empty">No company loaded yet. <a class="lnk" href="#analyze">Analyze a company</a> or pick one from <a class="lnk" href="#cohort">Cohort Ranking</a>.</div>`;

function groupRuns(runs) {
  const g = {};
  runs.forEach(r => { const k = (r.companies || ["?"])[0]; (g[k] = g[k] || []).push(r); });
  return Object.entries(g).map(([code, list]) => ({ code, latest: list[0], older: list.slice(1) }));
}
async function pAnalyze() {
  const rr = await get(`/api/runs?${q()}`);
  const groups = groupRuns(rr.runs), dyn = S.mode === "dynamic";
  const when = r => esc(String(r.finished || r.run).replace("T", " ").slice(0, 16));
  $("#view").innerHTML = `
  <div class="card"><h3>Analyze a company — ${MODE_INFO[S.mode].title} mode</h3>
    <p class="muted">${MODE_INFO[S.mode].how}</p>
    <p class="muted">Enter a company name or NSE symbol. The agents find the BRSR/XBRL filings, extract and audit the metrics, score, check consistency and explain the result.</p>
    <div class="row" style="margin-top:12px"><input id="co" style="min-width:280px" placeholder="Infosys / INFY / HDFCBANK">
    <input id="hq" placeholder="HQ city — only for a company outside the cohort">
    ${dyn ? `<label><input type="checkbox" id="llm" checked> use LLM (slower)</label>` : ""}
    <button class="primary" id="go">Analyze (${S.mode})</button></div></div>
  <div id="prog"></div>
  <div class="card" style="margin-top:14px"><h3>Previous ${S.mode} analyses <small class="muted">(finished runs only)</small></h3>
    ${groups.length ? `<table><tr><th>Company</th><th>Finished</th><th>Seconds</th><th>Data / LLM</th><th></th></tr>${groups.map(g => `<tr><td><b>${esc(g.code)}</b>${g.older.length ? `<details><summary>+${g.older.length} earlier run(s)</summary>${g.older.map(o => `<div class="muted">${when(o)} · ${num(o.total_seconds, 0)} s · <a class="lnk" data-run="${esc(o.run)}" data-code="${esc(g.code)}">open</a> · <a class="lnk" data-del="${esc(o.run)}">delete</a></div>`).join("")}</details>` : ""}</td>
      <td>${when(g.latest)}</td><td>${num(g.latest.total_seconds, 0)}</td><td class="muted">${g.latest.live_data ? "live data" : "bundled data"} · ${g.latest.llm_used ? "LLM" : "no LLM"}</td>
      <td><a class="lnk" data-run="${esc(g.latest.run)}" data-code="${esc(g.code)}">open</a> · <a class="lnk" data-del="${esc(g.latest.run)}">delete</a></td></tr>`).join("")}</table>` : `<p class="muted">No finished ${S.mode} analyses yet.</p>`}
    ${rr.hidden_unfinished_or_failed ? `<p class="muted">${rr.hidden_unfinished_or_failed} unfinished or failed run(s) are hidden. <a class="lnk" id="clean">Delete them</a></p>` : ""}</div>`;
  document.querySelectorAll("a[data-run]").forEach(a => a.onclick = () => openCompany(a.dataset.code, a.dataset.run));
  document.querySelectorAll("a[data-del]").forEach(a => a.onclick = async () => { if (confirm("Delete this run?")) { await fetch(`/api/runs/${S.mode}/${a.dataset.del}`, { method: "DELETE" }); pAnalyze(); } });
  const cl = $("#clean"); if (cl) cl.onclick = async () => { await post(`/api/runs/cleanup?${q()}`); pAnalyze(); };
  $("#go").onclick = async () => {
    const company = $("#co").value.trim(); if (!company) return;
    $("#go").disabled = true;
    try { const j = await post("/api/analyze", { company, hq_city: $("#hq").value, mode: S.mode, use_llm: dyn ? $("#llm").checked : false }); S.job = j.job_id; poll(); }
    catch (e) { alert(e.message); $("#go").disabled = false; }
  };
  if (S.job) poll();
}
const AGENTS = [["agent0", "Filing discovery"], ["agent1", "BRSR/XBRL extraction"], ["agent2", "Environmental context"], ["agent3", "News sentiment"], ["agent4", "Market performance"], ["agent5", "Consistency / greenwashing"], ["agent6", "ESG scoring"], ["agent7", "Explanation"]];
function poll() {
  clearTimeout(S.timer);
  get(`/api/jobs/${S.job}`).then(j => {
    if (j.mode !== S.mode) return;
    const done = {}; (j.agents_finished || []).forEach(l => { const m = l.match(/\[(agent\d)\] (\w+)/); if (m) done[m[1]] = m[2]; });
    (j.progress?.agents || []).forEach(a => { done[a.id] = a.status; });
    const spans = (j.metrics && j.metrics.spans) || [];
    const dur = id => { const s = spans.find(x => x.source === id && x.kind === "agent"); return s ? num(s.duration, 2) + " s" : ""; };
    const el = $("#prog"); if (!el) return;
    el.innerHTML = `<div class="card" style="margin-top:14px"><h3>Analyzing ${esc(j.company)} (${esc(j.mode)} mode) — ${esc(j.status)}</h3>
      ${AGENTS.map(([id, t]) => { const st = done[id]; const cls = st === "completed" || st === "skipped" ? "ok" : st === "failed" || st === "stalled" || st === "max_steps" ? "bad" : st === "running" ? "run" : (j.status === "running" ? "run" : ""); return `<div class="step"><span class="dot ${cls}"></span><b>${t}</b><span class="muted">${st || (j.status === "running" ? "waiting / running" : "")}</span><span style="margin-left:auto">${dur(id)}</span></div>`; }).join("")}
      <details open><summary>Live log</summary><div class="log">${esc((j.log_tail || []).join("\n"))}</div></details>
      ${j.status === "completed" ? `<p><button class="primary" id="openRes">Open results</button></p>` : ""}
      ${j.status === "failed" ? `<div class="warnbox">The run failed. See the log above. ${esc(j.error || "")}</div>` : ""}</div>`;
    if (j.status === "running") S.timer = setTimeout(poll, 1500);
    else { $("#go") && ($("#go").disabled = false); const b = $("#openRes"); if (b) b.onclick = async () => { const rep = await get(`/api/report?mode=${j.mode}&run=${encodeURIComponent(j.run)}`); S.report = rep; location.hash = "#dashboard"; route(); }; }
  }).catch(() => { });
}

function ring(v) {
  const r = 58, c = 2 * Math.PI * r, p = v == null ? 0 : v / 100, col = v == null ? "var(--line)" : v >= 70 ? "var(--good)" : v >= 50 ? "var(--warn)" : "var(--bad)";
  return `<svg class="ring" viewBox="0 0 150 150"><circle cx="75" cy="75" r="${r}" fill="none" stroke="var(--line)" stroke-width="14"/><circle cx="75" cy="75" r="${r}" fill="none" stroke="${col}" stroke-width="14" stroke-linecap="round" stroke-dasharray="${c * p} ${c}" transform="rotate(-90 75 75)"/><text x="75" y="82" text-anchor="middle" font-size="28" font-weight="700">${v == null ? "—" : num(v, 1)}</text><text x="75" y="102" text-anchor="middle" font-size="11" fill="var(--muted)">/ 100</text></svg>`;
}
function pDashboard() {
  const r = S.report; if (!r) return $("#view").innerHTML = need();
  const s = r.scores, ex = (r.explanation || [])[0] || {};
  const pil = [["Environmental", s.Environmental_Score], ["Social", s.Social_Score], ["Governance", s.Governance_Score]];
  $("#view").innerHTML = `
  <div class="grid g2"><div class="card"><div class="row">${ring(s.Master_ESG_Score)}<div>
    <div class="kpi">${s.Master_ESG_Score == null ? "Score withheld" : num(s.Master_ESG_Score, 1)}<small> master ESG</small></div>
    <p>${riskBadge(s.Risk_Level)} ${badge("evidence: " + s.Quality_Gate_Status, s.Quality_Gate_Status === "Pass" ? "good" : "warn")} ${s.Rank ? badge("rank " + s.Rank + " of " + ((r.cohort_rank || {}).of || "?"), "blue") : ((r.cohort_rank || {}).rank ? badge("batch rank " + r.cohort_rank.rank + "/" + r.cohort_rank.of, "blue") : badge("rank n/a (single run)", "blue"))}</p>
    <p class="muted">${s.Usable_Metrics}/${s.Scored_Metrics_Total ?? 16} scored metrics trusted</p></div></div></div>
  <div class="card"><h3>Pillars</h3>${pil.map(([n, v]) => `<div style="margin:8px 0"><div class="row" style="justify-content:space-between"><b>${n}</b><span>${v == null ? "no reliable BRSR evidence" : num(v, 1)}</span></div>${bar(v || 0)}</div>`).join("")}</div></div>
  <div class="card" style="margin-top:14px"><h3>Why this score? ${ex.Narrative_Source ? badge(ex.Narrative_Source, String(ex.Narrative_Source).startsWith("llm") ? "good" : "blue") : ""} ${ex.Grounding_Check ? badge("grounding: " + ex.Grounding_Check, ex.Grounding_Check === "pass" ? "good" : "warn") : ""}</h3>
    <div class="expl">${cite(ex.Score_Explanation || "No explanation available.")}</div>
    ${ex.LLM_Note ? `<p class="muted">${esc(ex.LLM_Note)}</p>` : ""}
    ${ex.Recommendations ? `<h3 style="margin-top:14px">Suggested actions</h3><ul>${ex.Recommendations.split(" | ").map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    <p class="muted">Bracketed IDs (e.g. [M1]) point to rows in <a class="lnk" href="#sources">Sources &amp; Evidence</a>.</p></div>`;
}
function pPerformance() {
  const r = S.report; if (!r) return $("#view").innerHTML = need();
  const tr = Object.fromEntries((r.trends || []).filter(t => t.Comparable !== false && t.Comparable !== "False").map(t => [t.Metric_Name, t]));
  const chg = t => { if (t.Change_Value == null || t.Change_Value === "") return num(t.Change_Pct, 1) + " %"; const v = Number(t.Change_Value), sg = v > 0 ? "+" : ""; return t.Change_Mode === "pp" ? sg + num(v, 2) + " pp" : t.Change_Mode === "abs" ? sg + num(v, 3) + (t.Unit ? " " + t.Unit : "") : sg + num(v, 1) + " %"; };
  const arrow = m => { const t = tr[m]; if (!t) return ""; const w = t.Direction === "improved" ? ["▲ better", "good"] : t.Direction === "worsened" ? ["▼ worse", "bad"] : ["→ flat", "muted"]; return `<span style="color:var(--${w[1]})">${w[0]}</span><div class="muted">${chg(t)}${t.Base_Small === true || t.Base_Small === "True" ? " · tiny base" : ""}</div>`; };
  const block = (p, title) => { const rows = (r.metrics || []).filter(m => m.Pillar === p).sort((a, b) => b.Score_0_10 - a.Score_0_10); return `<div class="card"><h3>${title}</h3>${rows.length ? `<table><tr><th>Metric</th><th>Value</th><th>Score</th><th>YoY</th><th>Source</th></tr>${rows.map(m => `<tr><td>${esc(m.Metric_Name)}</td><td>${num(m.Value, 2)} <span class="muted">${esc(m.Unit)}</span></td><td>${num(m.Score_0_10 * 10, 0)}${bar(m.Score_0_10 * 10)}</td><td>${arrow(m.Metric_Name)}</td><td class="muted">${esc(m.Source)} · conf ${num(m.Confidence, 2)}<br>anchors ${esc(m.Anchor_0)} → ${esc(m.Anchor_10)}</td></tr>`).join("")}</table>` : `<p class="muted">No trusted BRSR evidence for this pillar yet (see Sources &amp; Evidence for what was quarantined).</p>`}</div>`; };
  $("#view").innerHTML = `<div class="grid">${block("Environmental", "Environmental")}${block("Social", "Social")}${block("Governance", "Governance")}</div>
  <div class="card" style="margin-top:14px"><h3>Year-over-year trends (from prior-year values in the same XBRL)</h3>${(r.trends || []).length ? `<table><tr><th>Metric</th><th>Prior</th><th>Current</th><th>Change</th><th></th></tr>${r.trends.map(t => `<tr><td>${esc(t.Metric_Name)}</td><td>${num(t.Prior, 2)}</td><td>${num(t.Current, 2)}</td><td>${chg(t)}${t.Base_Small === true || t.Base_Small === "True" ? ' <span class="muted">(tiny base: judged by absolute change)</span>' : ""}</td><td>${(t.Comparable === false || t.Comparable === "False") ? badge("not comparable", "warn") : badge(t.Direction, t.Direction === "improved" ? "good" : t.Direction === "worsened" ? "bad" : "blue")}</td></tr>`).join("")}</table>` : `<p class="muted">No prior-year XBRL values available.</p>`}</div>`;
}
function pMarket() {
  const r = S.report; if (!r) return $("#view").innerHTML = need();
  const m = (r.market || [])[0];
  if (!m || m.Coverage !== "price_data") return $("#view").innerHTML = `<div class="card empty">${S.mode === "offline" ? "The bundled offline price file has no history for this company (it covers only some companies). Switch to <b>Dynamic</b> mode to request live prices; available fallback sources are labeled in Sources &amp; Evidence." : "The live price fetch returned nothing for this company. " + esc(m && m.Error || "")}</div>`;
  const items = [["1M", m.Return_1M_Pct], ["3M", m.Return_3M_Pct], ["6M", m.Return_6M_Pct], ["1Y", m.Return_1Y_Pct], ["3Y", m.Return_3Y_Pct], ["FY24-25", m.FY_Return_Pct]];
  const mx = Math.max(20, ...items.map(x => Math.abs(x[1] || 0)));
  const dbar = (v, max) => { const w = Math.min(50, Math.abs(v || 0) / max * 50), pos = (v || 0) >= 0; return `<div class="dbar"><i style="left:${pos ? 50 : 50 - w}%;width:${w}%;background:var(--${pos ? "good" : "bad"})"></i></div>`; };
  $("#view").innerHTML = `<div class="grid g4">${items.map(([k, v]) => `<div class="card"><span class="muted">${k} return</span><div class="kpi" style="color:var(--${v >= 0 ? "good" : "bad"})">${num(v, 1)}%</div></div>`).join("")}
  <div class="card"><span class="muted">Volatility</span><div class="kpi">${num(m.Volatility_Pct, 1)}%</div></div><div class="card"><span class="muted">Max drawdown</span><div class="kpi">${num(m.Max_Drawdown_Pct, 1)}%</div></div>
  <div class="card"><span class="muted">Market score (vs cohort)</span><div class="kpi">${num(m.Stock_Performance_Score, 1)}<small>/10</small></div></div></div>
  <div class="card" style="margin-top:14px"><h3>Return profile</h3>${items.map(([k, v]) => `<div class="row"><span style="width:60px">${k}</span><div style="flex:1">${dbar(v, mx)}</div><span style="width:70px;text-align:right">${num(v, 1)}%</span></div>`).join("")}
  <p class="muted">Source: ${esc(m.Data_Source)} · window end ${esc(m.Window_End)} · ${m.Data_Points} data points · ${m.Window_Policy === "capped_to_offline_history" ? "window aligned to offline history" : "latest available window"}. ESG-vs-market relationships are statistical associations, not causation (see Validation &amp; Research).</p></div>`;
}
function newsFetchSummary(report) {
  const sources = report.data_sources || [];
  const source = sources.find(x => x.key === "news:corpus") || sources.find(x => x.category === "news");
  const history = (report.agents || {}).agent3?.history || [];
  const readStep = tool => {
    const entry = history.find(x => x.tool === tool);
    if (!entry) return {};
    try { return JSON.parse(entry.observation || "{}"); }
    catch (error) {
      if (error instanceof SyntaxError) return {};
      throw error;
    }
  };
  const loaded = readStep("load_corpus"), attributed = readStep("attribute_articles");
  const scored = readStep("score_articles");
  if (!source) return "No news-source provenance was recorded for this run.";
  if (source.status === "failed" && !(source.rows > 0))
    return `News fetch failed: ${source.note || "no articles were returned"}`;
  const sourceName = ({ live: "GDELT live", cache: "GDELT cache", bundled: "Bundled news corpus",
    static: "Bundled news corpus" })[source.kind] || "News source";
  const rows = loaded.articles ?? source.rows;
  const range = source.date_min || source.date_max
    ? ` (${source.date_min || "?"} to ${source.date_max || "?"})` : "";
  const result = `${sourceName} returned ${rows ?? "an unknown number of"} raw articles${range}.`;
  const note = source.note ? ` ${source.note}` : "";
  const filters = [];
  if (attributed.attributed_rows !== undefined)
    filters.push(`${attributed.attributed_rows} passed company attribution`);
  if (scored.esg_relevant !== undefined)
    filters.push(`${scored.esg_relevant} passed ESG relevance screening`);
  if (attributed.rejected_roundups > 0)
    filters.push(`${attributed.rejected_roundups} roundup headlines were excluded`);
  if (filters.length) return `${result} ${filters.join("; ")}.${note}`;
  return `${result}${note}`;
}
function pNews() {
  const r = S.report; if (!r) return $("#view").innerHTML = need();
  const n = (r.news_summary || [])[0] || {}, arts = r.news_articles || [];
  const fetchSummary = newsFetchSummary(r);
  if (!arts.length && !(n.General_Articles > 0)) return $("#view").innerHTML =
    `<div class="card empty"><b>No ESG-relevant articles retained.</b><p class="muted">${esc(fetchSummary)}</p></div>`;
  const pos = arts.filter(a => a.Sentiment_Class === "Positive").length, neg = arts.filter(a => a.Sentiment_Class === "Negative").length, neu = arts.length - pos - neg, t = Math.max(arts.length, 1);
  $("#view").innerHTML = `<div class="grid g4"><div class="card"><span class="muted">ESG articles</span><div class="kpi">${n.Articles ?? 0}</div></div>
   <div class="card"><span class="muted">Positive / Neutral / Negative</span><div class="kpi" style="font-size:20px">${Math.round(100 * pos / t)}% · ${Math.round(100 * neu / t)}% · ${Math.round(100 * neg / t)}%</div></div>
   <div class="card"><span class="muted">E / S / G events</span><div class="kpi" style="font-size:20px">${n.Environmental_Events ?? 0} / ${n.Social_Events ?? 0} / ${n.Governance_Events ?? 0}</div></div>
   <div class="card"><span class="muted">Reliability</span><div class="kpi">${num(n.Sentiment_Reliability, 2)}</div><span class="muted">${esc(n.Corpus_Source || "")}</span></div></div>
  <div class="card" style="margin-top:14px"><h3>Articles</h3>${arts.length ? `<table><tr><th>Date</th><th>Headline</th><th>Category</th><th>Sentiment</th></tr>${arts.map(a => `<tr><td>${esc(String(a.Article_Date || "").slice(0, 10))}</td><td>${a.URL ? `<a class="lnk" href="${esc(a.URL)}" target="_blank" rel="noopener">${esc(a.Article_Title)}</a>` : esc(a.Article_Title)}<div class="muted">${esc(a.Domain || "")}</div></td><td>${esc(a.ESG_Category || "")}</td><td>${badge(a.Sentiment_Class, a.Sentiment_Class === "Positive" ? "good" : a.Sentiment_Class === "Negative" ? "bad" : "blue")} ${num(a.Sentiment_Score, 2)}</td></tr>`).join("")}</table>` : `<p class="muted">No ESG-relevant articles retained.</p><p class="muted">${esc(fetchSummary)}</p>`}
   <p class="muted">Scores use sentence-level sentiment shrunk toward neutral by sample size; small samples are not trusted.</p></div>`;
}
function pEnv() {
  const r = S.report; if (!r) return $("#view").innerHTML = need();
  const a = r.air || [];
  $("#view").innerHTML = `<div class="card"><h3>Operating locations — air-quality context</h3>${a.length ? `<table><tr><th>Location</th><th>Type</th><th>Avg AQI</th><th>Category</th><th>Evidence</th><th>Confidence</th></tr>${a.map(x => `<tr><td>${esc(x.City)}${x.Area ? " · " + esc(x.Area) : ""}</td><td>${esc(x.Location_Type)}</td><td>${num(x.Avg_AQI, 0)}</td><td>${esc(x.Risk_Level || "unresolved")}</td><td class="muted">${esc(x.Match_Method)}${x.Matched_City ? " → " + esc(x.Matched_City) : ""}<br>${esc(x.AQI_Source || "")}</td><td>${num(x.Evidence_Confidence, 2)}</td></tr>`).join("")}</table>` : `<p class="muted">No locations.</p>`}
  <div class="warnbox">Air quality reflects the whole location (traffic, industry, weather). It is shown as <b>context</b> for the company's environmental performance, never as proof of it.</div></div>`;
}
function pGreen() {
  const r = S.report; if (!r) return $("#view").innerHTML = need();
  const c = (r.consistency || [])[0] || {}, claims = r.claims || [], sig = r.signals || [];
  const vb = v => badge(String(v).replace(/_/g, " "), v === "consistent" ? "good" : v === "insufficient_evidence" ? "warn" : "bad");
  const claimCount = c.Claims_Investigated ?? claims.length;
  const ids = text => String(text || "").split(",").map(x => x.trim()).filter(Boolean);
  const evidenceList = x => String(x.Evidence || "").split(" || ").filter(Boolean).map(item => {
    const match = item.match(/^(E\d+)\s+\[([^\]]+)\]\s*(.*)$/);
    if (!match) return `<li>${esc(item)}</li>`;
    const [, id, kind, text] = match, ruleCites = ids(x.Cited_Evidence).includes(id);
    const llmCites = ids(x.LLM_Cited_Evidence).includes(id);
    const citedBy = [ruleCites ? "rule" : "", llmCites ? "LLM" : ""].filter(Boolean).join(" + ");
    return `<li>${badge(id, ruleCites || llmCites ? "blue" : "warn")} <span class="muted">${esc(kind)}${citedBy ? ` · cited by ${esc(citedBy)}` : " · not cited"}</span><br>${esc(text)}</li>`;
  }).join("");
  $("#view").innerHTML = `<div class="card"><div class="row"><div class="kpi">${esc(c.Risk_Level || "n/a")}<small> consistency risk</small></div>${c.Trust_Score != null ? badge(`trust ${num(c.Trust_Score, 1)}/10 on ${claimCount} claim${Number(claimCount) === 1 ? "" : "s"}`, "blue") : ""}</div>
  <p class="muted">Trust is reduced only when a claim has the verdict potential inconsistency. Confidence and rationale are informational and do not change the score.</p>
  <p class="muted">Signals indicate potential inconsistencies between disclosures and independent evidence. They are not accusations.</p></div>
  <div class="card" style="margin-top:14px"><h3>Claim → evidence → verdict</h3>${claims.length ? `<table><tr><th>Claim</th><th>Rule verdict</th><th>LLM verdict</th><th>Agreement</th><th>Evidence list (citations highlighted)</th></tr>${claims.map(x => {
    const hasLlm = x.LLM_Verdict != null && x.LLM_Verdict !== "";
    return `<tr><td>${esc(x.Claim)}<div class="muted">${esc(x.Claim_Type)}</div></td>
      <td>${vb(x.Verdict)}<div class="muted">confidence ${num(x.Confidence, 2)} · cites ${esc(x.Cited_Evidence || "none")}</div><div class="muted">${esc(x.Rationale || "")}</div></td>
      <td>${hasLlm ? `${vb(x.LLM_Verdict)}<div class="muted">confidence ${num(x.LLM_Confidence, 2)} · cites ${esc(x.LLM_Cited_Evidence || "none")}</div><div class="muted">${esc(x.LLM_Rationale || "")}</div>` : `<span class="muted">not evaluated</span>`}</td>
      <td>${!hasLlm || x.Verdict_Agreement == null ? "—" : badge(x.Verdict_Agreement ? "agree" : "disagree", x.Verdict_Agreement ? "good" : "warn")}</td>
      <td class="muted"><ul>${evidenceList(x)}</ul>${esc(x.Caveat || "")}</td></tr>`;
  }).join("")}</table>` : `<p class="muted">No claims to test (no comparable prior-year improvements or achievement statements found).</p>`}</div>
  <div class="card" style="margin-top:14px"><h3>Signals</h3>${c.Signal_Detail ? `<ul>${String(c.Signal_Detail).split(" | ").map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="muted">No inconsistency signals.</p>`}</div>`;
}
function pAgents() {
  const r = S.report; if (!r) return $("#view").innerHTML = need();
  const ag = r.agents || {};
  const llmUsage = llmUsageLabel((r.run_metrics || {}).llm_usage);
  $("#view").innerHTML = `${llmUsage ? `<p class="muted">${esc(llmUsage)}</p>` : ""}
  <div class="card"><b>Decision legend:</b> policy = the agent's fixed rule chose the step; LLM = the language model proposed the step and code checked it; guardrail = code rejected an invalid LLM choice (unknown tool, wrong order, company outside the run, repeated failure, or early finish) and used the agent's own step.</div>
  <div class="grid g2">${Object.entries(ag).map(([id, a]) => `<div class="card"><h3>${esc(a.name)}</h3>
    <p>${badge(a.status, a.status === "completed" ? "good" : "bad")} ${num(a.seconds, 2)} s · ${a.steps} steps · planner ${esc(a.planner_mode || (a.llm ? "on" : "off"))}</p>
    <p class="muted">${esc(a.summary)}</p><p>Decisions: ${Object.entries(a.decisions || {}).map(([k, v]) => `${k}: ${v}`).join(" · ")}</p>
    <p class="muted">LLM planning: ${a.llm_planning_decisions || 0} accepted decisions / ${a.llm_planning_calls || 0} calls · judgements: ${a.llm_judgements || 0} · budget: ${esc(a.budget_reason || "none")} · planning ${num(a.llm_planning_seconds, 2)} s / judging ${num(a.llm_judging_seconds, 2)} s</p>
    ${(a.findings || []).slice(0, 3).map(f => `<div class="warnbox">${esc(f.message)}</div>`).join("")}
    ${(a.guardrail_rejections || []).map(g => `<div class="warnbox"><b>Rejected proposal at step ${g.step}:</b> ${esc(g.proposal?.tool || "unknown")} ${esc(JSON.stringify(g.proposal?.args || {}))}<br>${esc(g.reason)}</div>`).join("")}
    <details><summary>Decision trace (last ${(a.history || []).length})</summary><table>${(a.history || []).map(h => `<tr><td>${h.step}</td><td><b>${esc(h.tool)}</b> <span class="muted">${esc(JSON.stringify(h.args)).slice(0, 80)}</span><br><span class="muted">[${esc(h.source)}] ${esc(String(h.observation).slice(0, 160))}</span></td></tr>`).join("")}</table></details></div>`).join("") || `<div class="card empty">No agent traces in this run.</div>`}</div>`;
}
function parallelGainLabel(wall, sum) {
  const wallSeconds = Number(wall), sumSeconds = Number(sum);
  if (!Number.isFinite(wallSeconds) || !Number.isFinite(sumSeconds)) return "—";
  const gain = Math.max(0, sumSeconds - wallSeconds);
  return gain < 0.05 ? "sequential" : `${num(gain, 2)} s`;
}
function timingBreakdown(summary) {
  const stages = Object.entries(summary.stages || {});
  const endToEnd = Number(summary.total_seconds || 0);
  const stageWallTotal = stages.reduce((total, [, value]) => total + Number(value.stage_wall_seconds || 0), 0);
  return { stages, endToEnd, stageWallTotal, overhead: endToEnd - stageWallTotal };
}
function pTiming() {
  const r = S.report; if (!r) return $("#view").innerHTML = need();
  const m = r.run_metrics; if (!m) return $("#view").innerHTML = `<div class="card empty">No timing data for this run.</div>`;
  const s = m.summary, spans = m.spans || [];
  const { stages, endToEnd, overhead } = timingBreakdown(s);
  $("#view").innerHTML = `<div class="grid g4"><div class="card" title="Total wait time from run start to completion."><span class="muted">End-to-end</span><div class="kpi">${num(endToEnd, 2)} s</div><div class="muted">Total wait time from run start to completion.</div></div>
  <div class="card" title="Slowest agent in the longest stage."><span class="muted">Bottleneck</span><div class="kpi" style="font-size:16px">${s.bottleneck ? esc(s.bottleneck.name) + "<br>" + num(s.bottleneck.seconds, 2) + " s" : "—"}</div><div class="muted">Slowest agent in the longest stage.</div></div>
  <div class="card" title="A requested live source failed and a substitute was used; static data chosen in offline mode is not counted."><span class="muted">Fallbacks used</span><div class="kpi">${(s.fallbacks_used || []).length}</div><div class="muted">A requested live source failed and a substitute was used; offline static sources are not counted.</div></div>
  <div class="card" title="An external fetch failed, whether or not a fallback was available."><span class="muted">Failures</span><div class="kpi">${(s.failures || []).length}</div><div class="muted">An external fetch failed, whether or not a fallback was available.</div></div></div>
  <div class="card" style="margin-top:14px"><h3>Stages (wall-clock vs total work)</h3><table><tr><th>Stage</th><th>Wall</th><th>Sum of parts</th><th>Parallel gain</th></tr>${stages.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${num(v.stage_wall_seconds, 2)} s</td><td>${num(v.sum_seconds, 2)} s</td><td>${parallelGainLabel(v.stage_wall_seconds, v.sum_seconds)}</td></tr>`).join("")}<tr><th>Overhead (startup, scheduling, writing files)</th><td>${num(overhead, 2)} s</td><td>—</td><td>—</td></tr><tr><th>End-to-end</th><th>${num(endToEnd, 2)} s</th><td>—</td><td>—</td></tr></table></div>
  <div class="card" style="margin-top:14px"><h3>Every step</h3><table><tr><th>Step</th><th>Source</th><th>Status</th><th>Seconds</th><th>Fallback</th></tr>${spans.map(x => {
    const help = [x.error ? `Failure: ${x.error}` : "", x.fallback_used ? `Fallback used: ${x.source}` : ""].filter(Boolean).join(" · ");
    return `<tr title="${esc(help)}"><td>${esc(x.name)}</td><td>${esc(x.source)}</td><td>${badge(x.status, x.status === "success" ? "good" : "bad")}</td><td>${num(x.duration, 2)}</td><td>${x.fallback_used ? "yes" : ""}</td></tr>`;
  }).join("")}</table></div>`;
}
function pSources() {
  const r = S.report; if (!r) return $("#view").innerHTML = need();
  const ev = r.evidence_package || {}, f = r.filings;
  const src = [["BRSR PDF", f && Object.values(f.companies || {})[0]?.valid?.pdf], ["XBRL", f && Object.values(f.companies || {})[0]?.valid?.xbrl], ["Market", (r.market || [])[0]?.Coverage === "price_data"], ["News", (r.news_summary || [])[0]?.Articles > 0], ["Air quality", (r.air || []).some(a => a.Avg_AQI != null)]];
  const dataSources = r.data_sources || [];
  $("#view").innerHTML = `<div class="card"><h3>Data sources</h3><div class="row">${src.map(([n, ok]) => `<span>${badge(n + (ok === undefined ? " ?" : ok ? " ✓" : " ✗"), ok ? "good" : ok === undefined ? "blue" : "warn")}</span>`).join("")}</div>
  ${dataSources.length ? `<table><tr><th>Feed</th><th>Source</th><th>Kind</th><th>Date / range</th><th>Rows used</th><th>Note</th></tr>${dataSources.map(x => {
    const range = x.as_of || (x.date_min || x.date_max ? `${x.date_min || "?"} – ${x.date_max || "?"}` : "—");
    return `<tr><td>${esc(x.category || "source")}</td><td>${esc(x.source || "—")}</td><td>${badge(sourceKindLabel(x.kind), x.kind === "live" ? "good" : x.kind === "cache" ? "blue" : "warn")}</td><td>${esc(range)}</td><td>${x.rows ?? "—"}</td><td class="muted">${esc(x.note || "")}</td></tr>`;
  }).join("")}</table>` : `<p class="muted">Source provenance was not recorded for this run.</p>`}
  <p class="muted">Market: ${esc((r.market || [])[0]?.Data_Source || "n/a")} · News corpus: ${esc((r.news_summary || [])[0]?.Corpus_Source || "n/a")}</p></div>
  <div class="card" style="margin-top:14px"><h3>BRSR metrics — provenance</h3><table><tr><th>Metric</th><th>Value</th><th>Unit</th><th>Source</th><th>Status</th><th>Confidence</th><th>Audit note</th></tr>${(r.brsr || []).map(x => `<tr><td>${esc(x.Metric_Name)}</td><td>${x.Metric_Value == null ? "—" : num(x.Metric_Value, 2)}</td><td>${esc(x.Unit)}</td><td class="muted">${esc(x.Final_Source)}</td><td>${badge(x.Validation_Status || "n/a", x.Validation_Status === "VALID" ? "good" : x.Validation_Status === "QUARANTINED" ? "bad" : "warn")}</td><td>${num(x.Confidence_Score, 2)}</td><td class="muted">${esc(String(x.Audit_Note || "").slice(0, 120))}</td></tr>`).join("")}</table></div>
  <div class="card" style="margin-top:14px"><h3>Evidence package given to the Explanation Agent</h3><details><summary>show JSON</summary><div class="log">${esc(JSON.stringify(ev, null, 1))}</div></details></div>`;
}
async function pValidation() {
  const v = await get(`/api/validation?${q()}`), a = (v.summary || {}).analyses || {};
  if (!v.summary) return $("#view").innerHTML = `<div class="card empty">No ${S.mode} batch has been run yet, so there is nothing to validate.<br>Run <code>python run_agents.py --mode ${S.mode}</code> (or use Cohort Ranking → Run ${S.mode} batch).</div>`;
  const names = { nse_top200: "Our ESG vs NSE Top 200", nse_cfa: "Our BRSR metrics vs NSE/CFA research", esg_stock: "ESG ↔ Stock", esg_news: "ESG ↔ News", esg_air: "ESG ↔ Air quality (context)", sensitivity: "Score sensitivity to anchors and weights" };
  const tbl = rows => rows && rows.length ? `<div style="overflow:auto"><table><tr>${Object.keys(rows[0]).map(k => `<th>${esc(k)}</th>`).join("")}</tr>${rows.map(r => `<tr>${Object.values(r).map(x => `<td>${esc(typeof x === "number" ? num(x, 3) : x)}</td>`).join("")}</tr>`).join("")}</table></div>` : "";
  const files = { nse_top200: "validation_nse_top200.csv", nse_cfa: "validation_nse_cfa.csv", esg_stock: "validation_esg_stock.csv", esg_news: "validation_esg_news.csv", esg_air: "validation_esg_air.csv", sensitivity: null };
  $("#view").innerHTML = `<div class="warnbox">${esc((v.summary || {}).caveat || "Run the batch pipeline first: python run_agents.py")}</div><div class="grid g2">${Object.entries(names).map(([k, t]) => { const x = a[k] || {}; return `<div class="card"><h3>${t}</h3><p>${badge(x.status || "not run", x.status === "ok" ? "good" : "warn")}</p>${x.how ? `<p class="muted">${esc(x.how)}</p>` : ""}${x.reading ? `<p>${esc(x.reading)}</p>` : ""}${((v.summary || {}).interpretations || {})[k] ? `<p class="muted"><b>Reading (${esc(v.summary.interpretations[k].source)}):</b> ${esc(v.summary.interpretations[k].text)}</p>` : ""}${x.caveat ? `<p class="muted">${esc(x.caveat)}</p>` : ""}${k === "sensitivity" && x.status === "ok" ? `<table><tr><th>Trials</th><th>Spearman median</th><th>5th pct</th><th>Mean rank shift</th><th>Band unchanged</th></tr><tr><td>${x.trials}</td><td>${num(x.spearman_vs_baseline_median, 3)}</td><td>${num(x.spearman_vs_baseline_p5, 3)}</td><td>${num(x.mean_abs_rank_shift_median, 2)}</td><td>${num(x.risk_band_unchanged_median, 2)}</td></tr></table>` : tbl(v.tables[files[k]])}</div>`; }).join("")}</div>
  <div class="card" style="margin-top:14px"><h3>Benchmark files</h3><p>nse_top200_sector.csv: ${v.benchmark_files_present["nse_top200_sector.csv"] ? badge("present", "good") : badge("missing", "warn")} · nse_top200_companies.csv: ${v.benchmark_files_present["nse_top200_companies.csv"] ? badge("present", "good") : badge("missing", "warn")} · nse_cfa_reference.csv: ${v.benchmark_files_present["nse_cfa_reference.csv"] ? badge("present", "good") : badge("missing", "warn")}</p><p class="muted">Built by <code>python tools/build_benchmarks.py --top200-pdf &lt;report&gt;.pdf</code> (see docs/VALIDATION.md); the NSE report publishes sector statistics, not per-company scores.</p></div>`;
}
async function batchClick() {
  if (S.mode === "dynamic" && !confirm("A dynamic batch fetches live data for every company and can take a long time. It will run WITHOUT the LLM (use the terminal for an LLM batch). Start it?")) return;
  const button = $("#runBatch"); if (button) button.disabled = true;
  const status = $("#batchStatus");
  if (status) status.innerHTML = `<p class="muted">Starting ${S.mode} batch. Existing published results remain available until the new batch completes.</p>`;
  try {
    const j = await post(`/api/batch/run?${q()}&use_llm=false`);
    pollBatch(j.job_id, S.mode);
  } catch (e) {
    if (status) status.innerHTML = `<div class="warnbox">Could not start the batch: ${esc(e.message)}. Existing published results are unchanged.</div>`;
    if (button) button.disabled = false;
  }
}
function pollBatch(jobId, mode) {
  clearTimeout(S.batchTimer);
  get(`/api/jobs/${jobId}`).then(async j => {
    if (mode !== S.mode) return;
    const status = $("#batchStatus");
    if (status) {
      const progress = j.progress || {};
      const agents = (progress.agents || []).map(a => {
        const cls = a.status === "completed" || a.status === "skipped" ? "ok"
          : a.status === "failed" || a.status === "stalled" || a.status === "max_steps" ? "bad"
          : a.status === "running" ? "run" : "";
        return `<div class="step"><span class="dot ${cls}"></span><b>${esc(a.id)}</b><span class="muted">${esc(a.status)}</span></div>`;
      }).join("");
      const message = j.status === "running"
        ? `${esc(j.company)} is running (${progress.completed || 0}/${progress.total || 0} agents complete). Existing published results remain available until the batch completes.`
        : j.status === "completed"
          ? `${esc(j.company)} completed and the new batch is published.${j.warning ? ` ${esc(j.warning)}` : ""}`
          : `${esc(j.company)} failed. Existing published results were kept. ${esc(j.error || "")}`;
      status.innerHTML = `<div class="card"><div class="${j.status === "failed" ? "warnbox" : "muted"}">${message}</div>${agents}</div>`;
    }
    if (j.status === "running") {
      S.batchTimer = setTimeout(() => pollBatch(jobId, mode), 1500);
    } else {
      const button = $("#runBatch"); if (button) button.disabled = false;
      if (j.status === "completed" && S.page === "cohort") {
        await pCohort();
        const refreshedStatus = $("#batchStatus");
        if (refreshedStatus) refreshedStatus.innerHTML = `<div class="muted">${esc(j.company)} completed and the new batch is published.${j.warning ? ` ${esc(j.warning)}` : ""}</div>`;
      }
    }
  }).catch(() => {
    if (mode === S.mode) S.batchTimer = setTimeout(() => pollBatch(jobId, mode), 3000);
  });
}
async function pCohort() {
  const batch = await get(`/api/cohort?${q()}`), rows = batch.companies || [];
  const llmUsage = llmUsageLabel(batch.llm_usage);
  const agreementNote = `<div class="warnbox">Offline and Dynamic results are separate and are compared only for matched companies. Dynamic market returns use the matching ticker's offline snapshot end date when available; run <code>python tools/evaluate_mode_agreement.py</code> after both batches to check score and market-window agreement.</div>`;
  if (!rows.length) return $("#view").innerHTML = `<div id="batchStatus"></div>${agreementNote}<div class="card empty">No ${S.mode} batch results yet.<br><button class="primary" id="runBatch" style="margin-top:10px">Run ${S.mode} batch now</button><p class="muted">Or in a terminal: <code>python run_agents.py --mode ${S.mode}</code>${S.mode === "dynamic" ? " (slow: live data for every company)" : ""}</p>${llmUsage ? `<p class="muted">${esc(llmUsage)}</p>` : ""}</div>`, $("#runBatch").onclick = batchClick;
  $("#view").innerHTML = `<div id="batchStatus"></div>${agreementNote}${llmUsage ? `<p class="muted">${esc(llmUsage)}</p>` : ""}<div class="card"><div class="row" style="justify-content:space-between"><h3>${S.mode.toUpperCase()} batch results (${rows.length} companies)</h3><button id="runBatch">Re-run ${S.mode} batch</button></div>
  <table><tr><th>Rank</th><th>Company</th><th>Master</th><th>E</th><th>S</th><th>G</th><th>Risk</th><th>Evidence</th></tr>${rows.map(r => `<tr><td>${r.Rank ?? "—"}</td><td><a class="lnk" data-c="${esc(r.Company_Code)}">${esc(r.Company_Name)}</a></td><td>${num(r.Master_ESG_Score, 1)}</td><td>${num(r.Environmental_Score, 0)}</td><td>${num(r.Social_Score, 0)}</td><td>${num(r.Governance_Score, 0)}</td><td>${riskBadge(r.Risk_Level)}</td><td>${esc(r.Quality_Gate_Status)}</td></tr>`).join("")}</table></div>`;
  document.querySelectorAll("a[data-c]").forEach(a => a.onclick = () => openCompany(a.dataset.c, "batch"));
  $("#runBatch").onclick = batchClick;
  $("#codes").innerHTML = rows.map(r => `<option value="${esc(r.Company_Code)}">${esc(r.Company_Name)}</option>`).join("");
}
function render() {
  const f = { analyze: pAnalyze, dashboard: pDashboard, performance: pPerformance, market: pMarket, news: pNews, env: pEnv, greenwashing: pGreen, agents: pAgents, timing: pTiming, sources: pSources, validation: pValidation, cohort: pCohort }[S.page];
  Promise.resolve(f()).catch(e => { $("#view").innerHTML = `<div class="card empty">Error: ${esc(e.message)}</div>`; })
    .finally(() => $("#view").insertAdjacentHTML("afterbegin", banner()));
}
document.querySelectorAll("#modeSwitch button").forEach(b => b.onclick = () => setMode(b.dataset.m));
get("/api/health").then(h => { S.health = h; paintMode(); }).catch(() => { });
paintMode(); loadCodes();
route();
