// Interface web do PSPStream: lê /api/i18n e /api/config uma vez, /api/status
// a cada 2 s, e manda só o que mudou para POST /api/config.
// Textos em inglês, dentro de t(); a tradução vem do servidor (lang_pt.py).
"use strict";

const N_ = (text) => text;  // marca para tradução; t() na hora de mostrar
const LABELS = {
  source: { portal: "portal (Wayland)", kms: N_("kms (graphics card)"), x11: "x11", test: N_("test (animated pattern)"),
            static: N_("static (still image)"), gst: N_("gst (command line)") },
  codec: { auto: "auto", h264p: N_("h264p (P frames)"), h264: N_("h264 (complete frames)"), jpeg: "jpeg" },
  dscp: { ef: N_("ef (voice)"), cs5: N_("cs5 (video)"), af41: N_("af41 (video)"), "0": N_("none") },
};
// Os nomes dos idiomas ficam no próprio idioma, sem tradução.
const LANG_NAMES = { en: "English", pt: "Português" };

let schema = [];
let saved = {};      // valores do servidor
let pending = {};    // valem ao reiniciar o servidor
let profiles = {};
let lastLog = 0;
let busy = false;
let configPath = "";
let messages = {};   // inglês -> idioma escolhido (vazio em inglês)

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) if (k != null) n.append(k);
  return n;
};

function t(text, params) {
  const s = messages[text] || text;
  return params ? s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m)) : s;
}

function translatePage(lang) {
  document.documentElement.lang = lang === "pt" ? "pt-BR" : "en";
  for (const n of document.querySelectorAll("[data-i18n]")) n.textContent = t(n.dataset.i18n);
  for (const n of document.querySelectorAll("[data-i18n-aria-label]")) n.setAttribute("aria-label", t(n.dataset.i18nAriaLabel));
}

function optionLabel(key, value) {
  if (key === "profile" && profiles[value]) {
    return profiles[value] === "gamepad" ? t("{name} (Xbox controller)", { name: value })
      : t("{name} (keyboard and mouse)", { name: value });
  }
  if (key === "language") return LANG_NAMES[value] || String(value);
  return LABELS[key] && LABELS[key][value] ? t(LABELS[key][value]) : String(value);
}

function control(s) {
  const id = "f-" + s.key;
  if (s.kind === "bool") {
    const box = el("input", { type: "checkbox", id });
    return [el("label", { className: "switch" }, box, el("span")), box];
  }
  if (s.kind === "choice") {
    const sel = el("select", { id });
    for (const c of s.choices) sel.append(el("option", { value: String(c), textContent: optionLabel(s.key, c) }));
    return [sel, sel];
  }
  if (s.kind === "text") {
    const inp = el("input", { type: "text", id, spellcheck: false, autocomplete: "off" });
    if (s.suggestions) {
      const list = el("datalist", { id: id + "-list" });
      for (const v of s.suggestions) list.append(el("option", { value: v }));
      inp.setAttribute("list", list.id);
      return [el("div", { className: "wide" }, inp, list), inp];
    }
    return [inp, inp];
  }
  const inp = el("input", { type: "number", id, inputMode: s.kind === "int" ? "numeric" : "decimal" });
  if (s.min != null) inp.min = s.min;
  if (s.max != null) inp.max = s.max;
  inp.step = s.kind === "int" ? "1" : "any";
  return [inp, inp];
}

function render() {
  const form = $("form");
  form.textContent = "";
  const groups = [...new Set(schema.map((s) => s.group))];  // na ordem do servidor, já traduzidos
  for (const g of groups) {
    const items = schema.filter((s) => s.group === g);
    const fs = el("fieldset", { className: "card" }, el("legend", { textContent: g }));
    for (const s of items) {
      const [widget, input] = control(s);
      input.dataset.key = s.key;
      input.addEventListener("input", refresh);
      input.addEventListener("change", refresh);
      const badges = el("span", { className: "badges" });
      if (s.apply !== "live") badges.append(el("span", { className: "badge", textContent: s.apply_text }));
      if (s.cli) {
        badges.append(el("span", {
          className: "badge cli", textContent: t("command line"),
          title: t("{flag} was given on the command line: on restart, it wins over this setting", { flag: s.flag }),
        }));
      }
      badges.append(el("span", { className: "badge later", id: "p-" + s.key, hidden: true }));
      const row = el("div", { className: "row", id: "r-" + s.key },
        el("label", { className: "lab", htmlFor: "f-" + s.key, textContent: s.label }, badges),
        widget,
        s.help ? el("div", { className: "help", textContent: s.help }) : null,
        el("div", { className: "err", id: "e-" + s.key, hidden: true }));
      fs.append(row);
    }
    form.append(fs);
  }
  fill(saved);
}

function fill(values) {
  for (const s of schema) {
    const input = $("f-" + s.key);
    const v = values[s.key];
    if (s.kind === "bool") input.checked = !!v;
    else input.value = v == null ? "" : String(v);
    const p = $("p-" + s.key);
    p.hidden = !(s.key in pending);
    if (s.key in pending) p.textContent = t("{value} on restart", { value: pending[s.key] });
  }
  refresh();
}

function read(s) {
  const input = $("f-" + s.key);
  if (s.kind === "bool") return input.checked;
  if (s.kind === "int" || s.kind === "float") return input.value.trim() === "" ? null : Number(input.value);
  if (s.kind === "choice") {
    const hit = s.choices.find((c) => String(c) === input.value);
    return hit === undefined ? input.value : hit;
  }
  return input.value.trim();
}

function current() {
  const out = {};
  for (const s of schema) out[s.key] = read(s);
  return out;
}

function changes() {
  const now = current();
  const diff = {};
  for (const s of schema) if (now[s.key] !== saved[s.key]) diff[s.key] = now[s.key];
  return diff;
}

function refresh() {
  const now = current();
  for (const s of schema) {
    const row = $("r-" + s.key);
    row.hidden = !!(s.show_if && !s.show_if.values.includes(now[s.show_if.key]));
    row.classList.toggle("changed", now[s.key] !== saved[s.key]);
  }
  const n = Object.keys(changes()).length;
  $("bar").hidden = n === 0 && !busy;
  if (!busy) $("bar-msg").textContent = n === 1 ? t("1 change not applied yet") : t("{n} changes not applied yet", { n });
}

function showErrors(errors) {
  for (const s of schema) {
    const e = $("e-" + s.key);
    const msg = errors && errors[s.key];
    e.hidden = !msg;
    e.textContent = msg || "";
    $("f-" + s.key).classList.toggle("invalid", !!msg);
  }
}

let toastTimer = null;
function toast(text, ms = 5000) {
  const box = $("toast");
  box.textContent = text;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, ms);
}

async function apply() {
  const diff = changes();
  if (!Object.keys(diff).length || busy) return;
  busy = true;
  $("apply").disabled = $("undo").disabled = true;
  $("bar-msg").textContent = "source" in diff && diff.source === "portal"
    ? t("Applying... (confirm the screen in the portal window, on the PC)") : t("Applying...");
  try {
    const res = await fetch("/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: diff }),
    });
    const r = await res.json();
    if (!res.ok) throw new Error(r.error || res.statusText);
    if (r.applied && r.applied.includes("language")) {
      location.reload();  // a página inteira no idioma novo
      return;
    }
    showErrors(r.errors);
    if (r.values) saved = r.values;
    if (r.pending) pending = r.pending;
    const keep = r.errors ? Object.fromEntries(Object.keys(r.errors).filter((k) => k in diff).map((k) => [k, diff[k]])) : {};
    fill({ ...saved, ...keep });
    const parts = [];
    if (r.applied && r.applied.length) parts.push(t("Applied: {keys}.", { keys: r.applied.join(", ") }));
    for (const l of r.later || []) parts.push(`${l.key}: ${l.when}.`);
    if (r.errors && Object.keys(r.errors).length) parts.push(t("Some changes were not applied (see the fields)."));
    if (r.save_error) parts.push(r.save_error);
    toast(parts.join(" ") || t("Nothing changed."), r.ok ? 4000 : 9000);
  } catch (err) {
    toast(t("Could not apply: {error}", { error: err.message }), 9000);
  } finally {
    busy = false;
    $("apply").disabled = $("undo").disabled = false;
    refresh();
  }
}

function fmt(v, digits = 0) {
  return v == null ? "–" : Number(v).toFixed(digits);
}

function setFacts(rows) {
  const dl = $("facts");
  dl.textContent = "";
  for (const [k, v, bad] of rows) dl.append(el("dt", { textContent: k }), el("dd", { textContent: v, className: bad ? "bad" : "" }));
}

function showStatus(st) {
  const psp = st.psp;
  const pill = $("conn");
  if (psp && psp.streaming) {
    pill.className = "pill ok";
    pill.textContent = t("PSP connected ({transport})", { transport: psp.transport.toUpperCase() });
  } else {
    pill.className = "pill wait";
    pill.textContent = psp ? t("PSP connecting") : t("waiting for the PSP");
  }
  const s = (psp && psp.summary) || {};
  $("t-fps").textContent = fmt(s.fps, 1);
  $("t-src").textContent = fmt(s.source_fps, 1);
  $("t-lat").textContent = fmt(s.latency_ms);
  $("t-wifi").textContent = fmt(s.wifi_kbps);
  $("t-hitch").textContent = psp && psp.summary ? String(s.hitches || 0) : "–";
  $("t-q").textContent = s.quality == null ? (st.capture.quality == null ? "–" : String(st.capture.quality)) : String(s.quality);

  const rows = [];
  if (psp) {
    let line = t("{addr} over {transport}, for {seconds} s, {frames} frames ({mb} MB)", {
      addr: psp.addr, transport: psp.transport.toUpperCase(), seconds: psp.since_s, frames: psp.frames, mb: psp.mb,
    });
    if (psp.wifi) line += t(", signal {signal}%", { signal: psp.wifi.signal });
    rows.push(["PSP", line]);
    if (psp.wifi && psp.wifi.power_save) {
      rows.push([t("PSP Wi-Fi"), t("WLAN power save on: turn it off in Settings > Power Save Settings"), true]);
    }
  } else {
    rows.push(["PSP", t('in server.txt: {ip}:{port} (or "Find the PC on the network")', { ip: st.ip, port: st.port })]);
  }
  const c = st.capture;
  rows.push([t("Capture"), t("{source}, {codec}, up to {fps} fps", { source: c.source, codec: c.codec, fps: c.fps_limit }) +
    (c.failed ? t(" — stopped: {error}", { error: c.failed }) : ""), !!c.failed]);
  if (st.wolf) rows.push(["Wolf", st.wolf.text, st.wolf.warn]);
  const a = st.audio;
  if (a && a.on) {
    rows.push([t("Audio"), `${a.device}, ${a.rate} Hz ${a.channels === 2 ? t("stereo") : "mono"}, ~${a.kbps} KB/s` +
      (psp && psp.audio_on === false ? t(" (turned off on the PSP)") : "") +
      (a.failed ? t(" — stopped: {error}", { error: a.failed }) : ""), !!a.failed]);
  } else {
    rows.push([t("Audio"), (a && a.note) || t("turned off")]);
  }
  const i = st.input;
  rows.push([t("Controls"), i.on ? t("profile {profile} ({note})", { profile: i.profile, note: i.note }) : i.note || t("off"),
    !i.on && /disabled|desativ/.test(i.note || "")]);
  setFacts(rows);
  $("foot").textContent = t("Server at {ip}:{port}, running for {minutes} min", {
    ip: st.ip, port: st.port, minutes: Math.round(st.uptime_s / 60),
  }) + (configPath ? t(" — settings saved in {path}", { path: configPath }) : "");

  const log = $("log");
  const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
  for (const [id, level, text] of st.log || []) {
    lastLog = Math.max(lastLog, id);
    log.append(el("div", { className: level, textContent: text }));
  }
  while (log.childNodes.length > 300) log.firstChild.remove();
  if (atEnd) log.scrollTop = log.scrollHeight;
}

async function poll() {
  try {
    const res = await fetch(`/api/status?since=${lastLog}`, { cache: "no-store" });
    if (!res.ok) throw new Error(res.statusText);
    showStatus(await res.json());
  } catch (err) {
    const pill = $("conn");
    pill.className = "pill bad";
    pill.textContent = t("server offline");
  }
  setTimeout(poll, 2000);
}

async function start() {
  try {
    const res = await fetch("/api/i18n", { cache: "no-store" });
    const i18n = await res.json();
    messages = i18n.messages || {};
    translatePage(i18n.lang);
  } catch (err) {
    messages = {};  // segue em inglês
  }
  try {
    const res = await fetch("/api/config", { cache: "no-store" });
    const cfg = await res.json();
    schema = cfg.settings;
    saved = cfg.values;
    pending = cfg.pending || {};
    profiles = cfg.profiles || {};
    $("version").textContent = cfg.version;
    render();
    configPath = cfg.config_path || "";
  } catch (err) {
    toast(t("Could not read the settings: {error}", { error: err.message }), 15000);
  }
  $("apply").addEventListener("click", apply);
  $("undo").addEventListener("click", () => { showErrors({}); fill(saved); });
  $("form").addEventListener("submit", (e) => { e.preventDefault(); apply(); });
  poll();
}

start();
