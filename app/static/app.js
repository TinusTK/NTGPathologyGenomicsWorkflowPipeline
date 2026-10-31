// NTG pipeline UI. Plain JS, no build step. Talks to the JSON API in app/server.py.
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false)
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
};
const api = async (path, opts = {}) => {
  const r = await fetch("/api/" + path, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};
const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
const clone = (x) => JSON.parse(JSON.stringify(x));
let toastTimer;
function toast(msg, ms = 4000) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
}
function modal(content) {
  const m = $("#modal"), box = $("#modal-box");
  put(box, content); m.classList.remove("hidden");
  return () => m.classList.add("hidden");
}
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") e.target.classList.add("hidden"); });
const warnBox = (w) => h("div", { class: "warn " + w.level },
  h("b", {}, w.level === "error" ? "Problem:" : w.level === "warning" ? "Warning:" : "Note:"),
  w.step ? h("span", { class: "mono" }, `[${w.step}] `) : null, w.message);

// ------------------------------------------------------------------ state
const S = {
  page: "workflows", hw: null, tools: {}, workflows: [],
  wfId: null, wfVersion: null, wf: null, galaxy: null,
  editing: false, draft: null, selected: null, dirty: false,
  runTimer: null,
  usageTimer: null,
  usage: null,
};

async function boot() {
  try { localStorage.getItem("x"); } catch { /* storage blocked: fine */ }
  document.querySelectorAll("#nav a").forEach((a) => a.addEventListener("click", () => go(a.dataset.page)));
  window.addEventListener("beforeunload", (e) => { if (S.editing && S.dirty) { e.preventDefault(); e.returnValue = ""; } });
  await Promise.all([loadTools(), loadHardware(), loadWorkflows()]);
  try { S.galaxy = await api("workflows/rdh_v5_2"); } catch { S.galaxy = null; }
  const start = (location.hash || "#workflows").slice(1);
  go(start.split("/")[0] || "workflows", start.split("/")[1]);
  checkUpdates().catch(() => {}); // quietly, once per start; the badge shows if there's a newer version
  checkToolUpdates().catch(() => {});
}
// Tool updates: the server checks once a day by itself; the Tools menu shows a badge.
async function checkToolUpdates() {
  S.toolUpdates = await api("tools/updates");
  const link = $('#nav a[data-page="tools"]');
  link.querySelector(".badge")?.remove();
  const n = S.toolUpdates.updates.length;
  if (n) link.append(" ", h("span", { class: "badge run", title: `${n} tool(s) have a newer version` }, String(n)));
  return S.toolUpdates;
}
// The tool a step uses: the version it picked, else the tool's default version.
function stepTool(st) {
  const base = S.tools[st.tool];
  if (!base || !st.tool_version || String(st.tool_version) === String(base.version)) return base || null;
  return (base.variants || {})[st.tool_version] || null;
}
async function checkUpdates(fetch = true) {
  S.updates = await api("updates" + (fetch ? "" : "?fetch=0"));
  const link = $('#nav a[data-page="system"]');
  link.querySelector(".badge")?.remove();
  if ((S.updates.new || []).length) link.append(" ", h("span", { class: "badge run", title: "A newer version of the pipeline is on GitHub" }, "update"));
  return S.updates;
}
async function loadTools() { S.tools = Object.fromEntries((await api("tools")).map((t) => [t.id, t])); }
async function loadWorkflows() { S.workflows = await api("workflows"); }
async function loadHardware() {
  S.hw = await api("hardware");
  showUsage(S.usage);
  if (!S.usageTimer) { pollUsage(); S.usageTimer = setInterval(pollUsage, 2000); }
}
// Top bar: live CPU use, clock speed and RAM, every 2 s while the tab is visible.
async function pollUsage() {
  if (document.hidden) return;
  try { S.usage = await api("usage"); showUsage(S.usage); } catch { /* UI restarting: keep the last values */ }
}
// Green while there's room, amber from 70% used, red from 90%.
const level = (pct) => pct == null ? "" : pct >= 90 ? "lvl lvl-err" : pct >= 70 ? "lvl lvl-warn" : "lvl lvl-ok";
function showUsage(u) {
  const hw = S.hw;
  const parts = [`${hw.cpus} CPUs`];
  if (u) {
    if (u.cpu_percent != null) parts.push(h("span", { class: level(u.cpu_percent), title: "CPU in use" }, `CPU ${Math.round(u.cpu_percent)}%`));
    if (u.cpu_ghz != null) parts.push(`${u.cpu_ghz.toFixed(2)} GHz${u.cpu_ghz_kind === "base" ? " (base)" : ""}`);
    parts.push(h("span", { class: level(u.memory_gb ? 100 * u.memory_used_gb / u.memory_gb : null), title: "RAM in use" },
      `RAM ${u.memory_used_gb.toFixed(1)} / ${Math.round(u.memory_gb)} GB`));
    const usedPct = u.disk_total_gb && u.disk_free_gb != null ? 100 * (1 - u.disk_free_gb / u.disk_total_gb) : null;
    parts.push(h("span", { class: level(usedPct), title: "Free space on the disk that holds runs and work files" }, `${u.disk_free_gb ?? "?"} GB free storage`));
  } else {
    parts.push(`${Math.round(hw.memory_gb)} GB RAM`, `${hw.disk_free_gb.work ?? "?"} GB free storage`);
  }
  parts.push(h("span", { class: hw.docker ? "" : "lvl lvl-err" }, `Docker ${hw.docker ? hw.docker : "NOT RUNNING"}`));
  const chip = $("#hw-chip");
  put(chip, parts.flatMap((p, i) => i ? [" · ", p] : [p]));
  chip.title = u && u.cpu_ghz_kind === "base" ? "This computer (live clock speed not readable here: showing the base clock)" : "This computer (updates every 2 seconds)";
}

function go(page, arg) {
  if (S.editing && S.dirty && page !== "workflows" && !confirm("You have unsaved workflow changes. Leave anyway?")) return;
  S.page = page;
  clearInterval(S.runTimer);
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === page));
  history.replaceState(null, "", "#" + page + (arg ? "/" + arg : ""));
  const pages = { workflows: pageWorkflows, run: pageRun, runs: pageRuns, tools: pageTools, databases: pageDatabases, system: pageSystem };
  (pages[page] || pageWorkflows)(arg);
}

// ------------------------------------------------------------------ workflow helpers
function sourceLabel(src, wf) {
  if (src === "input") return "raw reads";
  const [sid, out] = src.split(".");
  const st = wf.steps[sid];
  return `${st ? (st.label || sid) : sid} → ${out}`;
}
// Which steps run, given steps switched off: mirrors the compiler's rules.
function runnable(wf, off = new Set()) {
  const ok = {};
  const order = topo(wf);
  for (const sid of order) {
    const st = wf.steps[sid];
    const tool = stepTool(st);
    if (!tool || off.has(sid) || st.enabled === false) { ok[sid] = false; continue; }
    let good = true;
    for (const [name, spec] of Object.entries(tool.inputs || {})) {
      const srcs = (st.inputs || {})[name] || [];
      if (spec.optional) continue;
      if (!srcs.some((s) => s === "input" || ok[s.split(".")[0]])) good = false;
    }
    ok[sid] = good;
  }
  return ok;
}
function topo(wf) {
  const seen = new Set(), out = [];
  const visit = (sid) => {
    if (seen.has(sid) || !wf.steps[sid]) return; seen.add(sid);
    for (const srcs of Object.values(wf.steps[sid].inputs || {}))
      for (const s of srcs) if (s !== "input") visit(s.split(".")[0]);
    out.push(sid);
  };
  Object.keys(wf.steps).forEach(visit);
  return out;
}
function dependents(wf, sid) {
  return Object.entries(wf.steps).filter(([id, st]) => id !== sid &&
    Object.values(st.inputs || {}).some((srcs) => srcs.some((s) => s.split(".")[0] === sid))).map(([id]) => id);
}
function rawWorkflow(view) {
  // strip the UI-only fields so the workflow can be saved
  const wf = clone(view);
  for (const k of ["versions", "error", "locked", "builtin", "_file", "_version"]) delete wf[k];
  for (const st of Object.values(wf.steps)) { delete st.tool_info; delete st.resolved_settings; }
  return wf;
}

// ------------------------------------------------------------------ graph
// Left to right: column = longest path from the reads, steps stacked in each column.
const COL_W = 230, ROW_H = 60;
function layeredPositions(wf) {
  const level = { input: 0 };
  for (const sid of topo(wf)) {
    let l = 1;
    for (const srcs of Object.values(wf.steps[sid].inputs || {}))
      for (const s of srcs) if (s !== "input" && level[s.split(".")[0]] !== undefined) l = Math.max(l, level[s.split(".")[0]] + 1);
    level[sid] = l;
  }
  const cols = {};
  for (const [id, l] of Object.entries(level)) (cols[l] = cols[l] || []).push(id);
  const tallest = Math.max(...Object.values(cols).map((c) => c.length));
  const pos = {};
  for (const [l, ids] of Object.entries(cols))
    ids.forEach((id, i) => { pos[id] = { x: +l * COL_W, y: (i - (ids.length - 1) / 2) * ROW_H + (tallest * ROW_H) / 2 }; });
  return pos;
}
// Saved positions (workflow `layout`, from dragging boxes while editing) win;
// steps without one, e.g. newly added, get the layered position.
function graphPositions(wf) {
  const pos = layeredPositions(wf), saved = wf.layout || {};
  for (const id of Object.keys(pos)) if (saved[id]) pos[id] = { x: saved[id].x, y: saved[id].y };
  return pos;
}
function graphHeight(wf) {
  const pos = Object.values(graphPositions(wf));
  const ys = pos.map((p) => p.y);
  return Math.max(260, Math.max(...ys) - Math.min(...ys) + 100);
}
function drawGraph(container, wf, opts = {}) {
  container.style.height = graphHeight(wf) + "px";
  const status = opts.status || {};
  const off = opts.off || new Set();
  const willRun = runnable(wf, off);
  const els = [{ data: { id: "input", label: "Paired reads\n(input)" }, classes: "input" }];
  for (const [sid, st] of Object.entries(wf.steps)) {
    const tool = stepTool(st);
    const label = `${st.label || sid}\n${tool ? tool.name + " " + (tool.version || "") : "MISSING TOOL: " + st.tool}`;
    const cls = [!tool ? "missing" : "", willRun[sid] ? "" : "off", status[sid] || ""].join(" ");
    els.push({ data: { id: sid, label }, classes: cls });
    for (const [name, srcs] of Object.entries(st.inputs || {})) {
      srcs.forEach((src, i) => {
        const from = src === "input" ? "input" : src.split(".")[0];
        if (from === "input" && srcs.length > 1) return; // raw-read fallback: shown in the panel, not as an edge
        if (from !== "input" && !wf.steps[from]) return;
        els.push({ data: { id: `${from}->${sid}:${name}:${i}`, source: from, target: sid }, classes: i > 0 ? "fallback" : "" });
      });
    }
  }
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  const cy = cytoscape({
    container, elements: els, boxSelectionEnabled: false, minZoom: 0.3, maxZoom: 2, autoungrabify: !opts.onMove,
    style: [
      { selector: "node", style: {
        shape: "round-rectangle", width: 190, height: 46, label: "data(label)", "text-wrap": "wrap",
        "text-valign": "center", "font-size": 11, color: v("--text"), "background-color": v("--node"),
        "border-width": 1.5, "border-color": v("--border") } },
      { selector: "node.input", style: { "background-color": v("--accent-2"), "border-color": v("--accent") } },
      { selector: "node.off", style: { opacity: 0.4, "border-style": "dashed" } },
      { selector: "node.missing", style: { "border-color": v("--err"), "border-width": 2 } },
      { selector: "node.running", style: { "border-color": v("--run"), "border-width": 3 } },
      { selector: "node.done", style: { "border-color": v("--ok"), "border-width": 3 } },
      { selector: "node.failed", style: { "border-color": v("--err"), "border-width": 3, "background-color": v("--err-bg") } },
      { selector: "node:selected", style: { "border-color": v("--accent"), "border-width": 3 } },
      { selector: "edge", style: { width: 1.5, "line-color": v("--edge"), "target-arrow-color": v("--edge"),
        "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.9 } },
      { selector: "edge.fallback", style: { "line-style": "dashed" } },
    ],
    layout: { name: "preset", positions: graphPositions(wf), padding: 16 },
  });
  // fit again once the page has its final size, so no box is cut off at the edges
  requestAnimationFrame(() => { cy.resize(); cy.fit(undefined, 16); });
  if (opts.onSelect) cy.on("tap", "node", (e) => opts.onSelect(e.target.id()));
  // after a drag, hand back every box's position so the whole picture is kept
  if (opts.onMove) cy.on("dragfree", "node", () => opts.onMove(Object.fromEntries(cy.nodes().map((n) =>
    [n.id(), { x: Math.round(n.position("x")), y: Math.round(n.position("y")) }]))));
  if (opts.selected) cy.$id(opts.selected).select();
  return cy;
}

// ------------------------------------------------------------------ Workflows page
async function pageWorkflows(arg) {
  if (arg && !S.editing) { const [id, ver] = arg.split("@"); S.wfId = id; S.wfVersion = ver ? +ver : null; }
  if (!S.wfId) S.wfId = (S.workflows[0] || {}).id;
  if (!S.editing || !S.wf) S.wf = await api(`workflows/${S.wfId}` + (S.wfVersion ? `?version=${S.wfVersion}` : ""));
  if (S.page !== "workflows") return; // user moved on while this loaded
  renderWorkflows();
}

function current() { return S.editing ? S.draft : S.wf; }

// Two warnings: deleting removes every saved version and can't be undone.
async function deleteWorkflow() {
  const wf = S.wf, n = (wf.versions || []).length;
  if (!confirm(`Delete the workflow "${wf.name}" and all ${n} saved version${n > 1 ? "s" : ""}?`)) return;
  if (!confirm(`Are you sure? "${wf.name}" will be gone for good: it can't be brought back.\n\nPast runs of it keep their results (each run folder has its own copy of the workflow).`)) return;
  try {
    await api("workflows/" + wf.id, { method: "DELETE" });
    toast(`Deleted ${wf.name}`);
    await loadWorkflows();
    S.wfId = null; S.wfVersion = null; S.wf = null; S.selected = null;
    await pageWorkflows();
  } catch (e) { toast("Not deleted: " + e.message, 8000); }
}

function renderWorkflows() {
  const wf = current();
  const main = $("#main");
  const wfSelect = h("select", { disabled: S.editing, onchange: async (e) => { S.wfId = e.target.value; S.wfVersion = null; S.selected = null; await pageWorkflows(); } },
    S.workflows.map((w) => h("option", { value: w.id, selected: w.id === S.wfId }, `${w.name}${w.builtin ? "  (built-in)" : ""}`)));
  const verSelect = h("select", { disabled: S.editing, title: "Load a saved version",
    onchange: async (e) => { S.wfVersion = +e.target.value; await pageWorkflows(); } },
    (S.wf.versions || [S.wf.version]).slice().reverse().map((v) => h("option", { value: v, selected: v === (S.wf._version || S.wf.version) }, `v${v}`)));

  const bar = h("div", { class: "card row" },
    h("div", {}, h("div", { class: "muted small" }, "Workflow"), wfSelect),
    h("div", {}, h("div", { class: "muted small" }, "Version"), verSelect),
    S.editing ? h("span", { class: "badge edit", title: "Changes are saved as a new version; nothing is overwritten." }, "EDITING (unsaved draft)")
      : h("span", { class: "badge lock", title: "Read-only: nothing can be changed or deleted. Use Edit to make a copy." }, "🔒 READ-ONLY"),
    h("div", { class: "spacer" }),
    S.editing ? [
      h("button", { onclick: addStepDialog }, "+ Add step"),
      h("button", { title: "Forget where the boxes were moved to and line them up automatically again",
        onclick: () => { if (S.draft.layout) { delete S.draft.layout; S.dirty = true; } renderWorkflows(); } }, "Reset layout"),
      h("button", { onclick: () => { if (!S.dirty || confirm("Discard your changes?")) { S.editing = false; S.draft = null; S.dirty = false; renderWorkflows(); } } }, "Cancel"),
      !S.wf.builtin ? h("button", { class: "primary", onclick: () => saveDraft(false) }, `Save as v${Math.max(...S.wf.versions) + 1}`) : null,
      h("button", { class: S.wf.builtin ? "primary" : "", onclick: () => saveDraft(true) }, "Save as new workflow…"),
    ] : [
      h("button", { onclick: () => { S.editing = true; S.draft = rawWorkflow(S.wf); S.draft._view = S.wf; S.dirty = false; renderWorkflows(); },
        title: S.wf.builtin ? "Built-in workflows can't change; you'll save your edits as a new workflow." : "Edit a copy; Save makes a new version." }, "✎ Edit"),
      h("button", { class: "primary", onclick: () => { S.runPreset = { id: S.wf.id, version: S.wf._version || S.wf.version }; go("run"); } }, "▶ Run this workflow"),
      S.wf.builtin ? null : h("button", { class: "danger", title: "Delete this workflow and all its saved versions", onclick: deleteWorkflow }, "Delete workflow"),
    ]);

  const info = h("div", { class: "card" },
    h("h1", {}, wf.name),
    h("div", { class: "muted" }, wf.description || ""),
    wf.extends ? h("div", { class: "small muted" }, `Extends: ${wf.extends}`) : null,
    S.wf.error ? warnBox({ level: "error", message: S.wf.error }) : null,
    S.wf.builtin && !S.editing ? h("div", { class: "small muted" }, "Built-in workflow: always read-only. Edit it to save your own copy under a new name.") : null);

  const graph = h("div", { id: "graph" });
  const panel = h("div", { class: "card step-panel", id: "step-panel" });
  put(main, bar, info, h("div", { class: "grid2" }, h("div", {}, graph,
    h("div", { class: "small muted", style: "margin-top:6px" }, "Click a step to see its tool, version, container and settings. Dashed boxes are switched off."
      + (S.editing ? " Drag the boxes to arrange them; the layout is saved with the workflow." : ""))), panel));
  drawGraph(graph, wf, { selected: S.selected, onSelect: (sid) => { S.selected = sid; renderStepPanel(); },
    onMove: S.editing ? (pos) => { S.draft.layout = pos; S.dirty = true; } : null });
  renderStepPanel();
}

function galaxyValue(sid, tool, name) {
  const g = S.galaxy && S.galaxy.steps[sid];
  if (!g || g.tool !== tool) return undefined;
  if (g.galaxy_values && name in g.galaxy_values) return g.galaxy_values[name]; // where we deliberately differ
  return g.resolved_settings[name] ? g.resolved_settings[name].value : undefined;
}

function renderStepPanel() {
  const panel = $("#step-panel"); if (!panel) return;
  const wf = current();
  const sid = S.selected;
  if (!sid || sid === "input") {
    put(panel, h("h2", {}, sid === "input" ? "Input: paired reads" : "Steps"),
      sid === "input" ? h("div", {}, h("p", {}, (wf.input || {}).description || "Paired Illumina reads."),
        h("div", { class: "kv" }, h("div", {}, "File pattern"), h("div", { class: "mono" }, (wf.input || {}).pattern)))
        : h("div", {}, topo(wf).map((s) => h("div", {}, h("a", { onclick: () => { S.selected = s; renderWorkflows(); } }, wf.steps[s].label || s),
          h("span", { class: "muted small" }, `  ${(S.tools[wf.steps[s].tool] || {}).name || wf.steps[s].tool}`)))));
    return;
  }
  const st = wf.steps[sid];
  const tool = stepTool(st);
  const ed = S.editing;
  const kids = [h("div", { class: "row" }, h("h2", { style: "margin:0" }, st.label || sid), h("div", { class: "spacer" }),
    ed ? h("button", { class: "danger", onclick: () => removeStep(sid) }, "Remove step") : null)];
  if (ed) kids.push(h("div", { class: "row small" }, "Label ", h("input", { type: "text", value: st.label || "", oninput: (e) => { st.label = e.target.value; S.dirty = true; } })));
  const base = S.tools[st.tool];
  if (!tool && base) {
    kids.push(warnBox({ level: "error", message: `${base.name} version ${st.tool_version} is not installed any more. ${ed ? "Pick another version:" : "Edit the workflow to pick another version, or add it again on the Tools page."}` }));
    if (ed) kids.push(versionPicker(st, base));
    put(panel, ...kids); return;
  }
  if (!tool) { kids.push(warnBox({ level: "error", message: `Tool '${st.tool}' is not installed. Install its tool pack on the Tools page.` })); put(panel, ...kids); return; }
  kids.push(h("p", { class: "muted" }, tool.description || ""),
    h("div", { class: "kv" },
      h("div", {}, "Tool"), h("div", {}, tool.name),
      h("div", {}, "Version"), h("div", {}, ed && base.versions.length > 1 ? versionPicker(st, base)
        : [`${tool.version || ""}`, versionTags(base.versions.find((v) => v.version === String(tool.version)) || {}),
          base.versions.length > 1 && !ed ? h("span", { class: "muted small" }, `  (${base.versions.length} installed; Edit to change)`) : null]),
      h("div", {}, "Container"), h("div", { class: "mono small" }, tool.container),
      h("div", {}, "Runs"), h("div", {}, tool.scope === "all_samples" ? "once, on all samples together" : "once per sample"),
      h("div", {}, "Source"), h("div", {}, tool._source === "built-in" ? "built-in" : `tool pack ${tool._source}` + (tool.source ? ` @ ${String(tool.source.commit).slice(0, 10)}` : "")),
      st.galaxy_step !== undefined ? [h("div", {}, "Galaxy step"), h("div", {}, String(st.galaxy_step))] : null,
      tool.homepage ? [h("div", {}, "Homepage"), h("div", {}, h("a", { href: tool.homepage, target: "_blank", rel: "noopener" }, tool.homepage))] : null,
      tool.license ? [h("div", {}, "License"), h("div", {}, tool.license)] : null),
    tool.requirements && tool.requirements.note ? h("div", { class: "warn info small" }, tool.requirements.note) : null,
    st.galaxy_note ? h("div", { class: "warn warning small" }, st.galaxy_note) : null,
    h("h3", {}, "Inputs"),
    h("div", {}, Object.entries(tool.inputs || {}).map(([name, spec]) => {
      const srcs = (st.inputs || {})[name] || [];
      if (!ed) return h("div", { class: "small" }, h("b", {}, name), ": ", srcs.length ? srcs.map((s) => sourceLabel(s, wf)).join(", else ") : h("span", { class: "muted" }, "not connected"));
      return h("div", { class: "small row" }, h("b", {}, name), sourceSelect(wf, sid, name, spec, srcs));
    })),
    h("h3", {}, "Settings"));
  const settings = Object.entries(tool.settings || {});
  if (!settings.length) kids.push(h("div", { class: "muted small" }, "No settings."));
  st.settings = st.settings || {};
  for (const [name, spec] of settings) kids.push(settingRow(sid, st, tool, name, spec, ed));
  put(panel, ...kids);
}

// Small labels after a version: built-in / installed, untested.
function versionTags(v) {
  return [v.default ? h("span", { class: "badge gray", title: "The version this tool came with" }, "original") : null,
    v.untested ? h("span", { class: "badge err", title: "Added on the Tools page; no successful run with it yet" }, "untested") : null]
    .filter(Boolean).flatMap((b) => [" ", b]);
}
function versionPicker(st, base) {
  const cur = String(st.tool_version || base.version);
  return h("select", { onchange: (e) => {
    if (e.target.value === String(base.version)) delete st.tool_version; else st.tool_version = e.target.value;
    S.dirty = true; renderWorkflows();
  } }, base.versions.map((v) => h("option", { value: v.version, selected: v.version === cur },
    `${v.version}${v.default ? " (original)" : ""}${v.untested ? " (untested)" : ""}`)),
    base.versions.some((v) => v.version === cur) ? null : h("option", { value: cur, selected: true, disabled: true }, `${cur} (not installed)`));
}

function sourceSelect(wf, sid, name, spec, srcs) {
  const options = [];
  if (spec.type === "reads" || spec.type === "any") options.push(["input", "raw reads"]);
  for (const [osid, ost] of Object.entries(wf.steps)) {
    if (osid === sid || dependsOn(wf, osid, sid)) continue;
    const ot = stepTool(ost); if (!ot) continue;
    for (const [oname, ospec] of Object.entries(ot.outputs || {})) {
      const compatible = spec.type === "any" || (spec.type === "reads") === (ospec.type === "reads");
      if (compatible) options.push([`${osid}.${oname}`, `${ost.label || osid} → ${oname}`]);
    }
  }
  const sel = h("select", { onchange: (e) => {
    const v = e.target.value;
    wf.steps[sid].inputs = wf.steps[sid].inputs || {};
    wf.steps[sid].inputs[name] = v ? (v !== "input" && spec.type === "reads" ? [v, "input"] : [v]) : [];
    S.dirty = true; renderWorkflows();
  } }, h("option", { value: "" }, spec.optional ? "(not connected)" : "— choose —"),
    options.map(([v, l]) => h("option", { value: v, selected: srcs[0] === v }, l)));
  return h("span", {}, sel, srcs.length > 1 ? h("span", { class: "muted" }, " (else raw reads)") : null);
}
function dependsOn(wf, a, b) { // does step a (transitively) use step b?
  const seen = new Set();
  const walk = (x) => {
    if (x === b) return true; if (seen.has(x) || !wf.steps[x]) return false; seen.add(x);
    return Object.values(wf.steps[x].inputs || {}).some((srcs) => srcs.some((s) => s !== "input" && walk(s.split(".")[0])));
  };
  return walk(a);
}

function settingRow(sid, st, tool, name, spec, ed) {
  const value = name in st.settings ? st.settings[name] : spec.default;
  const gv = galaxyValue(sid, tool.id, name);
  const set = (v) => { st.settings[name] = v; S.dirty = true; };
  const isAuto = spec.auto && String(value) === String(spec.auto.value);
  let input;
  const disabled = !ed || isAuto;
  if (spec.type === "boolean") {
    input = h("label", { class: "switch" }, h("input", { type: "checkbox", checked: value === true || value === "true", disabled: !ed,
      onchange: (e) => set(e.target.checked) }), value === true || value === "true" ? "On" : "Off");
  } else if (spec.type === "select") {
    input = h("select", { disabled, onchange: (e) => set(e.target.value) },
      (spec.options || []).map((o) => h("option", { value: o, selected: String(o) === String(value) }, o)));
  } else {
    input = h("input", { type: spec.type === "integer" || spec.type === "number" ? "number" : "text",
      step: spec.type === "number" ? "any" : "1", value: isAuto ? "" : value ?? "", disabled, placeholder: isAuto ? spec.auto.label : "",
      oninput: (e) => set(spec.type === "integer" ? parseInt(e.target.value, 10) : spec.type === "number" ? parseFloat(e.target.value) : e.target.value) });
  }
  const autoToggle = spec.auto ? h("label", { class: "switch small", title: "Let the tool decide" },
    h("input", { type: "checkbox", checked: isAuto, disabled: !ed, onchange: (e) => {
      if (e.target.checked) set(spec.auto.value);
      else set(spec.type === "select" ? (spec.options || []).find((o) => o !== spec.auto.value) : spec.default !== spec.auto.value ? spec.default : "");
      renderStepPanel();
    } }), `Auto${isAuto ? ": " + spec.auto.label : ""}`) : null;
  const galaxy = gv === undefined ? null : h("span", { class: "galaxy" + (String(gv) !== String(value) ? " diff" : "") },
    String(gv) !== String(value) ? `Galaxy RDH v5.2: ${fmt(gv)} (changed)` : "same as Galaxy");
  return h("div", { class: "setting" },
    h("div", { class: "head" }, h("b", {}, spec.label || name), h("span", { class: "mono muted small" }, name), galaxy),
    h("div", { class: "desc" }, spec.description || ""),
    h("div", { class: "control" }, input, autoToggle,
      h("span", { class: "muted small" }, `default ${fmt(spec.default)}`)));
}
const fmt = (v) => v === "" ? "(blank)" : typeof v === "boolean" ? (v ? "on" : "off") : String(v);

function removeStep(sid) {
  const wf = S.draft;
  const deps = dependents(wf, sid);
  if (deps.length && !confirm(`These steps use "${wf.steps[sid].label || sid}": ${deps.join(", ")}.\nRemove it anyway? Their inputs from it will be disconnected.`)) return;
  delete wf.steps[sid];
  for (const st of Object.values(wf.steps))
    for (const [k, srcs] of Object.entries(st.inputs || {})) st.inputs[k] = srcs.filter((s) => s.split(".")[0] !== sid);
  S.selected = null; S.dirty = true; renderWorkflows();
}

function addStepDialog() {
  const tools = Object.values(S.tools).sort((a, b) => (a.category || "").localeCompare(b.category || "") || a.name.localeCompare(b.name));
  let close;
  const list = h("div", {}, tools.map((t) => h("div", { class: "setting" },
    h("div", { class: "head" }, h("b", {}, `${t.name} ${t.version || ""}`), h("span", { class: "muted small" }, t.category || ""),
      h("div", { class: "spacer" }), h("button", { onclick: () => { addStep(t); close(); } }, "Add")),
    h("div", { class: "desc" }, t.description || ""))));
  close = modal(h("div", {}, h("h2", {}, "Add a step"), h("p", { class: "muted small" }, "Pick a tool. You can connect its inputs in the step panel. More tools: Tools page → install a tool pack."), list));
}
function addStep(tool) {
  const wf = S.draft;
  let sid = tool.id, i = 2;
  while (wf.steps[sid]) sid = `${tool.id}_${i++}`;
  const inputs = {};
  for (const [name, spec] of Object.entries(tool.inputs || {})) {
    if (spec.type === "reads") inputs[name] = wf.steps.fastp ? ["fastp.reads", "input"] : ["input"];
    else if (spec.type === "fasta" && wf.steps.shovill) inputs[name] = ["shovill.contigs"];
  }
  wf.steps[sid] = { tool: tool.id, label: tool.name, inputs, settings: {} };
  S.selected = sid; S.dirty = true; renderWorkflows();
}

async function saveDraft(asNew) {
  const wf = rawWorkflow(S.draft); delete wf._view;
  let newId = null;
  if (asNew) {
    const name = prompt("Name for the new workflow:", S.wf.builtin ? `${S.wf.name} (my copy)` : wf.name);
    if (!name) return;
    newId = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "w_$1").slice(0, 50) || "my_workflow";
    wf.name = name;
    delete wf.extends;
    wf.based_on = { id: S.wf.id, version: S.wf._version || S.wf.version };
  }
  try {
    const r = await api("workflows/save", { body: { workflow: wf, new_id: newId } });
    toast(`Saved ${r.id} v${r.version}`);
    S.editing = false; S.draft = null; S.dirty = false;
    await loadWorkflows();
    S.wfId = r.id; S.wfVersion = r.version; S.selected = null;
    await pageWorkflows();
  } catch (e) { toast("Not saved: " + e.message, 8000); }
}

// ------------------------------------------------------------------ Run page
async function pageRun() {
  const preset = S.runPreset || { id: S.wfId || (S.workflows[0] || {}).id };
  S.runPreset = null;
  const main = $("#main");
  const state = { id: preset.id, version: preset.version, input: S.lastInput || "", off: new Set(), cleanup: true, wf: null,
    pairing: S.lastPairing || { mode: "auto", r1: "_R1", r2: "_R2" } };
  const wfSel = h("select", { onchange: async (e) => { state.id = e.target.value; state.version = null; await loadWf(); } },
    S.workflows.map((w) => h("option", { value: w.id, selected: w.id === state.id }, w.name)));
  const verSel = h("select", { onchange: async (e) => { state.version = +e.target.value; await loadWf(); } });
  let typing;
  const recheck = () => { clearTimeout(typing); typing = setTimeout(check, 400); };
  const input = h("input", { type: "text", class: "wide", value: state.input, placeholder: "/path/to/reads folder", oninput: (e) => { state.input = e.target.value; recheck(); } });
  const pairBox = h("div", {});
  const marker = (k) => h("input", { type: "text", class: "mono", style: "width:7em", value: state.pairing[k],
    oninput: (e) => { state.pairing[k] = e.target.value; recheck(); } });
  const customBox = h("span", { class: "row", style: "gap:6px" }, "R1 marker", marker("r1"), "R2 marker", marker("r2"));
  const showCustom = () => { customBox.style.display = state.pairing.mode === "custom" ? "" : "none"; };
  const pairMode = h("select", { onchange: (e) => { state.pairing.mode = e.target.value; showCustom(); check(); } },
    h("option", { value: "auto", selected: state.pairing.mode === "auto" }, "Auto-detect"),
    h("option", { value: "custom", selected: state.pairing.mode === "custom" }, "Custom"));
  showCustom();
  const name = h("input", { type: "text", class: "wide", placeholder: "optional, e.g. 2026-10-29 run 1" });
  const cleanup = h("input", { type: "checkbox", checked: true, onchange: (e) => { state.cleanup = e.target.checked; } });
  const stepsBox = h("div", { class: "steps-list" });
  const checkBox = h("div", {});
  const startBtn = h("button", { class: "primary", onclick: start }, "▶ Start run");
  put(main, 
    h("h1", {}, "Run a workflow"),
    h("div", { class: "card" },
      h("div", { class: "row" }, h("div", {}, h("div", { class: "muted small" }, "Workflow"), wfSel), h("div", {}, h("div", { class: "muted small" }, "Version"), verSel)),
      h("h3", {}, "Reads folder"),
      h("div", { class: "row" }, h("div", { style: "flex:1" }, input), h("button", { onclick: () => browse(state.input, (p) => { input.value = state.input = p; check(); }) }, "Browse…"), h("button", { onclick: check }, "Check")),
      h("div", { class: "small muted" }, "A folder with <sample>_R1…gz and <sample>_R2…gz files. Under WSL, Windows drives are at /mnt/c, /mnt/d …"),
      h("h3", {}, "Read pairing"),
      h("div", { class: "row" }, pairMode, customBox),
      h("div", { class: "small muted" }, "Auto tries _R1/_R2, .R1/.R2, _1/_2 and .1/.2 and keeps the one that pairs the most files. Custom: the text that marks read 1 and read 2 in your file names. The sample name is everything before the marker. Check the pairs below before starting: the run uses exactly these (saved as samples.csv in the run folder)."),
      pairBox,
      h("h3", {}, "Run name"), name,
      h("h3", {}, "Intermediate files"),
      h("label", { class: "switch" }, cleanup, h("b", {}, "Clean up after a successful run")),
      h("div", { class: "small muted" }, "On: Nextflow's work folder (every step's intermediate files) is deleted when the run succeeds, to save disk space. Final results are always kept. Off: everything is kept for traceability, and a stopped run can be resumed. The choice is saved in the run's provenance.")),
    h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "Steps for this run"),
      h("div", { class: "small muted" }, "Switch a step off to skip it and everything that needs its output. This only changes this run, never the saved workflow."), stepsBox),
    h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "Checks for this computer"), checkBox, h("div", { class: "row", style: "margin-top:10px" }, startBtn)));

  async function loadWf() {
    state.wf = await api(`workflows/${state.id}` + (state.version ? `?version=${state.version}` : ""));
    state.version = state.wf._version || state.wf.version;
    put(verSel, ...(state.wf.versions || [state.version]).slice().reverse().map((v) => h("option", { value: v, selected: v === state.version }, `v${v}`)));
    state.off = new Set(); renderSteps(); check();
  }
  function renderSteps() {
    const ok = runnable(state.wf, state.off);
    put(stepsBox, ...topo(state.wf).map((sid) => {
      const st = state.wf.steps[sid];
      const skipped = !ok[sid] && !state.off.has(sid);
      return h("label", {}, h("input", { type: "checkbox", checked: !state.off.has(sid), onchange: (e) => { e.target.checked ? state.off.delete(sid) : state.off.add(sid); renderSteps(); check(); } }),
        h("span", { class: ok[sid] ? "" : "skipped" }, st.label || sid),
        h("span", { class: "muted small" }, stepTool(st) ? `${stepTool(st).name} ${stepTool(st).version || ""}${stepTool(st).untested ? " (untested)" : ""}` : `missing tool ${st.tool}${st.tool_version ? " " + st.tool_version : ""}`),
        skipped ? h("span", { class: "badge gray" }, "skipped: needs a step that is off") : null);
    }));
  }
  let checkSeq = 0;
  async function check() {
    if (!state.wf) return;
    const seq = ++checkSeq;
    put(checkBox, h("div", { class: "muted" }, "Checking…"));
    const wf = rawWorkflow(state.wf);
    const r = await api("workflows/check", { body: { workflow: wf, input: state.input || null, steps_off: [...state.off], pairing: state.pairing } }).catch((e) => ({ warnings: [{ level: "error", message: e.message }], samples: [] }));
    if (seq !== checkSeq) return;
    const errors = r.warnings.filter((w) => w.level === "error");
    startBtn.disabled = errors.length > 0 || !state.input || !r.samples.length;
    renderPairs(r.pairing);
    put(checkBox,
      r.samples.length ? h("div", {}, h("b", {}, `${r.samples.length} sample(s) found: `), r.samples.map((s) => s.sample).join(", ")) : null,
      r.warnings.length ? r.warnings.map(warnBox) : h("div", { class: "warn info" }, "No problems found for this computer."),
      !state.input ? h("div", { class: "muted small" }, "Pick a reads folder to check the samples.") : null);
  }
  // Live view of how the reads were paired, so the user can see every pair before starting.
  function renderPairs(p) {
    if (!p) return put(pairBox);
    const base = (f) => f ? f.split("/").pop() : null;
    const counts = {};
    p.samples.forEach((s) => { counts[s.sample] = (counts[s.sample] || 0) + 1; });
    put(pairBox,
      h("div", { class: "small", style: "margin:8px 0 4px" }, h("b", {}, `${p.samples.filter((s) => s.r2).length} pair(s)`),
        ` matched on ${p.r1} / ${p.r2}`, p.mode === "auto" ? " (auto-detected)" : " (custom)",
        p.unpaired.length ? h("span", { style: "color:var(--err)" }, ` · ${p.unpaired.length} file(s) not paired`) : null),
      p.samples.length || p.unpaired.length ? h("div", { class: "table-wrap", style: "max-height:300px" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Sample"), h("th", {}, "Read 1"), h("th", {}, "Read 2"))),
        h("tbody", {},
          p.samples.map((s) => h("tr", {},
            h("td", { style: counts[s.sample] > 1 ? "color:var(--err)" : "" }, s.sample, counts[s.sample] > 1 ? " (duplicate name)" : ""),
            h("td", { class: "mono small", style: "word-break:break-all" }, base(s.r1)),
            h("td", { class: "mono small", style: "word-break:break-all" + (s.r2 ? "" : ";color:var(--err)") }, base(s.r2) || `✗ no matching ${p.r2} file`))),
          p.unpaired.map((f) => h("tr", {},
            h("td", { class: "muted" }, "not paired"), h("td", { class: "mono small", colspan: 2, style: "word-break:break-all;color:var(--err)" }, f)))))) : null);
  }
  async function start() {
    startBtn.disabled = true;
    try {
      S.lastInput = state.input;
      S.lastPairing = { ...state.pairing };
      const run = await api("runs", { body: { workflow: state.id, version: state.version, input: state.input, name: name.value || null, cleanup: state.cleanup, steps_off: [...state.off], pairing: state.pairing } });
      toast("Run started");
      go("runs", run.id);
    } catch (e) { toast("Could not start: " + e.message, 8000); startBtn.disabled = false; }
  }
  await loadWf();
}

async function browse(start, pick) {
  let close;
  const body = h("div", {});
  const show = async (path) => {
    const r = await api("browse?path=" + encodeURIComponent(path || ""));
    put(body, 
      h("div", { class: "mono small" }, r.path),
      h("div", { class: "row", style: "margin:8px 0" }, h("button", { onclick: () => show(r.parent) }, "↑ Up"),
        h("button", { class: "primary", onclick: () => { pick(r.path); close(); } }, "Use this folder"),
        h("span", { class: "muted small" }, `${r.reads.length} .gz file(s) here`)),
      h("div", { style: "max-height:50vh;overflow:auto" }, r.dirs.map((d) => h("div", {}, h("a", { onclick: () => show(r.path.replace(/\/$/, "") + "/" + d) }, "📁 " + d))),
        r.reads.slice(0, 30).map((f) => h("div", { class: "muted small mono" }, f))));
  };
  close = modal(h("div", {}, h("h2", {}, "Choose the reads folder"), body));
  await show(start);
}

// ------------------------------------------------------------------ Runs page
const LIVE_MS = 4000;
const isLive = (status) => ["running", "starting"].includes(status);
const clock = () => new Date().toLocaleTimeString();
function since(iso) {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
// Re-render without losing the reader's place: keep scroll and open <details>.
function keepPlace(fn) {
  const y = window.scrollY;
  const open = [...document.querySelectorAll("#main details")].map((d) => d.open);
  fn();
  document.querySelectorAll("#main details").forEach((d, i) => { if (open[i]) d.open = true; });
  window.scrollTo(0, y);
}

async function pageRuns(runId) {
  clearInterval(S.runTimer);
  if (!runId) return pageRunList();
  const main = $("#main");
  let wf = null, tablesKey = "", tablesEl = null;
  const render = async () => {
    const r = await api("runs/" + runId);
    if (!wf) wf = await api(`workflows/${r.workflow.id}?version=${r.workflow.version}`).catch(() => null);
    if (S.page !== "runs" || !location.hash.includes(runId)) return;
    const status = {};
    for (const [sid, st] of Object.entries(r.steps || {})) {
      const ts = Object.values(st.tasks || {});
      status[sid] = ts.some((t) => t.status === "FAILED") ? "failed"
        : ts.length && ts.every((t) => ["COMPLETED", "CACHED"].includes(t.status)) ? "done"
        : ts.some((t) => t.status === "RUNNING") ? "running" : "waiting";
    }
    const off = new Set(r.steps_off || []);
    const willRun = wf ? runnable(wf, off) : {};
    const order = wf ? topo(wf) : Object.keys(r.steps || {});
    const graph = h("div", { id: "graph" });
    const files = r.files || [];
    const summary = files.filter((f) => f.startsWith("summary/") && /\.tsv$/.test(f));
    const key = summary.join("|");
    if (key !== tablesKey) { // only fetch the summary tables when the set of files changes
      tablesKey = key;
      tablesEl = h("div", {});
      for (const f of summary) {
        const txt = await fetch(`/api/runs/${runId}/file?path=${encodeURIComponent(f)}`).then((x) => x.text());
        tablesEl.append(h("h3", {}, f.replace(/^summary\//, "")), tsvTable(txt));
      }
    }
    const stepRow = (sid) => {
      const st = (r.steps || {})[sid] || { tasks: {} };
      const ts = Object.entries(st.tasks || {});
      const done = ts.filter(([, t]) => ["COMPLETED", "CACHED"].includes(t.status)).length;
      const label = wf && wf.steps[sid] ? wf.steps[sid].label || sid : sid;
      const skipped = wf && !willRun[sid];
      const badge = skipped ? h("span", { class: "badge gray" }, off.has(sid) ? "off" : "skipped")
        : h("span", { class: `badge status-${{ done: "succeeded", failed: "failed", running: "running" }[status[sid]] || "stopped"}` },
          status[sid] || (isLive(r.status) ? "waiting" : "not run"));
      return h("tr", {}, h("td", { class: skipped ? "skipped" : "" }, label),
        h("td", {}, ts.length ? `${done}/${ts.length}` : ""), h("td", {}, badge),
        h("td", { class: "small muted" }, ts.filter(([, t]) => t.status === "RUNNING").map(([tag, t]) => `${tag === "all" ? "all samples" : tag}: running ${since(t.started_at)}`).join("; ")));
    };
    keepPlace(() => put(main,
      h("div", { class: "row" }, h("a", { onclick: () => go("runs") }, "← All runs"), h("div", { class: "spacer" }),
        isLive(r.status) ? h("span", { class: "small muted" }, `● Live, updated ${clock()}`) : null,
        isLive(r.status) ? h("button", { class: "danger", onclick: async () => { if (confirm("Stop this run?")) { await api(`runs/${runId}/stop`, { method: "POST" }); render(); } } }, "Stop run")
          : h("button", { class: "danger", onclick: async () => { if (confirm("Delete this run and all its results? This cannot be undone.")) { await api("runs/" + runId, { method: "DELETE" }); go("runs"); } } }, "Delete run")),
      h("div", { class: "card" }, h("div", { class: "row" }, h("h1", {}, r.name), h("span", { class: `badge status-${r.status}` }, r.status)),
        h("div", { class: "kv" }, h("div", {}, "Workflow"), h("div", {}, `${r.workflow.name} v${r.workflow.version}`),
          h("div", {}, "Reads"), h("div", { class: "mono small" }, r.input),
          h("div", {}, `Samples (${(r.samples || []).length})`), h("div", {},
            (r.samples || []).slice().sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).map((s) => h("div", { class: "mono small" }, s))),
          h("div", {}, "Steps off"), h("div", {}, (r.steps_off || []).join(", ") || "none"),
          h("div", {}, "Cleanup"), h("div", {}, r.cleanup ? "on (work folder deleted after success)" : "off (everything kept)"),
          h("div", {}, "Started / finished"), h("div", {}, `${r.started} → ${r.finished || "…"}`),
          h("div", {}, "Results folder"), h("div", { class: "mono small" }, r.results)),
        r.error && r.status !== "succeeded" ? h("div", {}, h("h3", {}, "Error"), h("pre", {}, r.error)) : null),
      h("div", { class: "grid2" },
        h("div", {}, graph),
        h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "Progress"),
          h("table", { class: "data" }, h("tbody", {}, order.map(stepRow))))),
      summary.length ? h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "Summary tables"), tablesEl) : null,
      h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, `All result files (${files.length})`),
        h("div", { class: "table-wrap", style: "max-height:300px" }, h("table", { class: "data" }, h("tbody", {}, files.map((f) => h("tr", {},
          h("td", { class: "mono small" }, f),
          h("td", {}, /\.(tsv|tab|txt|log|csv|json|yml|out)$/.test(f) ? h("a", { onclick: () => viewFile(runId, f) }, "view") : null),
          h("td", {}, h("a", { href: `/api/runs/${runId}/file?path=${encodeURIComponent(f)}${/\.html$/.test(f) ? "" : "&download=1"}`, target: "_blank" }, /\.html$/.test(f) ? "open" : "download")))))))),
      r.provenance ? h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "Provenance"),
        h("div", { class: "small muted" }, "Exactly what produced these results: tool versions, containers, database versions, decisions made automatically and every setting."),
        h("h3", {}, "Tools and databases"), kvTable(r.provenance.tools),
        r.provenance.notes && Object.keys(r.provenance.notes).length ? [h("h3", {}, "Automatic decisions"), kvTable(r.provenance.notes)] : null,
        h("h3", {}, "Containers"), kvTable(Object.fromEntries(Object.entries(r.provenance.containers || {}).map(([k, v]) => [k, `${v.image}${v.digest ? "  (" + v.digest.split("@")[1] + ")" : ""}`]))),
        h("details", {}, h("summary", {}, "Full provenance.json"), h("pre", {}, JSON.stringify(r.provenance, null, 2)))) : null,
      h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "Nextflow log (last lines)"), h("pre", {}, (r.log_tail || []).join("\n")))));
    if (wf) drawGraph(graph, wf, { status, off });
    return r.status;
  };
  // Poll while the run is going; one last render after it ends, then stop.
  const loop = async () => {
    let st;
    try { st = await render(); } catch { st = "running"; }
    if (S.page === "runs" && location.hash.includes(runId) && isLive(st)) S.runTimer = setTimeout(loop, LIVE_MS);
  };
  await loop();
}

async function pageRunList() {
  const list = await api("runs");
  if (S.page !== "runs" || location.hash.includes("/")) return;
  keepPlace(() => put($("#main"), h("div", { class: "row" }, h("h1", {}, "Runs & results"), h("div", { class: "spacer" }),
      list.some((r) => isLive(r.status)) ? h("span", { class: "small muted" }, `● Live, updated ${clock()}`) : null),
    h("div", { class: "card" }, list.length ? h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["Run", "Workflow", "Samples", "Status", "Started", "Finished"].map((x) => h("th", {}, x)))),
      h("tbody", {}, list.map((r) => h("tr", {},
        h("td", {}, h("a", { onclick: () => go("runs", r.id) }, r.name || r.id), h("div", { class: "muted small mono" }, r.id)),
        h("td", {}, `${r.workflow.name} v${r.workflow.version}`), h("td", {}, r.n_samples),
        h("td", {}, h("span", { class: `badge status-${r.status}` }, r.status), isLive(r.status) ? h("div", { class: "small muted" }, `running ${since(r.started)}`) : null),
        h("td", {}, r.started || ""), h("td", {}, r.finished || "")))))
      : h("div", { class: "muted" }, "No runs yet. Start one on the Run page."))));
  if (list.some((r) => isLive(r.status))) S.runTimer = setTimeout(pageRunList, LIVE_MS);
}
function kvTable(obj) {
  return h("table", { class: "data" }, h("tbody", {}, Object.entries(obj || {}).map(([k, v]) => h("tr", {}, h("td", { class: "mono small" }, k), h("td", { class: "small" }, String(v))))));
}
function tsvTable(txt, max = 300) {
  const rows = txt.replace(/\n$/, "").split("\n").map((l) => l.split("\t"));
  if (!rows.length || !rows[0].length) return h("div", { class: "muted" }, "(empty)");
  return h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, rows[0].map((c) => h("th", {}, c)))),
    h("tbody", {}, rows.slice(1, max).map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))));
}
async function viewFile(runId, f) {
  const txt = await fetch(`/api/runs/${runId}/file?path=${encodeURIComponent(f)}`).then((x) => x.text());
  modal(h("div", {}, h("h2", {}, f), /\.(tsv|tab)$/.test(f) ? tsvTable(txt) : h("pre", {}, txt.slice(0, 200000))));
}

// ------------------------------------------------------------------ Tools page
async function pageTools() {
  await loadTools();
  const packs = await api("packs");
  const upd = await checkToolUpdates().catch(() => ({ tools: {}, updates: [], checked: null }));
  if (S.page !== "tools") return;
  const updBox = h("div", {});
  const main = $("#main");
  const byCat = {};
  for (const t of Object.values(S.tools)) (byCat[t.category || "Other"] = byCat[t.category || "Other"] || []).push(t);
  const urlInput = h("input", { type: "text", class: "wide", placeholder: "https://github.com/owner/repo  (repo must contain ntg/pack.yml)" });
  const jobBox = h("div", {});
  put(main,
    h("h1", {}, "Tools"),
    h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "Tool versions"),
      h("div", { class: "row" },
        upd.updates.length ? h("span", { class: "badge run" }, `${upd.updates.length} tool${upd.updates.length > 1 ? "s have" : " has"} a newer version`)
          : h("span", { class: "badge ok" }, upd.checked ? "All tools are up to date" : "Not checked yet"),
        h("span", { class: "small muted" }, upd.checked ? `Last checked ${upd.checked.replace("T", " ")}` : ""),
        h("div", { class: "spacer" }),
        h("button", { onclick: () => runJob("tools/updates/check", {}, updBox, pageTools) }, "Check for updates")),
      h("div", { class: "small muted", style: "margin-top:6px" },
        "Checks quay.io (built-in tools) and GitHub (tool packs) once a day by itself. Adding a version never removes the one you have: both stay installed, and each workflow step picks its version (Workflows → Edit → click a step → Version). A new version is marked untested until a run with it succeeds."),
      updBox),
    h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "Tool packs from git"),
      h("div", { class: "small muted" }, "A tool pack adds tools from another git repository, pinned to an exact commit, with its own container. Installed tools can be added to any workflow (Workflows → Edit → Add step)."),
      h("table", { class: "data", style: "margin-top:8px" }, h("tbody", {}, packs.map((p) => h("tr", {},
        h("td", {}, h("b", {}, p.name), h("div", { class: "muted small" }, p.description)),
        h("td", { class: "small" }, h("a", { href: p.repo, target: "_blank", rel: "noopener" }, p.repo.replace("https://github.com/", "")), h("div", { class: "mono muted" }, "@ " + String(p.commit).slice(0, 12))),
        h("td", { class: "small" }, p.tools.join(", ")),
        h("td", {}, p.installed ? [h("span", { class: "badge ok" }, "installed"), " ",
          h("button", { title: "Install again: rebuilds the container and replaces the pack's files (saved workflows are kept)",
            onclick: () => { if (confirm(`Reinstall ${p.name}? This rebuilds its container and takes a few minutes.`)) runJob("packs/install", { source: p.id }, jobBox, pageTools); } }, "Reinstall"), " ",
          h("button", { onclick: async () => { if (confirm(`Uninstall ${p.name}? Workflows that use it will show a missing tool.`)) { await api("packs/uninstall", { body: { id: p.id } }); pageTools(); } } }, "Uninstall")]
          : h("button", { class: "primary", onclick: () => runJob("packs/install", { source: p.id }, jobBox, pageTools) }, "Install")))))),
      h("h3", {}, "Install from a git URL"),
      h("div", { class: "row" }, h("div", { style: "flex:1" }, urlInput), h("button", { onclick: () => urlInput.value && runJob("packs/install", { source: urlInput.value.trim() }, jobBox, pageTools) }, "Install")),
      jobBox),
    ...Object.entries(byCat).sort().map(([cat, ts]) => h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, cat),
      h("table", { class: "data" }, h("tbody", {}, ts.map((t) => h("tr", {},
        h("td", { style: "width:40%" }, h("b", {}, t.name), h("div", { class: "muted small" }, t.description),
          h("div", { class: "muted small" }, t._source === "built-in" ? "built-in" : "from tool pack " + t._source)),
        h("td", {}, toolVersionsCell(t, (upd.tools || {})[t.id] || {})))))))));
}
// Installed versions as chips (× removes an added one), plus "newer available" and Add version….
function toolVersionsCell(t, u) {
  if (!t.version_kind) return h("div", { class: "small muted" }, `${t.version || ""} · part of the pipeline (updates with it)`);
  const chips = t.versions.map((v) => h("span", { class: "vchip" + (v.untested ? " untested" : ""), title: v.container || "" },
    h("b", {}, v.version), versionTags(v),
    v.default ? null : h("a", { class: "vchip-x", title: "Remove this version", onclick: () => removeVersion(t, v) }, "×")));
  return h("div", {},
    h("div", { class: "vchips" }, chips),
    h("div", { class: "row small", style: "margin-top:4px" },
      (u.newer || []).length ? h("span", { class: "badge run" }, `${u.newer[0]}${u.newer.length > 1 ? ` (+${u.newer.length - 1} more)` : ""} available`) : null,
      u.error ? h("span", { class: "muted", title: u.error }, "couldn't check") : null,
      h("button", { onclick: () => addVersionDialog(t, u) }, "Add version…")));
}
async function removeVersion(t, v) {
  const used = await api(`tools/versions/used?tool=${encodeURIComponent(t.id)}&version=${encodeURIComponent(v.version)}`);
  const msg = `Remove ${t.name} ${v.version}?` + (used.length ? `\n\nThese saved workflows use it and will show it as missing until you pick another version:\n• ${used.join("\n• ")}` : "\n\nNo saved workflow uses it.") +
    "\n\nPast runs keep their results. You can add the version again later.";
  if (!confirm(msg)) return;
  try { await api("tools/versions/remove", { body: { tool: t.id, version: v.version } }); toast(`Removed ${t.name} ${v.version}`); pageTools(); }
  catch (e) { toast("Not removed: " + e.message, 8000); }
}
// Pick any published version: newest first; installed ones can't be added twice.
function addVersionDialog(t, u) {
  let close;
  const jobBox = h("div", {});
  const have = new Set(t.versions.map((v) => t.version_kind === "git" ? v.commit : v.version));
  const list = u.available || [];
  const newestHave = t.versions.reduce((a, v) => (verCmp(v.version, a) > 0 ? v.version : a), t.version);
  const rows = list.map((v) => {
    const installed = have.has(t.version_kind === "git" ? v.choice : v.version);
    const newer = t.version_kind === "git" ? v.ref === "main" && !installed : verCmp(v.version, newestHave) > 0;
    return h("tr", { class: installed ? "muted" : "" },
      h("td", { class: "mono small" }, v.label),
      h("td", {}, installed ? h("span", { class: "badge gray" }, "installed") : newer ? h("span", { class: "badge run" }, t.version_kind === "git" ? "latest" : "newer") : null),
      h("td", {}, installed ? null : h("button", { class: newer ? "primary" : "", onclick: () => {
        put(jobBox); runJob("tools/versions/add", { tool: t.id, choice: v.choice }, jobBox, () => { close(); pageTools(); });
      } }, "Add")));
  });
  close = modal(h("div", {},
    h("h2", {}, `Add a version of ${t.name}`),
    h("div", { class: "small muted" }, `Installed: ${t.versions.map((v) => v.version).join(", ")}. The installed versions stay; the new one is checked (downloaded and started once) and marked untested until a run with it succeeds.` +
      (t.version_kind === "git" ? " Tool packs: pick a branch or tag of the pack's repository; its container is built here (a few minutes)." : " Versions come from BioContainers on quay.io; a newer version can change options or results, so check the first run's output.")),
    list.length ? h("div", { class: "table-wrap", style: "max-height:45vh;margin-top:8px" }, h("table", { class: "data" }, h("tbody", {}, rows)))
      : h("div", { class: "warn info" }, u.error ? `Couldn't look up versions: ${u.error}` : "No version list yet: press Check for updates on the Tools page first."),
    jobBox));
}
// Compare version strings numerically: 1.10.0 > 1.9.2.
function verCmp(a, b) {
  const pa = String(a).split(/[.\-_+]/), pb = String(b).split(/[.\-_+]/);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? "", y = pb[i] ?? "";
    const nx = /^\d+$/.test(x) ? +x : -1, ny = /^\d+$/.test(y) ? +y : -1;
    if (nx !== ny) return nx - ny;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}
async function runJob(path, body, box, done) {
  const job = await api(path, { body });
  const pre = h("pre", {}, "");
  const bar = h("div", { class: "progress" }, h("div", { style: "width:0%" }));
  put(box, h("h3", {}, job.title), bar, pre);
  const poll = async () => {
    const j = await api("jobs/" + job.id);
    pre.textContent = j.log.join("\n");
    if (j.progress && j.progress[1]) bar.firstChild.style.width = (100 * j.progress[0] / j.progress[1]).toFixed(1) + "%";
    if (j.status === "running") setTimeout(poll, 1500);
    else { bar.firstChild.style.width = "100%"; toast(`${job.title}: ${j.status}`); if (j.status === "done" && done) setTimeout(done, 800); }
  };
  poll();
}

// ------------------------------------------------------------------ Databases page
async function pageDatabases() {
  const dbs = await api("databases");
  if (S.page !== "databases") return;
  const jobBox = h("div", {});
  put($("#main"), h("h1", {}, "Reference databases"),
    h("div", { class: "card" }, h("div", { class: "small muted" }, "Databases are stored on this computer (default ~/.ntg/databases, or $NTG_DB_DIR). Their name and version are recorded in every run's provenance."),
      h("table", { class: "data", style: "margin-top:8px" }, h("tbody", {}, dbs.map((d) => h("tr", {},
        h("td", {}, h("b", {}, d.name), h("div", { class: "muted small" }, d.description), d.galaxy_note ? h("div", { class: "galaxy diff" }, d.galaxy_note) : null),
        h("td", { class: "small" }, `version ${d.version}`, h("div", { class: "muted" }, `${d.size_gb} GB`), h("div", { class: "mono muted" }, d.path)),
        h("td", {}, d.installed ? h("span", { class: "badge ok" }, "installed")
          : h("button", { class: "primary", onclick: () => runJob(`databases/${d.id}/install`, {}, jobBox, pageDatabases) }, "Download & install")))))),
      jobBox));
}

// ------------------------------------------------------------------ System page
async function pageSystem() {
  await loadHardware();
  const hw = S.hw;
  const wf = S.wf || await api(`workflows/${(S.workflows[0] || {}).id}`);
  const r = await api("workflows/check", { body: { workflow: rawWorkflow(wf) } }).catch(() => ({ warnings: [] }));
  if (S.page !== "system") return;
  put($("#main"), h("h1", {}, "This computer"),
    h("div", { class: "card" }, h("div", { class: "kv" },
      h("div", {}, "CPU threads"), h("div", {}, hw.cpus),
      h("div", {}, "Memory"), h("div", {}, `${hw.memory_gb} GB total, ${hw.memory_available_gb} GB free now`),
      h("div", {}, "Running in WSL"), h("div", {}, hw.wsl ? "yes" : "no"),
      h("div", {}, "Docker"), h("div", {}, hw.docker ? `running (${hw.docker})` : h("span", { style: "color:var(--err)" }, `not available: ${hw.docker_error}`)),
      h("div", {}, "Apptainer"), h("div", {}, hw.apptainer || "not installed"),
      h("div", {}, "Nextflow"), h("div", {}, hw.nextflow || h("span", { style: "color:var(--err)" }, "not installed")),
      h("div", {}, "Java"), h("div", {}, hw.java || "not found"),
      h("div", {}, "Free disk (runs / work / databases)"), h("div", {}, `${hw.disk_free_gb.runs} / ${hw.disk_free_gb.work} / ${hw.disk_free_gb.databases} GB`))),
    h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, `What this means for "${wf.name}"`),
      r.warnings.length ? r.warnings.map(warnBox) : h("div", { class: "warn info" }, "This computer can run every step.")),
    updatesCard());
}

// ------------------------------------------------------------------ Updates (on the System page)
function updatesCard() {
  const box = h("div", {});
  const card = h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "Updates"), box);
  const show = (u, busy) => {
    const cur = u && u.current;
    const fresh = u && u.checked && !u.error;
    put(box,
      cur ? h("div", { class: "kv" },
        h("div", {}, "This version"), h("div", {}, h("span", { class: "mono" }, cur.commit), ` from ${cur.date}: ${cur.subject}`),
        u.upstream ? [h("div", {}, "Compared with"), h("div", { class: "mono small" }, u.upstream)] : null) : null,
      u && u.error ? warnBox({ level: "warning", message: u.error }) : null,
      fresh && !u.new.length ? h("div", { class: "warn info" }, "You have the latest version.") : null,
      u && (u.new || []).length ? [
        h("h3", {}, `What's new: ${u.new.length} change${u.new.length > 1 ? "s" : ""} on GitHub`),
        h("div", { class: "small muted" }, "Read what each update fixes or adds before you update."),
        h("div", { class: "changes" }, u.new.map((c) => h("div", { class: "change" },
          h("div", {}, h("b", {}, c.subject), h("span", { class: "small muted" }, `  ${c.date} · ${c.commit}`)),
          (c.notes || []).length ? h("ul", {}, c.notes.map((n) => h("li", {},
            n.kind ? h("span", { class: "badge " + ({ "Bug fix": "err", New: "ok", Improved: "run" }[n.kind] || "gray") }, n.kind) : null,
            n.kind ? " " : null, n.text))) : null))),
        u.blocked ? warnBox({ level: "warning", message: "Can't update now. " + u.blocked }) : null] : null,
      h("div", { class: "row", style: "margin-top:8px" },
        h("button", { disabled: busy, onclick: async () => { show(u, true); show(await checkUpdates().catch((e) => ({ ...u, error: e.message })), false); } },
          busy ? "Checking…" : "Check for updates"),
        u && (u.new || []).length ? h("button", { class: "primary", disabled: busy || !!u.blocked, onclick: () => install(u) }, "Update now") : null),
      h("div", { class: "small muted", style: "margin-top:6px" },
        "Updating downloads the new version of the pipeline from GitHub and restarts this page. Your saved workflows, runs, databases and tool packs are kept. Every run records which version produced it."));
  };
  const install = async (u) => {
    if (!confirm(`Update to the newest version (${u.new.length} change${u.new.length > 1 ? "s" : ""})? The page restarts afterwards.`)) return;
    put(box, h("div", { class: "warn info" }, "Updating…"));
    try {
      const r = await api("updates/install", { body: {} });
      if (!r.updated) return show(await checkUpdates(), false);
      put(box, h("div", { class: "warn info" }, `Updated to ${r.current.commit}. Restarting…`));
      for (let i = 0; i < 30; i++) { // wait for the restarted UI, then load the new page
        await new Promise((ok) => setTimeout(ok, 1000));
        try { await fetch("/api/updates?fetch=0"); location.reload(); return; } catch { /* not back yet */ }
      }
      put(box, warnBox({ level: "warning", message: "Updated, but the UI didn't come back. Close it and open the desktop shortcut again." }));
    } catch (e) { show({ ...u, error: "Not updated: " + e.message }, false); }
  };
  show(S.updates, false);
  if (!S.updates) checkUpdates(false).then((u) => show(u, false)).catch(() => {});
  return card;
}

boot().catch((e) => { put($("#main"), h("div", { class: "warn error" }, "Could not load: " + e.message)); });
