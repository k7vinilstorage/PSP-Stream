// Interface web do PSPStream: lê /api/config uma vez, /api/status a cada 2 s,
// e manda só o que mudou para POST /api/config.
"use strict";

const GROUPS = ["Captura", "Vídeo", "Som", "Controles", "Rede"];
const LABELS = {
  source: { portal: "portal (Wayland)", kms: "kms (placa de vídeo)", x11: "x11", test: "test (padrão animado)",
            static: "static (imagem fixa)", gst: "gst (linha de comando)" },
  codec: { auto: "auto", h264p: "h264p (frames P)", h264: "h264 (quadros completos)", jpeg: "jpeg" },
  dscp: { ef: "ef (voz)", cs5: "cs5 (vídeo)", af41: "af41 (vídeo)", "0": "nenhuma" },
};

let schema = [];
let saved = {};      // valores do servidor
let pending = {};    // valem ao reiniciar o servidor
let profiles = {};
let lastLog = 0;
let busy = false;
let configPath = "";

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) if (k != null) n.append(k);
  return n;
};

function optionLabel(key, value) {
  if (key === "profile" && profiles[value]) {
    return `${value} (${profiles[value] === "gamepad" ? "controle de Xbox" : "teclado e mouse"})`;
  }
  return (LABELS[key] && LABELS[key][value]) || String(value);
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
  for (const g of GROUPS) {
    const items = schema.filter((s) => s.group === g);
    if (!items.length) continue;
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
          className: "badge cli", textContent: "linha de comando",
          title: `${s.flag} foi dado na linha de comando: ao reiniciar, ele vale mais que esta configuração`,
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
    if (s.key in pending) p.textContent = `${pending[s.key]} ao reiniciar`;
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
  if (!busy) $("bar-msg").textContent = n === 1 ? "1 mudança ainda não aplicada" : `${n} mudanças ainda não aplicadas`;
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
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

async function apply() {
  const diff = changes();
  if (!Object.keys(diff).length || busy) return;
  busy = true;
  $("apply").disabled = $("undo").disabled = true;
  $("bar-msg").textContent = "source" in diff && diff.source === "portal"
    ? "Aplicando... (confirme a tela na janela do portal, no PC)" : "Aplicando...";
  try {
    const res = await fetch("/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: diff }),
    });
    const r = await res.json();
    if (!res.ok) throw new Error(r.error || res.statusText);
    showErrors(r.errors);
    if (r.values) saved = r.values;
    if (r.pending) pending = r.pending;
    const keep = r.errors ? Object.fromEntries(Object.keys(r.errors).filter((k) => k in diff).map((k) => [k, diff[k]])) : {};
    fill({ ...saved, ...keep });
    const parts = [];
    if (r.applied && r.applied.length) parts.push(`Aplicado: ${r.applied.join(", ")}.`);
    for (const l of r.later || []) parts.push(`${l.key}: ${l.when}.`);
    if (r.errors && Object.keys(r.errors).length) parts.push("Algumas mudanças não foram aplicadas (veja os campos).");
    if (r.save_error) parts.push(r.save_error);
    toast(parts.join(" ") || "Nada mudou.", r.ok ? 4000 : 9000);
  } catch (err) {
    toast(`Não foi possível aplicar: ${err.message}`, 9000);
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
    pill.textContent = `PSP conectado (${psp.transport.toUpperCase()})`;
  } else {
    pill.className = "pill wait";
    pill.textContent = psp ? "PSP conectando" : "aguardando o PSP";
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
    let line = `${psp.addr} via ${psp.transport.toUpperCase()}, há ${psp.since_s} s, ${psp.frames} frames (${psp.mb} MB)`;
    if (psp.wifi) line += `, sinal ${psp.wifi.signal}%`;
    rows.push(["PSP", line]);
    if (psp.wifi && psp.wifi.power_save) {
      rows.push(["Wi-Fi do PSP", "economia de energia WLAN ligada: desligue em Ajustes > Economia de energia", true]);
    }
  } else {
    rows.push(["PSP", `no server.txt: ${st.ip}:${st.port} (ou "Procurar o PC na rede")`]);
  }
  const c = st.capture;
  rows.push(["Captura", `${c.source}, ${c.codec}, até ${c.fps_limit} fps` + (c.failed ? ` — parou: ${c.failed}` : ""), !!c.failed]);
  if (st.wolf) rows.push(["Wolf", st.wolf.text, st.wolf.warn]);
  const a = st.audio;
  if (a && a.on) {
    rows.push(["Som", `${a.device}, ${a.rate} Hz ${a.channels === 2 ? "estéreo" : "mono"}, ~${a.kbps} KB/s` +
      (psp && psp.audio_on === false ? " (desligado no PSP)" : "") + (a.failed ? ` — parou: ${a.failed}` : ""), !!a.failed]);
  } else {
    rows.push(["Som", (a && a.note) || "desligado"]);
  }
  const i = st.input;
  rows.push(["Controles", i.on ? `perfil ${i.profile} (${i.note})` : i.note || "desligados", !i.on && /desativ/.test(i.note || "")]);
  setFacts(rows);
  $("foot").textContent = `Servidor em ${st.ip}:${st.port}, rodando há ${Math.round(st.uptime_s / 60)} min` +
    (configPath ? ` — configurações gravadas em ${configPath}` : "");

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
    pill.textContent = "servidor fora do ar";
  }
  setTimeout(poll, 2000);
}

async function start() {
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
    toast(`Não foi possível ler as configurações: ${err.message}`, 15000);
  }
  $("apply").addEventListener("click", apply);
  $("undo").addEventListener("click", () => { showErrors({}); fill(saved); });
  $("form").addEventListener("submit", (e) => { e.preventDefault(); apply(); });
  poll();
}

start();
