import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { enhance } from "./controls.js";
import { RGBELoader } from "three/addons/loaders/RGBELoader.js";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const VIEWS = ["front", "left", "back", "right"];
const VIEW_RU = { front: "перед", left: "левый бок", back: "зад", right: "правый бок" };
const STEPS = ["views", "mesh", "lowpoly"];
const STEP_RU = { views: "Виды", mesh: "3D", lowpoly: "Лоу-поли", describe: "Описание" };
const ST_RU = { queued: "в очереди", running: "идёт", done: "готово", error: "ошибка" };
const api = async (url, opts = {}) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const fmtTime = s => { s = Math.round(s); return s < 60 ? `${s} с` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const busy = (m, s) => ["queued", "running"].includes(m?.steps[s]?.status);
const file = (m, rel) => `/files/${m.id}/${rel}`;

let props = [], cur = null, tab = "crop", photo = 0, crop = null, logStep = null, queue = { current: null, queued: [] };

// ================= список

async function loadList() {
  props = await api("/api/props");
  const list = $("#list");
  list.innerHTML = "";
  for (const p of props) {
    const el = document.createElement("div");
    el.className = "item" + (cur && p.id === cur.id ? " on" : "");
    const thumb = p.files.sheet || p.files.views.front || p.files.crop;
    el.innerHTML = `<img alt=""><div style="min-width:0"><div class="n"></div><div class="s">${
      STEPS.map(s => `<span class="pip ${p.steps[s]?.status || ""}" title="${STEP_RU[s]}: ${ST_RU[p.steps[s]?.status] || "—"}"></span>`).join("")}</div></div>`;
    if (thumb) $("img", el).src = file(p, thumb);
    $(".n", el).textContent = p.name;
    el.onclick = () => select(p.id);
    list.append(el);
  }
}

// ================= выбор пропа

async function select(id) {
  cur = await api(`/api/props/${id}`);
  photo = 0; crop = null;
  $("#empty").hidden = true;
  $("#inspEmpty").hidden = true;
  $("#insp").hidden = false;
  $("#runAll").disabled = false;
  for (const inp of $$("[data-f]")) inp.value = cur[inp.dataset.f] || "";
  for (const inp of $$("[data-p]")) {
    const v = cur.params[inp.dataset.p] ?? { single_src: "front", rot: 0, views_engine: "qwen", wear: "keep", t2i_model: "klein" }[inp.dataset.p] ?? "";
    if (inp.type === "checkbox") inp.checked = !!v; else inp.value = inp.dataset.p === "height_cm" && !v ? "" : v;
  }
  setVmode(cur.params);
  showParams();
  // фото
  const ph = $("#photos");
  ph.innerHTML = "";
  cur.files.photos.forEach((p, i) => {
    const img = new Image(); img.src = file(cur, p);
    img.onclick = () => { photo = i; setCropper(); };
    ph.append(img);
  });
  setCropper();
  $("#views").innerHTML = "";
  viewer.url = null;
  syncViewer();
  const f = cur.files;
  setTab(f.lo || f.hi ? "model" : Object.keys(f.views).length ? "views" : "crop");
  if (logStep) openLog(logStep);
  render(cur);
  loadList();
}

function setTab(t) {
  tab = t;
  $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  $$(".tools").forEach(el => el.classList.toggle("on", el.dataset.tools === t && !!cur));
  $$(".pane").forEach(el => el.classList.toggle("on", el.dataset.pane === t && !!cur));
  syncViewer();
}
$$("#tabs button").forEach(b => b.onclick = () => cur && setTab(b.dataset.tab));

// ================= инспектор

for (const inp of $$("[data-f]")) inp.onchange = () => patch({ [inp.dataset.f]: inp.value }).then(loadList);
for (const inp of $$("[data-p]")) {
  inp.oninput = showParams;
  inp.onchange = () => patch({ params: { [inp.dataset.p]: inp.type === "checkbox" ? inp.checked
    : ["model", "single_src", "views_engine", "wear", "t2i_model"].includes(inp.dataset.p) ? inp.value : +inp.value } });
}
$("#exportBtn").onclick = async () => {
  if (!cur) return;
  const b = $("#exportBtn");
  b.disabled = true; b.querySelector("b").textContent = "⬇ Собираю архив…";
  try {
    const r = await api(`/api/props/${cur.id}/export`, json("POST", { height_cm: +$("[data-p=height_cm]").value || 0, sources: $("[data-p=export_sources]").checked }));
    const a = document.createElement("a");
    a.href = r.url; a.download = r.slug + ".zip"; document.body.append(a); a.click(); a.remove();
    const s = r.size_m;
    toast(`${r.slug}.zip · ${(r.bytes / 2 ** 20).toFixed(1)} МБ · ${r.triangles} тр. · ${Math.round(s.width * 100)}×${Math.round(s.depth * 100)}×${Math.round(s.height * 100)} см`);
  } catch (e) { alert(e.message); }
  b.disabled = false; b.querySelector("b").textContent = "⬇ Скачать архив";
};
$("#describeBtn").onclick = async () => {
  if (!cur) return;
  const b = $("#describeBtn");
  b.disabled = true; b.classList.add("busy"); b.textContent = "✨ перевожу…";  // OpenAI отвечает за пару секунд прямо в запросе
  try {
    // в режиме «по тексту» — исчерпывающее описание для генерации с нуля (поле t2i), иначе промпт перерисовки (desc)
    const kind = vmodeOf(cur.params) === "text" ? "t2i" : "edit";
    cur = await api(`/api/props/${cur.id}/describe`, json("POST", { notes: $("[data-f=notes]").value, kind }));
    render(cur); poll(); healthAt = 0;
  } catch (e) { alert(e.message); render(cur); }
};
function renderDescribe(m) {
  const b = $("#describeBtn"), st = m.steps.describe, on = busy(m, "describe");
  b.disabled = on; b.classList.toggle("busy", on);
  b.textContent = on ? (st.status === "queued" ? "✨ в очереди…" : "✨ перевожу…") : st?.status === "error" ? "✨ ошибка — ещё раз" : "✨ Из заметки";
  const text = vmodeOf(m.params) === "text";  // «по тексту» — своё поле промпта: desc не затирается и пригодится в других режимах
  $("[data-f=desc]").hidden = text;
  $("[data-f=t2i]").hidden = !text;
  $("#descLabel").textContent = text ? "Описание для генерации с нуля (EN)" : "Промпт для генератора (EN)";
  const eng = text ? m.t2i_engine : m.desc_engine;
  b.title = st?.status === "error" ? st.error || ""
    : (text ? "Описать предмет с кадра исчерпывающе — форма, число деталей, материалы, износ — с переводом заметки"
      : "Перевести заметку целиком и дополнить деталями с кадра") + " (OpenAI, без ключа — локальная Gemma)" +
      (eng ? `\nПоследний раз: ${eng}` : "");
  for (const k of ["notes", "desc", "t2i"]) {  // поле, которое сейчас правят, не трогаем
    const ta = $(`[data-f=${k}]`);
    if (document.activeElement !== ta && ta.value !== (m[k] || "")) ta.value = m[k] || "";
  }
}


function showParams() {
  $("#v-tris").textContent = (+$("[data-p=tris]").value).toLocaleString("ru");
  $("#v-crease").textContent = $("[data-p=crease]").value + "°";
}

async function patch(body) {
  if (!cur) return;
  cur = await api(`/api/props/${cur.id}`, json("PATCH", body));
  render(cur);
  return cur;
}

async function run(body) {
  try {
    cur = await api(`/api/props/${cur.id}/run`, json("POST", body));
    render(cur);
    poll();
  } catch (e) {
    if (e.message !== "кадр не выделен") return alert(e.message);
    // рамку забыли: предложить обвести (по умолчанию) или осознанно запустить с целым фото
    const d = $("#cropDlg");
    d.returnValue = "";
    d.showModal();
    d.onclose = () => {
      if (d.returnValue === "force") return run({ ...body, force: true });
      if (d.returnValue === "crop") {
        setTab("crop");
        const c = $("#cropper");
        c.classList.remove("nudge"); void c.offsetWidth; c.classList.add("nudge");
      }
    };
  }
}

$("#runAll").onclick = () => run({ step: "all" });
$$("[data-act]").forEach(b => b.onclick = async () => {
  const a = b.dataset.act;
  if (a === "cropfull") return saveCrop(true);
  if (a === "views") return run({ step: "views" });
  if (a === "delete") {
    if (!confirm(`Удалить «${cur.name}» со всеми файлами?`)) return;
    try { await api(`/api/props/${cur.id}`, { method: "DELETE" }); } catch (e) { return alert(e.message); }
    cur = null; clearObj();
    $("#empty").hidden = false; $("#insp").hidden = true; $("#inspEmpty").hidden = false; $("#runAll").disabled = true;
    $("#crumb").textContent = "";
    setTab(tab);
    return loadList();
  }
});

// ---- конвейер: шаги со своими настройками. Режим видов объединяет «источник 3D» и «изображение»:
// чистый фронт = одно изображение по фронту Qwen; 4 вида = Pixal3D multiview; кадр как есть = одно изображение по кадру.
const VMODE = {
  front: { params: { mode: "single", single_src: "front" }, hint: "Qwen перерисует предмет чистым фронтом — лучшее для 3D." },
  multi: { params: { mode: "multiview" }, hint: "Фронт и 3 разворота → Pixal3D. Дольше; хорош со своими фото сбоку и сзади." },
  none: { params: { mode: "single", single_src: "crop" }, hint: "3D прямо по кадру: быстрее, но тени и блики уйдут в текстуру." },
  // по тексту: модель описывает предмет словами, Klein рисует его с нуля — фото в генератор не попадает вовсе
  text: { params: { mode: "single", single_src: "text" }, hint: "Модель опишет предмет, Klein или Krea-2 нарисует его с нуля — для простых вещей: паллеты, ящики, бочки." },
};
// Krea-2 — необязательная модель: пункт доступен, только если её файлы есть в ComfyUI (проверка — при открытии режима «по тексту»)
let kreaChecked = 0;
async function checkKrea() {
  if (Date.now() - kreaChecked < 60000) return;
  kreaChecked = Date.now();
  const f = await api("/api/engines").then(r => r.krea2).catch(() => null);
  const o = $("[data-p=t2i_model] option[value=krea2]");
  o.disabled = !f;
  o.textContent = f ? "Krea-2 Turbo · 8 шагов" : "Krea-2 · нет в ComfyUI";
  o.title = f ? `${f.unet}, ${f.clip}` : "Скачайте группу «krea» в Настройки → Проверка системы (~12 ГБ) или положите свои веса Krea-2 в папки ComfyUI";
}
const vmodeOf = p => p.mode === "multiview" ? "multi" : p.single_src === "crop" ? "none" : p.single_src === "text" ? "text" : "front";
function setVmode(p) {
  const v = vmodeOf(p);
  for (const r of $$("input[name=vmode]")) r.checked = r.value === v;
  $("#vmodeHint").textContent = VMODE[v].hint;
}
$$("input[name=vmode]").forEach(r => r.onchange = () => { setVmode(VMODE[r.value].params); patch({ params: VMODE[r.value].params }); });

function renderPipeline(m) {
  const v = vmodeOf(m.params);
  for (const s of STEPS) {
    const st = m.steps[s] || {}, el = $(`.pstep[data-step=${s}]`);
    let t = ST_RU[st.status] || "";
    if (st.status === "running" && st.started) t += " " + fmtTime(Date.now() / 1000 - st.started);
    else if (st.status === "done" && st.finished) t = "✓ " + fmtTime(st.finished - st.started);
    const chip = $(`[data-st=${s}]`);
    chip.className = "st " + (st.status || "");
    chip.textContent = t;
    chip.title = (st.error || "").slice(-300);
    const run = $(`[data-runslot=${s}]`), want = busy(m, s) ? "stop" : "run";
    if (run.dataset.k !== want) {  // не пересоздаём кнопку каждую секунду — иначе клик может «промахнуться»
      run.dataset.k = want;
      run.innerHTML = want === "stop" ? `<button class="stop" data-stop="${s}" title="остановить">■</button>`
        : `<button data-run="${s}" title="запустить шаг">▶</button>`;
    }
    el.classList.toggle("active", busy(m, s));
    el.classList.toggle("done", st.status === "done");
    el.classList.toggle("skip", s === "views" && v === "none");
  }
  $("[data-sub=views]").textContent = { front: "· фронт", multi: "· 4 вида", none: "· не нужны", text: "· по тексту" }[v];
  $("[data-sub=mesh]").textContent = v === "multi" ? "· Pixal3D multiview" : "";
  // по кадру фронт не генерируется; по тексту — своя модель (Klein или Krea-2), а износ описан в самом промпте
  $("#frontOpts").hidden = v === "none" || v === "text";
  $("#textOpts").hidden = v !== "text";
  if (v === "text") {
    checkKrea();
    $("[data-sub=views]").textContent += m.params.t2i_model === "krea2" ? " · Krea-2" : " · Klein";
  } else if (v !== "none") $("[data-sub=views]").textContent += m.params.views_engine === "klein" ? " · Klein" : " · Qwen";
  $("[data-p=model]").hidden = v === "multi";
  $("#meshFixed").hidden = v !== "multi";
}
$("#pipeline").addEventListener("click", e => {
  const r = e.target.closest("[data-run]"), l = e.target.closest("[data-log]"), stop = e.target.closest("[data-stop]");
  if (r) {
    const s = r.dataset.run;
    return s === "views" && cur.params.mode !== "multiview" ? run({ step: "views", view: "front" }) : run({ step: s });
  }
  if (l) return openLog(l.dataset.log);
  if (stop) {
    if (confirm("Остановить шаг? Сделанное в нём пропадёт.")) api("/api/cancel", json("POST", { pid: cur.id })).then(poll);
    return;
  }
  const head = e.target.closest(".ps-head");  // клик по заголовку шага — свернуть/развернуть его настройки
  if (head && !e.target.closest("button")) {
    const k = "ps-" + head.parentElement.dataset.step;
    layout.collapsed[k] = !layout.collapsed[k];
    applyLayout(); saveLayout();
  }
});

// ================= отрисовка состояния

function render(m) {
  if (!m) return;
  const f = m.files, multi = m.params.mode === "multiview";
  const stLast = STEPS.map(s => m.steps[s]).filter(Boolean).pop();
  $("#crumb").innerHTML = `<b></b>`; $("#crumb b").textContent = m.name;
  renderPipeline(m);
  renderDescribe(m);
  $("#runAll").disabled = STEPS.some(s => busy(m, s));
  // метки на вкладках — где уже есть результат
  const has = { crop: !!f.crop, views: Object.keys(f.views).length > 0, model: !!(f.lo || f.hi) };
  $$("#tabs button").forEach(b => b.classList.toggle("has", has[b.dataset.tab]));
  // кадр
  const ci = $("#cropImg");
  if (f.crop && ci.dataset.src !== f.crop) { ci.dataset.src = f.crop; ci.src = file(m, f.crop); }
  // рамки ещё нет — заметно на вкладке и над превью
  $("[data-tab=crop]").classList.toggle("warn", !m.cropped);
  $("#cropState").textContent = m.cropped ? "Кадр" : "Кадр не выделен — обведи предмет на фото";
  $("#cropState").classList.toggle("warn", !m.cropped);
  // виды
  const vs = $("#views");
  if (!vs.children.length) buildViews();
  for (const c of vs.children) {
    const v = c.dataset.v, src = f.views[v], box = $(".img", c);
    if (src && box.dataset.src !== src) { box.dataset.src = src; box.innerHTML = `<img src="${file(m, src)}" alt="">`; }
    $("input[type=checkbox]", c).checked = m.use_views[v] !== false;
    const used = multi || (v === "front" && ["front", "text"].includes(m.params.single_src));
    c.classList.toggle("off", m.use_views[v] === false || !used);
    const st = m.steps.views;
    c.classList.toggle("busy", busy(m, "views") && (!st.view || st.view === v));
  }
  $("[data-act=views]").disabled = busy(m, "views");
  // результат
  const maps = ["base", "normal", "ao"].filter(k => f[k]);
  $("#outGroup").hidden = !maps.length && !f.hi;
  $("#maps").innerHTML = maps.map(k =>
    `<a href="${file(m, f[k])}" download title="скачать PNG"><img src="${file(m, f[k])}" alt=""><span>${{ base: "Цвет", normal: "Нормали", ao: "AO" }[k]}</span><i>⬇</i></a>`).join("");
  const p = m.params;
  $("#resSum").innerHTML = f.lo ? `<span><b>${(+p.tris).toLocaleString("ru")}</b> тр.</span><span><b>${p.tex}</b> px</span>` +
    (p.height_cm ? `<span><b>${p.height_cm}</b> см</span>` : "") + `<span>${p.mode === "multiview" ? "Pixal3D · 4 вида" : { trellis2: "TRELLIS.2", pixal3d: "Pixal3D" }[p.model] || ""}</span>` : "";
  $("#exportBtn").disabled = !f.lo;
  $("#downloads").innerHTML = [["lo", "Лоу-поли", ".glb"], ["hi", "Хай-поли", ".glb"]]
    .filter(([k]) => f[k]).map(([k, t, ext]) => `<a href="${file(m, f[k])}" download><b>${t}</b><small>${ext}</small><i>⬇</i></a>`).join("");
  showModel();
  void stLast;
}

function buildViews() {
  for (const v of VIEWS) {
    const c = document.createElement("div");
    c.className = "view"; c.dataset.v = v;
    c.innerHTML = `<div class="img">нет вида</div><div class="bar"><input type="checkbox" title="использовать в 3D"><b>${VIEW_RU[v]}</b>
      <button class="ghost" title="перегенерировать с новым сидом">↻</button><button class="ghost" title="заменить своим фото">⤒</button><input type="file" accept="image/*" hidden></div>`;
    const chk = $(".bar input[type=checkbox]", c), [regen, up] = $$(".bar button", c), fileInp = $(".bar input[type=file]", c);
    chk.onchange = () => patch({ use_views: { [v]: chk.checked } });
    regen.onclick = () => run({ step: "views", view: v, seed: 1 + Math.floor(Math.random() * 99999) });
    up.onclick = () => fileInp.click();
    fileInp.onchange = async () => {
      const fd = new FormData(); fd.append("file", fileInp.files[0]);
      cur = await api(`/api/props/${cur.id}/view/${v}`, { method: "POST", body: fd }); render(cur);
    };
    $("#views").append(c);
  }
}

// ================= кадр

function setCropper() {
  const cv = $("#cropper canvas"), ctx = cv.getContext("2d");
  $$("#photos img").forEach((im, i) => im.classList.toggle("on", i === photo));
  const img = new Image();
  img.onload = () => {
    const W = cv.width = img.naturalWidth, H = cv.height = img.naturalHeight;
    const saved = cur.crop_rect;  // прежняя рамка этого фото — видна сразу, её можно поправить
    crop = saved && saved.photo === photo && !saved.full ? { x: saved.x, y: saved.y, w: saved.w, h: saved.h } : null;
    let hover = null, drag = null;
    const px = () => W / cv.getBoundingClientRect().width;  // пикселей картинки в одном пикселе экрана
    const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
    const handlePos = (r, h) => [r.x + r.w * (h.includes("w") ? 0 : h.includes("e") ? 1 : 0.5),
                                 r.y + r.h * (h.includes("n") ? 0 : h.includes("s") ? 1 : 0.5)];
    const CURSOR = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize", n: "ns-resize", s: "ns-resize",
                     e: "ew-resize", w: "ew-resize", move: "move" };
    const draw = () => {
      const s = px();
      ctx.setLineDash([]);
      ctx.drawImage(img, 0, 0);
      if (crop) {
        const { x, y, w, h } = crop;
        ctx.fillStyle = "rgba(0,0,0,.6)";
        ctx.fillRect(0, 0, W, y); ctx.fillRect(0, y + h, W, H - y - h);
        ctx.fillRect(0, y, x, h); ctx.fillRect(x + w, y, W - x - w, h);
        ctx.strokeStyle = "#ff9a1f"; ctx.lineWidth = 2 * s; ctx.strokeRect(x, y, w, h);
        const hs = 9 * s;  // ручки: углы и середины сторон
        for (const hd of HANDLES) {
          const [hx, hy] = handlePos(crop, hd);
          ctx.fillStyle = "#fff"; ctx.fillRect(hx - hs / 2, hy - hs / 2, hs, hs);
          ctx.strokeStyle = "#ff9a1f"; ctx.lineWidth = 1.5 * s; ctx.strokeRect(hx - hs / 2, hy - hs / 2, hs, hs);
        }
      }
      if (hover) {  // направляющие через курсор: проще попасть краем рамки в край предмета
        const [gx, gy] = hover;
        ctx.setLineDash([3 * s, 6 * s]); ctx.lineWidth = 1 * s;  // тонко и полупрозрачно — подсказка, а не прицел
        ctx.strokeStyle = "rgba(255,255,255,.38)";
        ctx.beginPath();
        ctx.moveTo(gx, 0); ctx.lineTo(gx, H);
        ctx.moveTo(0, gy); ctx.lineTo(W, gy);
        ctx.stroke();
      }
    };
    const hit = p => {  // что под курсором: ручка, внутренность рамки или ничего
      if (!crop) return null;
      const tol = 10 * px();
      for (const hd of HANDLES) {
        const [hx, hy] = handlePos(crop, hd);
        if (Math.abs(p[0] - hx) <= tol && Math.abs(p[1] - hy) <= tol) return hd;
      }
      const { x, y, w, h } = crop, inX = p[0] > x && p[0] < x + w, inY = p[1] > y && p[1] < y + h;
      if (inY && Math.abs(p[0] - x) <= tol) return "w";
      if (inY && Math.abs(p[0] - x - w) <= tol) return "e";
      if (inX && Math.abs(p[1] - y) <= tol) return "n";
      if (inX && Math.abs(p[1] - y - h) <= tol) return "s";
      return inX && inY ? "move" : null;
    };
    const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
    const pt = e => { const r = cv.getBoundingClientRect(); return [clamp((e.clientX - r.left) * W / r.width, 0, W), clamp((e.clientY - r.top) * H / r.height, 0, H)]; };
    const norm = (x1, y1, x2, y2) => ({ x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) });
    draw();
    cv.onpointerdown = e => {
      if (e.button !== 0) return;
      const p = pt(e), mode = hit(p);
      drag = { mode: mode || "new", p0: p, r0: crop && { ...crop } };
      cv.setPointerCapture(e.pointerId);
    };
    cv.onpointermove = e => {
      const p = pt(e);
      hover = p;
      if (drag) {
        const { mode, p0, r0 } = drag;
        if (mode === "new") crop = norm(p0[0], p0[1], p[0], p[1]);
        else if (mode === "move") {
          crop = { ...r0, x: clamp(r0.x + p[0] - p0[0], 0, W - r0.w), y: clamp(r0.y + p[1] - p0[1], 0, H - r0.h) };
        } else {  // тянем за край или угол
          let x1 = r0.x, y1 = r0.y, x2 = r0.x + r0.w, y2 = r0.y + r0.h;
          if (mode.includes("w")) x1 = p[0]; if (mode.includes("e")) x2 = p[0];
          if (mode.includes("n")) y1 = p[1]; if (mode.includes("s")) y2 = p[1];
          crop = norm(x1, y1, x2, y2);
        }
      } else cv.style.cursor = CURSOR[hit(p)] || "crosshair";
      draw();
    };
    cv.onpointerleave = () => { if (!drag) { hover = null; draw(); } };
    cv.onpointerup = () => {  // рамка сохраняется сама — отдельной кнопки, о которой легко забыть, нет
      if (!drag) return;
      const moved = drag.mode !== "new" || (crop && crop.w >= 16 && crop.h >= 16);
      drag = null;
      if (crop && crop.w >= 16 && crop.h >= 16 && moved) saveCrop();
      else if (crop && (crop.w < 16 || crop.h < 16)) { crop = null; draw(); }  // случайный клик — не рамка
    };
  };
  img.src = file(cur, cur.files.photos[photo]);
}

async function saveCrop(full = false) {
  try {
    cur = await api(`/api/props/${cur.id}/crop`, json("POST", full ? { photo, full: true } : { photo, ...crop }));
  } catch (e) { return alert(e.message); }
  render(cur); loadList();
  toast(full ? "Кадр — целое фото" : "Кадр сохранён");
}

// ================= лог

async function openLog(step) {
  logStep = step;
  $("#drawer").hidden = false;
  $$("#logTabs button").forEach(b => b.classList.toggle("on", b.dataset.log === step));
  requestAnimationFrame(resizeViewer);
  await refreshLog();
}
async function refreshLog() {
  if (!cur || !logStep || $("#drawer").hidden) return;
  const { log } = await api(`/api/props/${cur.id}/log/${logStep}`);
  const pre = $("#log"), atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  pre.textContent = log.split("\n").filter(l => !/it\/s\]|s\/it\]/.test(l)).slice(-200).join("\n") || "(лог пуст)";
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}
$$("#logTabs button").forEach(b => b.onclick = () => openLog(b.dataset.log));
$("#drawerClose").onclick = () => { $("#drawer").hidden = true; logStep = null; requestAnimationFrame(resizeViewer); };
$("#logToggle").onclick = () => $("#drawer").hidden ? cur && openLog(logStep || "views") : $("#drawerClose").click();

// ================= 3D. Просмотрщик живёт, только пока он нужен: открыта вкладка «Модель» и ничего не генерируется.
// Иначе выгружается целиком — WebGL-контекст, буферы, текстуры, панорамы — и видеопамять возвращается ComfyUI
// (на 8 ГБ даже сотни мегабайт у браузера выталкивали его в «общую» память Windows — генерация замедлялась в 10–20 раз).
// Камера, модель, панорама и сетка при новом создании восстанавливаются.

const viewer = { box: $("#viewer"), alive: false, show: "lo", obj: null, url: null, needs: true, envs: {}, off: false,
  camPos: new THREE.Vector3(1.7, 1.5, 2.4), camTarget: new THREE.Vector3(0, 0.6, 0) };

function createViewer() {
  const v = viewer;
  if (v.alive) return;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(devicePixelRatio);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  v.box.append(renderer.domElement);
  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
  cam.position.copy(v.camPos);
  const ctl = new OrbitControls(cam, renderer.domElement);
  ctl.enableDamping = true;
  ctl.target.copy(v.camTarget);
  const hemi = new THREE.HemisphereLight(0xffffff, 0x404040, 1.5); scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xffffff, 2.2); sun.position.set(2, 3, 2); scene.add(sun);
  const fill = new THREE.DirectionalLight(0x9fb4ff, 0.6); fill.position.set(-2, 1, -2); scene.add(fill);
  const grid = new THREE.GridHelper(4, 16, 0x555555, 0x333333); scene.add(grid);
  // тень на полу: «ловец теней» — прозрачная плоскость, видна только тень
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -1.2, right: 1.2, top: 1.2, bottom: -1.2, near: 0.1, far: 10 });
  sun.shadow.bias = -0.0005;
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(6, 6), new THREE.ShadowMaterial({ opacity: 0.35 }));
  floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
  Object.assign(v, { renderer, scene, cam, ctl, hemi, sun, fill, grid, pmrem: new THREE.PMREMGenerator(renderer),
    envs: {}, obj: null, url: null, needs: true, alive: true });
  ctl.addEventListener("change", () => { v.needs = true; });
  ctl.addEventListener("start", () => { fly = null; });  // пользователь сам крутит — перелёт отменяется
  ctl.update();
  resizeViewer();
  setEnv($("#env").value);
  showModel();
}

function destroyViewer() {
  const v = viewer;
  if (!v.alive) return;
  v.camPos.copy(v.cam.position); v.camTarget.copy(v.ctl.target);
  v.ctl.dispose();
  v.scene.traverse(o => {
    o.geometry?.dispose();
    for (const m of [].concat(o.material || [])) {
      for (const k in m) if (m[k]?.isTexture) m[k].dispose();
      m.dispose();
    }
  });
  for (const e of Object.values(v.envs)) { e.bg.dispose(); e.env.dispose(); }
  v.scene.background = v.scene.environment = null;
  v.pmrem.dispose();
  v.renderer.dispose();
  v.renderer.forceContextLoss();  // отдать контекст сразу, не дожидаясь сборщика мусора
  v.renderer.domElement.remove();
  Object.assign(v, { alive: false, renderer: null, scene: null, cam: null, ctl: null, pmrem: null, obj: null, url: null, envs: {} });
  $("#tris").textContent = "";
}

// нужен ли просмотрщик прямо сейчас — и подсказка, почему его нет
function syncViewer() {
  const busyGpu = !!queue.current, want = tab === "model" && !!cur && !busyGpu && !viewer.off;
  if (want) createViewer(); else destroyViewer();
  const note = $("#vpause");
  note.hidden = tab !== "model" || !cur || want;
  note.innerHTML = busyGpu ? "⏏ просмотр выгружен — видеопамять отдана генерации"
    : '⏏ просмотр выгружен из памяти · <button class="link" id="vShow">показать</button>';
  $("#vShow")?.addEventListener("click", () => { viewer.off = false; syncViewer(); });
  $("#vUnload").classList.toggle("on", viewer.off);
}

(function loop() {  // рисуем только по необходимости
  requestAnimationFrame(loop);
  const v = viewer;
  if (!v.alive || document.hidden) return;
  const moving = v.ctl.update();  // true, пока докатывается инерция
  if (moving || v.needs) { v.renderer.render(v.scene, v.cam); v.needs = false; }
})();

function resizeViewer() {
  if (!viewer.alive) return;
  const w = viewer.box.clientWidth, h = viewer.box.clientHeight;
  if (!w || !h) return;
  viewer.renderer.setSize(w, h); viewer.cam.aspect = w / h; viewer.cam.updateProjectionMatrix();
  viewer.needs = true;
}
new ResizeObserver(resizeViewer).observe(viewer.box);
$("#vUnload").onclick = () => { viewer.off = !viewer.off; syncViewer(); };
$$("#showSeg button").forEach(b => b.onclick = () => {
  viewer.show = b.dataset.show;
  $$("#showSeg button").forEach(x => x.classList.toggle("on", x === b));
  showModel();
});
$("#wire").onchange = setWire;

// ---- окружение: HDRI-панорама (Poly Haven, CC0, web/hdri: 1k для света, 4k для фона) — фон и освещение модели
const store = { get: k => { try { return localStorage.getItem(k); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
async function setEnv(name) {
  const v = viewer;
  store.set("propgen.env", name);
  if (!v.alive) return;  // применится при создании просмотрщика
  v.needs = true;
  const scene = v.scene;
  if (!name) {
    v.scene.environment = null; v.scene.background = null;
    v.renderer.toneMapping = THREE.NoToneMapping;
    v.hemi.intensity = 1.5; v.sun.intensity = 2.2; v.fill.intensity = 0.6; v.grid.visible = true;
    return;
  }
  if (!v.envs[name]) {
    $("#vnote").hidden = false; $("#vnote").textContent = "загрузка окружения…";
    const tex = await new RGBELoader().loadAsync(`hdri/${name}_1k.hdr`);
    if (!v.alive || v.scene !== scene) return tex.dispose();  // пока грузилось, просмотрщик выгрузили
    tex.mapping = THREE.EquirectangularReflectionMapping;
    v.envs[name] = { bg: tex, env: v.pmrem.fromEquirectangular(tex).texture };
    $("#vnote").hidden = !!v.obj;
  }
  if (store.get("propgen.env") !== name || v.scene !== scene) return;  // пока грузилось, выбрали другое
  v.scene.environment = v.envs[name].env;
  v.scene.background = v.envs[name].bg;
  // освещению хватает 1k, а фону на весь экран — нет: догружаем 4k и подменяем только задний план
  if (!v.envs[name].hd) {
    v.envs[name].hd = true;
    new RGBELoader().loadAsync(`hdri/${name}_4k.hdr`).then(tex => {
      if (!v.alive || v.scene !== scene || !v.envs[name]) return tex.dispose();
      tex.mapping = THREE.EquirectangularReflectionMapping;
      v.envs[name].bg.dispose();
      v.envs[name].bg = tex;
      if (store.get("propgen.env") === name) { v.scene.background = tex; v.needs = true; }
    }).catch(() => { if (v.envs[name]) v.envs[name].hd = false; });
  }
  v.scene.backgroundBlurriness = +$("#envBlur").value;
  v.scene.backgroundIntensity = 0.55;  // фон тусклее света, чтобы модель не терялась на ярких окнах
  v.renderer.toneMapping = THREE.ACESFilmicToneMapping;
  v.renderer.toneMappingExposure = 1.0;
  // свет даёт панорама; свой оставляем слабым ключом, чтобы читалась форма
  v.hemi.intensity = 0; v.sun.intensity = 1.0; v.fill.intensity = 0; v.grid.visible = false;
}
$("#env").onchange = () => setEnv($("#env").value);
$("#envBlur").oninput = () => { if (viewer.alive) { viewer.scene.backgroundBlurriness = +$("#envBlur").value; viewer.needs = true; } store.set("propgen.envBlur", $("#envBlur").value); };
$("#envBlur").value = store.get("propgen.envBlur") ?? 0;
$("#env").value = store.get("propgen.env") ?? "studio_small_09";
setEnv($("#env").value);

function showModel() {
  if (!viewer.alive) return;
  const rel = cur?.files[viewer.show], note = $("#vnote");
  if (!rel) { note.hidden = false; note.textContent = viewer.show === "lo" ? "лоу-поли ещё нет" : "хай-поли ещё нет"; clearObj(); viewer.url = null; return; }
  const url = file(cur, rel);
  if (viewer.url === url) return;
  viewer.url = url; note.hidden = false; note.textContent = "загрузка…";
  new GLTFLoader().load(url, g => {
    if (!viewer.alive || viewer.url !== url) return;
    clearObj();
    const obj = g.scene;
    const bb = new THREE.Box3().setFromObject(obj), size = bb.getSize(new THREE.Vector3()), c = bb.getCenter(new THREE.Vector3());
    const s = 1.2 / Math.max(size.x, size.y, size.z);
    obj.scale.setScalar(s); obj.position.set(-c.x * s, -bb.min.y * s, -c.z * s);
    viewer.ctl.target.set(0, size.y * s / 2, 0);  // смотрим в середину предмета, а не в пол
    obj.traverse(o => { if (o.isMesh) o.castShadow = true; });
    viewer.scene.add(obj); viewer.obj = obj; viewer.needs = true;
    let tris = 0;
    obj.traverse(o => { if (o.isMesh) tris += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3; });
    $("#tris").textContent = `${Math.round(tris).toLocaleString("ru")} тр.`;
    note.hidden = true;
    setWire();
  }, undefined, () => { note.textContent = "не удалось загрузить модель"; });
}
function clearObj() {
  if (viewer.alive && viewer.obj) { viewer.scene.remove(viewer.obj); viewer.obj = null; viewer.needs = true; }
  $("#tris").textContent = "";
}
function setWire() {
  if (!viewer.alive) return;
  const on = $("#wire").checked;
  viewer.obj?.traverse(o => {
    if (!o.isMesh) return;
    if (on && !o.userData.wire) {
      const w = new THREE.LineSegments(new THREE.WireframeGeometry(o.geometry), new THREE.LineBasicMaterial({ color: 0x111111, transparent: true, opacity: .55 }));
      o.add(w); o.userData.wire = w;
    }
    if (o.userData.wire) o.userData.wire.visible = on;
  });
  viewer.needs = true;
}

// ================= новый проп

const dlg = $("#newDlg");
let pending = [];
function openNew(files = []) {
  pending = []; $("#newThumbs").innerHTML = ""; $("#newName").value = ""; $("#newDesc").value = "";
  addFiles(files);
  dlg.showModal();
}
function addFiles(fs) {
  for (const f of fs.filter(f => f.type.startsWith("image/"))) {
    pending.push(f);
    const img = new Image(); img.src = URL.createObjectURL(f); $("#newThumbs").append(img);
  }
  $("#newOk").disabled = !pending.length;
}
$("#newBtn").onclick = () => openNew();
$("#pick").onclick = e => { e.preventDefault(); $("#newFiles").click(); };
$("#newFiles").onchange = () => addFiles([...$("#newFiles").files]);
const dz = $("#dz");
dz.ondragover = e => { e.preventDefault(); dz.classList.add("over"); };
dz.ondragleave = () => dz.classList.remove("over");
dz.ondrop = e => { e.preventDefault(); e.stopPropagation(); dz.classList.remove("over"); addFiles([...e.dataTransfer.files]); };
$("#newForm").onsubmit = async e => {
  if (e.submitter?.value !== "ok") return;
  e.preventDefault();
  const fd = new FormData();
  fd.append("name", $("#newName").value); fd.append("desc", $("#newDesc").value);
  for (const f of pending) fd.append("files", f);
  $("#newOk").disabled = true;
  try {
    const p = await api("/api/props", { method: "POST", body: fd });
    dlg.close();
    await loadList();
    await select(p.id);
    setTab("crop");
  } catch (err) { alert(err.message); $("#newOk").disabled = false; }
};
// фото можно бросить прямо в окно
let dragDepth = 0;
const hasFiles = e => [...(e.dataTransfer?.types || [])].includes("Files");
addEventListener("dragenter", e => { if (hasFiles(e) && !dlg.open) { dragDepth++; $(".app").classList.add("drag"); } });
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $(".app").classList.remove("drag"); } });
addEventListener("dragover", e => { if (hasFiles(e)) e.preventDefault(); });
addEventListener("drop", e => {
  if (!hasFiles(e) || dlg.open) return;
  e.preventDefault(); dragDepth = 0; $(".app").classList.remove("drag");
  openNew([...e.dataTransfer.files]);
});

// ================= вставка из буфера (скриншоты Google Maps и т.п.)

function toast(text) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = text;
  document.body.append(t);
  setTimeout(() => t.remove(), 2700);
}
addEventListener("paste", async e => {
  const imgs = [...(e.clipboardData?.items || [])].filter(i => i.kind === "file" && i.type.startsWith("image/"))
    .map((i, k) => { const f = i.getAsFile(); return new File([f], `clipboard-${Date.now()}-${k}.${(f.type.split("/")[1] || "png")}`, { type: f.type }); });
  if (!imgs.length) return;  // обычный текст — в поле ввода как обычно
  e.preventDefault();
  if (dlg.open) return addFiles(imgs);
  if (cur && tab === "crop") {
    const fd = new FormData();
    imgs.forEach(f => fd.append("files", f));
    try {
      const m = await api(`/api/props/${cur.id}/photos`, { method: "POST", body: fd });
      const n = m.files.photos.length;
      await select(m.id);
      setTab("crop");
      photo = n - 1; setCropper();
      toast(imgs.length > 1 ? `Добавлено фото: ${imgs.length}` : "Фото добавлено — обведи предмет рамкой");
    } catch (err) { alert(err.message); }
    return;
  }
  openNew(imgs);
});

// ================= опрос

// ---- живой прогресс ComfyUI: нода, шаг выборки, сколько стоим на шаге — видно, если завис
let comfySnap = null, comfyAt = 0;
const NODE_RU = { KSampler: "выборка", TextEncodeQwenImageEditPlus: "энкодер Qwen-VL", VAEDecode: "декодер", VAEEncode: "кодировщик",
  UnetLoaderGGUF: "загрузка модели", UNETLoader: "загрузка модели", CLIPLoader: "загрузка энкодера", VAELoader: "загрузка VAE",
  LoraLoaderModelOnly: "LoRA", SaveImage: "сохранение", SaveGLB: "сохранение", LoadImage: "загрузка фото",
  RemoveBackground: "вырезка фона", Trellis2Conditioning: "DINOv3", Pixal3DConditioning: "DINOv3", Pixal3DMultiViewConditioning: "DINOv3",
  VaeDecodeShapeTrellis: "сетка", VaeDecodeTextureTrellis: "текстура", RemeshMesh: "ремеш", DecimateMesh: "декимация",
  UnwrapMesh: "развёртка", BakeTextureFromVoxel: "запекание", MoGeInference: "MoGe" };
function renderComfy() {
  const el = $("#comfyProg"), c = comfySnap;
  if (!c || (!c.prompt_id && !c.node)) { el.hidden = true; return; }
  el.hidden = false;
  const now = c.now + (Date.now() / 1000 - comfyAt);
  const label = NODE_RU[c.cls] || c.cls || "нода " + c.node;
  const step = c.max ? ` ${c.value}/${c.max}` : "";
  const idle = c.last_event ? now - c.last_event : 0;
  el.classList.toggle("stall", idle > 120);
  el.innerHTML = `Comfy: <b>${label}${step}</b>` +
    (c.node_since ? ` · ${fmtTime(now - c.node_since)} на ноде` : "") +
    (c.started ? ` · всего ${fmtTime(now - c.started)}` : "") +
    (idle > 120 ? ` · <span class="warn">⚠ без событий ${fmtTime(idle)}</span>` : "") +
    (c.max ? `<i class="bar"><i style="width:${Math.round(100 * c.value / c.max)}%"></i></i>` : "");
}
setInterval(renderComfy, 1000);

let pollTimer = null, healthAt = 0;
async function poll() {
  clearTimeout(pollTimer);
  try {
    queue = await api("/api/queue");
    const q = $("#queue");
    if (queue.current) {
      const p = props.find(x => x.id === queue.current.pid);
      q.innerHTML = `<span class="dot run"></span>сейчас: <b>${STEP_RU[queue.current.step]}${queue.current.view ? " (" + VIEW_RU[queue.current.view] + ")" : ""}</b> — ${p ? p.name : ""}` +
        (queue.queued.length ? ` · ещё в очереди: ${queue.queued.length}` : "");
    } else q.innerHTML = `<span class="dot"></span>очередь пуста`;
    comfySnap = queue.comfy; comfyAt = Date.now() / 1000; renderComfy();
    syncViewer();
    if (Date.now() - healthAt > 10000) {
      healthAt = Date.now();
      const h = await api("/api/health");
      $("#health").innerHTML = h.comfy
        ? `<span class="dot ok"></span>Comfy · VRAM ${((h.vram_total - h.vram_free) / 2 ** 30).toFixed(1)} / ${(h.vram_total / 2 ** 30).toFixed(1)} ГБ`
        : `<span class="dot bad"></span>Comfy не запущен`;
      if (h.openai?.key)  // расход на тексты за сегодня — следить за экономией
        $("#health").innerHTML += ` · <span title="модель ${h.openai.model}, дневной лимит $${h.openai.limit}">OpenAI $${h.openai.today.toFixed(4)} сегодня</span>`;
    }
    if (cur) {
      const fresh = await api(`/api/props/${cur.id}`);
      const changed = JSON.stringify(fresh.steps) !== JSON.stringify(cur.steps) || JSON.stringify(fresh.files) !== JSON.stringify(cur.files);
      const wasNoModel = !(cur.files.lo || cur.files.hi);
      cur = fresh;
      render(cur);
      if (changed) loadList();
      if (wasNoModel && (cur.files.lo || cur.files.hi) && tab !== "crop") setTab("model");
      const running = queue.current?.pid === cur.id ? queue.current.step : null;
      if (running && !$("#drawer").hidden && logStep !== running) openLog(running); else refreshLog();
    }
  } catch (e) { $("#queue").innerHTML = `<span class="dot bad"></span>сервер недоступен`; }
  pollTimer = setTimeout(poll, queue.current || queue.queued.length ? 2000 : 6000);
}

// ================= раскладка: ширина колонок, свёрнутые панели и блоки (запоминается)

const app = $(".app");
let layout = { side: 236, insp: 304, noSide: false, noInsp: false, collapsed: {} };
try { Object.assign(layout, JSON.parse(store.get("propgen.layout") || "{}")); } catch {}
const saveLayout = () => store.set("propgen.layout", JSON.stringify(layout));
function applyLayout() {
  app.style.setProperty("--side-w", layout.noSide ? "0px" : layout.side + "px");
  app.style.setProperty("--insp-w", layout.noInsp ? "0px" : layout.insp + "px");
  app.classList.toggle("no-side", layout.noSide);
  app.classList.toggle("no-insp", layout.noInsp);
  $("#tglSide").classList.toggle("off", layout.noSide);
  $("#tglInsp").classList.toggle("off", layout.noInsp);
  $$(".gh[data-g]").forEach(h => h.closest(".group").classList.toggle("collapsed", !!layout.collapsed[h.dataset.g]));
  $$(".pstep").forEach(el => el.classList.toggle("folded", !!layout.collapsed["ps-" + el.dataset.step]));
}
const togglePanel = which => { layout[which === "side" ? "noSide" : "noInsp"] ^= true; layout.noSide = !!layout.noSide; layout.noInsp = !!layout.noInsp; applyLayout(); saveLayout(); };
$("#tglSide").onclick = () => togglePanel("side");
$("#tglInsp").onclick = () => togglePanel("insp");
addEventListener("keydown", e => {
  if (e.ctrlKey && (e.key === "b" || e.key === "и" || e.code === "KeyB")) { e.preventDefault(); togglePanel(e.altKey ? "insp" : "side"); }
});
const LIM = { side: [170, 460], insp: [250, 560] };
$$(".split").forEach(sp => {
  const which = sp.dataset.split;
  sp.onpointerdown = e => {
    if (e.button !== 0) return;
    sp.setPointerCapture(e.pointerId);
    sp.classList.add("drag"); app.classList.add("resizing");
    const r = app.getBoundingClientRect();
    sp.onpointermove = ev => {
      const w = which === "side" ? ev.clientX - r.left - 12 : r.right - ev.clientX - 12;
      const [lo, hi] = LIM[which];
      const hidden = w < lo * 0.55;  // утащил почти до края — колонка сворачивается
      layout[which === "side" ? "noSide" : "noInsp"] = hidden;
      if (!hidden) layout[which] = Math.round(Math.min(hi, Math.max(lo, w)));
      applyLayout();
    };
    sp.onpointerup = () => { sp.onpointermove = null; sp.classList.remove("drag"); app.classList.remove("resizing"); saveLayout(); };
  };
  sp.ondblclick = () => togglePanel(which);
});
$$(".gh[data-g]").forEach(h => h.onclick = () => { layout.collapsed[h.dataset.g] = !layout.collapsed[h.dataset.g]; applyLayout(); saveLayout(); });
applyLayout();
// высота полей заметки и промпта — тянется за угол, запоминается
layout.ta = layout.ta || {};
for (const ta of $$(".group textarea[data-f]")) {
  const k = ta.dataset.f;
  if (layout.ta[k]) ta.style.height = layout.ta[k] + "px";
  new ResizeObserver(() => {
    if (!ta.offsetHeight || !ta.style.height) return;  // скрыто или ещё не тянули
    layout.ta[k] = ta.offsetHeight; saveLayout();
  }).observe(ta);
}

// ================= ViewCube: куб повторяет поворот камеры, клик по грани — плавный перелёт к ракурсу

const vcCube = $("#vcCube"), vcM = new THREE.Matrix4(), vcQ = new THREE.Quaternion();
const DIRS = { front: [0, 0, 1], back: [0, 0, -1], right: [1, 0, 0], left: [-1, 0, 0], top: [0, 1, 0], bottom: [0, -1, 0], iso: [1, 0.72, 1.3] };
let fly = null;
function flyTo(name) {
  if (!viewer.alive) return;
  const { cam, ctl } = viewer;
  const from = new THREE.Spherical().setFromVector3(cam.position.clone().sub(ctl.target));
  let toTarget = ctl.target.clone(), radius = from.radius;
  if (name === "fit") {
    if (!viewer.obj) return;
    const sph = new THREE.Box3().setFromObject(viewer.obj).getBoundingSphere(new THREE.Sphere());
    toTarget = sph.center; radius = sph.radius / Math.sin(THREE.MathUtils.degToRad(cam.fov) / 2) * 1.08;
  }
  const to = name === "fit" ? from.clone() : new THREE.Spherical().setFromVector3(new THREE.Vector3(...DIRS[name]));
  to.radius = radius;
  if (name === "top" || name === "bottom") to.theta = from.theta;  // сверху/снизу — только наклон, без раскрутки
  to.phi = Math.min(Math.PI - 1e-3, Math.max(1e-3, to.phi));
  let dTheta = ((to.theta - from.theta + 3 * Math.PI) % (2 * Math.PI)) - Math.PI;  // кратчайший путь по кругу
  fly = { t0: performance.now(), from, dTheta, dPhi: to.phi - from.phi, dR: to.radius - from.radius,
          t1: ctl.target.clone(), t2: toTarget };
}
function navLoop(t) {
  requestAnimationFrame(navLoop);
  if (!viewer.alive) return;
  const { cam, ctl } = viewer;
  if (fly) {
    const k = Math.min(1, (t - fly.t0) / 480), e = 1 - Math.pow(1 - k, 3);
    const sp = new THREE.Spherical(fly.from.radius + fly.dR * e, fly.from.phi + fly.dPhi * e, fly.from.theta + fly.dTheta * e);
    ctl.target.lerpVectors(fly.t1, fly.t2, e);
    cam.position.setFromSpherical(sp).add(ctl.target);
    cam.lookAt(ctl.target);
    viewer.needs = true;
    if (k >= 1) fly = null;
  }
  // поворот мира в координатах камеры → CSS (ось Y в CSS смотрит вниз: меняем знак у смешанных с Y элементов)
  vcM.makeRotationFromQuaternion(vcQ.copy(cam.quaternion).invert());
  const m = vcM.elements.slice();
  m[1] = -m[1]; m[4] = -m[4]; m[6] = -m[6]; m[9] = -m[9];
  vcCube.style.transform = `matrix3d(${m.map(x => x.toFixed(6)).join(",")})`;
}
requestAnimationFrame(navLoop);
$$(".vc-face, .vc-btn").forEach(el => el.onclick = () => flyTo(el.dataset.dir));

// ================= вид крупно: зум колесом к курсору, перетаскивание, стрелки, Esc

const lb = $("#lightbox"), lbImg = $("#lbImg"), lbStage = $("#lbStage");
const lbs = { list: [], i: 0, k: 1, x: 0, y: 0, drag: null };
function openViewer(list, i) {
  if (!list.length) return;
  Object.assign(lbs, { list, i });
  lb.hidden = false;
  showLb();
}
function showLb() {
  const it = lbs.list[lbs.i];
  $("#lbLabel").textContent = lbs.list.length > 1 ? `${it.label} · ${lbs.i + 1}/${lbs.list.length}` : it.label;
  lbImg.onload = fitLb;
  lbImg.src = it.src;
  $("#lbPrev").disabled = $("#lbNext").disabled = lbs.list.length < 2;
}
function fitLb() {
  const W = lbStage.clientWidth, H = lbStage.clientHeight, w = lbImg.naturalWidth, h = lbImg.naturalHeight;
  lbs.k = Math.min(W / w, H / h) * 0.94;
  lbs.x = (W - w * lbs.k) / 2; lbs.y = (H - h * lbs.k) / 2;
  applyLb();
}
function applyLb() {
  lbImg.style.width = lbImg.naturalWidth + "px";
  lbImg.style.transform = `translate(${lbs.x}px, ${lbs.y}px) scale(${lbs.k})`;
}
function zoomAt(mx, my, k) {
  k = Math.min(16, Math.max(0.05, k));
  lbs.x = mx - (mx - lbs.x) * (k / lbs.k); lbs.y = my - (my - lbs.y) * (k / lbs.k); lbs.k = k;
  applyLb();
}
lbStage.addEventListener("wheel", e => {
  e.preventDefault();
  const r = lbStage.getBoundingClientRect();
  zoomAt(e.clientX - r.left, e.clientY - r.top, lbs.k * Math.exp(-e.deltaY * 0.0015));
}, { passive: false });
lbStage.onpointerdown = e => { lbs.drag = [e.clientX - lbs.x, e.clientY - lbs.y]; lbStage.setPointerCapture(e.pointerId); lbStage.classList.add("drag"); };
lbStage.onpointermove = e => { if (lbs.drag) { lbs.x = e.clientX - lbs.drag[0]; lbs.y = e.clientY - lbs.drag[1]; applyLb(); } };
lbStage.onpointerup = () => { lbs.drag = null; lbStage.classList.remove("drag"); };
lbStage.ondblclick = e => {  // 100% у курсора ↔ вписать
  const r = lbStage.getBoundingClientRect();
  if (Math.abs(lbs.k - 1) < 0.01) fitLb(); else zoomAt(e.clientX - r.left, e.clientY - r.top, 1);
};
const lbStep = d => { lbs.i = (lbs.i + d + lbs.list.length) % lbs.list.length; showLb(); };
$("#lbPrev").onclick = () => lbStep(-1);
$("#lbNext").onclick = () => lbStep(1);
$("#lbClose").onclick = () => { lb.hidden = true; };
addEventListener("keydown", e => {
  if (lb.hidden) return;
  if (e.key === "Escape") lb.hidden = true;
  if (e.key === "ArrowLeft") lbStep(-1);
  if (e.key === "ArrowRight") lbStep(1);
});
addEventListener("resize", () => { if (!lb.hidden) fitLb(); });
$("#views").addEventListener("click", e => {
  const img = e.target.closest(".img img");
  if (!img || !cur) return;
  const have = VIEWS.filter(v => cur.files.views[v]);
  const v = img.closest(".view").dataset.v;
  openViewer(have.map(x => ({ src: file(cur, cur.files.views[x]), label: VIEW_RU[x] })), Math.max(0, have.indexOf(v)));
});
$("#cropImg").addEventListener("click", () => cur?.files.crop && openViewer([{ src: file(cur, cur.files.crop), label: "кадр" }], 0));

// ================= настройки и проверка системы
// Окно в две панели: слева разделы с точкой-состоянием (по /api/doctor и проверке обновлений), справа — один раздел.
// Поля всех разделов живут в одном диалоге, поэтому «Сохранить» по-прежнему сохраняет всё разом.

const setDlg = $("#setDlg");
let settings = null, setSec = "system";
const MODE_HINT = {
  auto: "OpenAI, если есть ключ (секунды, ~$0.0003 за проп); иначе — локальная модель через LM Studio.",
  openai: "Только OpenAI. Без ключа или сверх дневного лимита заметка не переводится.",
  local: "Только LM Studio на этой машине: бесплатно, без интернета, ~10 с, ненадолго занимает видеокарту.",
  off: "Без текстовой модели: генератор получит заметку как есть — пиши её сразу по-английски.",
};
function showSec(sec) {
  if (!$(`#setNav [data-sec="${sec}"]`)) sec = "system";
  setSec = sec;
  $$("#setNav [data-sec]").forEach(b => b.classList.toggle("on", b.dataset.sec === sec));
  $$("#setDlg .set-sec").forEach(s => s.hidden = s.dataset.sec !== sec);
  $("#setPane").scrollTop = 0;
}
$$("#setNav [data-sec]").forEach(b => b.onclick = () => showSec(b.dataset.sec));
const SDOT_TITLE = { ok: "всё в порядке", bad: "нужно поправить", opt: "чего-то необязательного нет", new: "есть новая версия" };
function setDot(sec, state = "") {
  const d = $(`#setNav [data-sec="${sec}"] .sdot`);
  d.className = "sdot " + state; d.title = SDOT_TITLE[state] || "";
}
// sec — раздел: "system" · "llm" · "apps" · "update"; без него откроется тот, что смотрели в прошлый раз
async function openSettings(note = "", sec = "") {
  $("#setNote").textContent = note;
  showSec(sec || setSec);
  if (!setDlg.open) { setDlg.showModal(); document.activeElement?.blur(); }  // без кольца фокуса на ✕ при открытии
  checkUpdate(0);  // версия и новые коммиты по уже скачанным ссылкам — мгновенно, без похода на GitHub
  try { await loadSettings(); } catch (e) { $("#saveState").textContent = "✗ настройки не загрузились: " + e.message; }
  runDoctor();
}
async function loadSettings() {
  settings = await api("/api/settings");
  for (const inp of $$("#setDlg [data-c]")) inp.value = settings.config[inp.dataset.c] ?? "";
  $("#blenderIn").placeholder = settings.detected.blender ? "авто: " + settings.detected.blender : "не найден — укажи путь к blender.exe";
  $("[data-c=lms]").placeholder = settings.detected.lms ? "авто: " + settings.detected.lms : "не найден";
  for (const r of $$("input[name=llm_mode]")) r.checked = r.value === settings.config.llm_mode;
  $("#keyState").textContent = settings.has_key ? "ключ сохранён" : "ключа нет — без него тексты делает локальная модель (режим «авто»)";
  modeHint();
}
function modeHint() { $("#modeHint").textContent = MODE_HINT[$("input[name=llm_mode]:checked")?.value] || ""; }
$$("input[name=llm_mode]").forEach(r => r.onchange = modeHint);
$("#settingsBtn").onclick = () => openSettings();
$("#setClose").onclick = () => setDlg.close();
$("#keyEye").onclick = () => { const k = $("[data-c=openai_key]"); k.type = k.type === "password" ? "text" : "password"; };
$("#keyTest").onclick = async () => {
  $("#keyState").textContent = "проверяю…";
  let r;
  try { r = await api("/api/settings/test_openai", json("POST", { key: $("[data-c=openai_key]").value, model: $("[data-c=openai_model]").value })); }
  catch (e) { r = { ok: false, error: e.message }; }
  $("#keyState").textContent = r.ok ? `✓ ключ работает, модель ${r.model} доступна` : "✗ " + r.error;
};
$("#setSave").onclick = async () => {
  const body = {};
  for (const inp of $$("#setDlg [data-c]")) body[inp.dataset.c] = inp.type === "number" ? +inp.value : inp.value.trim();
  body.llm_mode = $("input[name=llm_mode]:checked")?.value || "auto";
  try {
    await api("/api/settings", json("POST", body));
    $("#saveState").textContent = "сохранено " + new Date().toLocaleTimeString("ru");
    await loadSettings(); runDoctor(); healthAt = 0;
  } catch (e) { alert(e.message); }
};

// строка проверки: подробность — одной строкой (целиком во всплывающей подсказке), у ошибок переносится —
// там написано, что поправить
const chkRow = (state, title, detail = "", btn = "") =>
  `<div class="chk ${state}"><span class="ic">${state === "ok" ? "✓" : state === "bad" ? "✕" : "○"}</span>` +
  `<div class="tx"><div class="t">${title}</div>${detail ? `<div class="d" title="${detail}">${detail}</div>` : ""}</div>${btn}</div>`;
const goBtn = (sec, text) => `<button class="small" data-go="${sec}">${text}</button>`;
const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let lastDoctor = null;
async function runDoctor() {
  $("#checks").innerHTML = '<div class="hint">проверяю…</div>';
  let r;
  try { r = lastDoctor = await api("/api/doctor"); } catch (e) {
    $("#checks").innerHTML = chkRow("bad", "Сервер propgen не ответил", esc(e.message)); setDot("system", "bad"); return;
  }
  // r.steps появились вместе с цепочкой; сервер со старым doctor (ещё не перезапущен) — прежний плоский список
  const states = r.steps ? workflow(r) : flatChecks(r);
  $$("#checks [data-dl]").forEach(btn => btn.onclick = () => startDownload(btn.dataset.dl.split(",")));
  $$("#checks [data-go]").forEach(btn => btn.onclick = () => showSec(btn.dataset.go));
  $("#settingsBtn").classList.toggle("alert", !setupReady(r));
  // точки в навигации: «Система» — худшее из проверок, остальные — по своим программам
  const l = r.llm;
  setDot("system", states.includes("bad") ? "bad" : states.includes("opt") ? "opt" : "ok");
  setDot("llm", l.mode === "off" ? "" : l.ok ? "ok" : "bad");
  setDot("apps", r.comfy.ok && r.blender.ok ? "ok" : "bad");
}

// ---- проверка системы цепочкой: итог «минимальный набор», основа (ComfyUI, Python), шаги конвейера с вариантами
// (что на шаге используется и чего не хватает), просмотрщик. Данные готовит tools/doctor.py → steps, minimum.
const gbTxt = gb => gb.toFixed(1).replace(".", ",");
function workflow(r) {
  const c = r.comfy, py = r.python, h = r.hdri, min = r.minimum, states = [];
  const coreGb = c.ok ? c.models.filter(m => m.group === "core" && !m.ok).reduce((a, m) => a + m.gb, 0) : 0;
  let html = min.ok
    ? '<div class="wf-sum ok"><span class="ic">✓</span><div><b>Минимальный набор на месте</b>' +
      "<small>ComfyUI с нодами · модели основного пути · Blender · Python — конвейер поедет</small></div></div>"
    : `<div class="wf-sum bad"><span class="ic">✕</span><div><b>Не хватает минимального набора</b>` +
      `<small title="${esc(min.missing.join(" · "))}">${esc(min.missing.join(" · "))}</small></div>` +
      (min.download.length ? `<button class="small primary" data-dl="${min.download.join(",")}">Скачать ${gbTxt(coreGb)} ГБ</button>` : "") + "</div>";
  states.push(min.ok ? "ok" : "bad");

  // основа: ComfyUI (через него идут фронт и 3D) и Python-зависимости сервера
  const base = [];
  if (!c.ok) base.push(chkRow("bad", "ComfyUI не отвечает",
    esc(c.error) + "\nЗапусти ComfyUI (Desktop или portable) и проверь адрес в разделе «ComfyUI и Blender».", goBtn("apps", "Адрес")));
  else {
    const noNode = c.nodes.filter(n => !n.ok), gguf = c.nodes.find(n => n.name === "UnetLoaderGGUF");
    base.push(chkRow(noNode.length ? "bad" : "ok", `ComfyUI ${esc(c.version)}`,
      noNode.length ? `нет нод: ${noNode.map(n => esc(n.name)).join(", ")}\n${esc(noNode[0].fix)}`
        // имя устройства у ComfyUI вида «cuda:0 NVIDIA … : cudaMallocAsync» — служебные хвосты не нужны
        : `${esc(c.url)} · нода GGUF ${gguf?.ok ? "есть" : "нет"} · ${esc(String(c.gpu || "").replace(/^\w+:\d+\s+/, "").replace(/\s+:\s+\w+$/, ""))} · ${c.vram_gb} ГБ видеопамяти`));
  }
  base.push(py.ok ? chkRow("ok", "Python-зависимости", esc(py.python))
    : chkRow("bad", "Python-зависимости", `нет: ${esc(py.missing.join(", "))} — запусти setup.cmd`));
  html += '<div class="fg-h">Основа</div><div class="checks">' + base.join("") + "</div>";

  // шаги: варианты — «таблетки» (✓ есть, ✕/○ нет, рамка — используется сейчас); под ними — чего не хватает и кнопка
  html += '<div class="fg-h">Конвейер</div><div class="wf">' + r.steps.map((s, i) => {
    states.push(s.state);
    const chips = s.options.map(o => {
      const cls = o.ok ? "ok" : s.state === "bad" ? "bad" : "no";
      // в подсказке — реальные файлы (у ComfyUI может лежать другой вариант весов, чем в манифесте)
      const tip = (o.ok ? "есть" + (o.files?.length ? ": " + o.files.join(", ") : "") : "не хватает: " + o.missing.join(", ")) +
        (o.current ? " · используется сейчас" : "");
      return `<span class="wf-o ${cls}${o.current ? " cur" : ""}" title="${esc(tip)}"><i>${o.ok ? "✓" : cls === "bad" ? "✕" : "○"}</i>` +
        `${esc(o.title)}${o.current ? "<em>сейчас</em>" : ""}</span>`;
    }).join("");
    const act = o => o.gb > 0 ? `<button class="small${s.state === "bad" ? " primary" : ""}" data-dl="${o.download.join(",")}">Скачать ${gbTxt(o.gb)} ГБ</button>`
      : o.manual.length ? '<span class="wf-manual">положи файлы в папку моделей ComfyUI</span>'
      : s.id === "prompt" ? goBtn("llm", "Настроить")
      : o.missing.some(m => m.startsWith("Blender")) ? goBtn("apps", "Указать путь") : "";
    // у рабочего шага не шумим про чужие варианты — только если их можно докачать или подложить руками;
    // без ComfyUI у всех его вариантов одна причина — она уже сказана в «Основе»
    // если на шаге есть «используется сейчас» (промпт) — говорим только про него; одну и ту же загрузку не повторяем
    const cur = s.options.find(o => o.current), seen = new Set();
    const lines = s.options.filter(o => !o.ok && (s.state !== "ok" || o.gb > 0 || o.manual.length) && (!cur || o === cur) &&
        !(o.missing.length === 1 && o.missing[0] === "ComfyUI не отвечает"))
      .filter(o => { const k = o.gb > 0 && !o.manual.length ? o.download.join(",") : ""; if (k && seen.has(k)) return false; seen.add(k); return true; })
      .map(o => `<div class="wf-m"><span class="d" title="${esc(o.missing.join(", "))}"><b>${esc(o.title)}</b> — ${esc(o.missing.join(", "))}</span>${act(o)}</div>`).join("");
    return `<div class="wf-step ${s.state}"><i class="wf-n">${i + 1}</i><div class="wf-b">` +
      `<div class="wf-h"><b>${esc(s.title)}</b><span>${esc(s.what)}${s.optional ? ' · <em class="wf-tag">необязательно</em>' : ""}</span></div>` +
      `<div class="wf-r"><div class="wf-opts">${chips}</div>${lines}</div></div></div>`;
  }).join("") + "</div>";

  html += '<div class="fg-h">Просмотрщик</div><div class="checks">' +
    chkRow(h.ok ? "ok" : "opt", "Панорамы 4k для фона — по желанию", `${h.have} из ${h.total}`, h.ok ? "" : '<button class="small" data-dl="hdri">Скачать ~80 МБ</button>') + "</div>";
  states.push(h.ok ? "ok" : "opt");
  $("#checks").innerHTML = html;
  $("#checks").classList.add("wf-mode");
  return states.map(s => s === "bad" && min.ok ? "opt" : s);  // при живом минимуме красного нет: остальное — по желанию
}

// прежний плоский список проверок — для сервера, ещё не знающего про steps
function flatChecks(r) {
  const c = r.comfy, rows = [], states = [];
  const add = (state, ...a) => { states.push(state); rows.push(chkRow(state, ...a)); };
  if (c.ok) add("ok", `ComfyUI ${esc(c.version)}`, `${esc(c.gpu)} · ${c.vram_gb} ГБ видеопамяти · ${c.ram_gb} ГБ RAM`);
  else add("bad", "ComfyUI не отвечает", esc(c.error) + "\nЗапусти ComfyUI (Desktop или portable) и проверь адрес в разделе «ComfyUI и Blender».",
    goBtn("apps", "Адрес"));
  if (c.ok) {
    for (const n of c.nodes.filter(n => !n.ok)) add("bad", `Нет ноды ${esc(n.name)}`, esc(n.fix));
    for (const [g, label] of Object.entries(r.groups)) {
      if (g === "hdri") continue;
      const ms = c.models.filter(m => m.group === g), miss = ms.filter(m => !m.ok);
      const gb = miss.reduce((a, m) => a + m.gb, 0);
      const must = g === "core";
      add(miss.length ? (must ? "bad" : "opt") : "ok", `Модели: ${esc(label)}${must ? "" : " — по желанию"}`,
        miss.length ? `нет ${miss.length} из ${ms.length}: ${miss.map(m => esc(m.file)).join(", ")}` : "все на месте",
        miss.length ? `<button class="small${must ? " primary" : ""}" data-dl="${g}">Скачать ${gb.toFixed(1).replace(".", ",")} ГБ</button>` : "");
    }
  }
  const b = r.blender;
  if (b.ok) add("ok", `Blender ${esc(b.version)}`, esc(b.path));
  else add("bad", "Blender", esc(b.error) + (b.path ? "\n" + esc(b.path) : ""), goBtn("apps", "Указать путь"));
  const l = r.llm, mode = { auto: "авто", openai: "OpenAI", local: "локально", off: "выключена" }[l.mode];
  add(l.ok ? "ok" : "bad", `Текстовая модель: ${mode}`,
    l.mode === "off" ? "заметки идут в генератор как есть" :
    [l.openai_key ? "ключ OpenAI есть" : "ключа OpenAI нет", l.lms ? (l.local_model_ok ? "локальная модель скачана" : "LM Studio есть, модель не скачана") : "LM Studio не установлен"].join(" · ") +
    (l.ok ? "" : "\nВведи ключ OpenAI или установи LM Studio и скачай модель — в разделе «Текстовая модель»."),
    l.ok ? "" : goBtn("llm", "Настроить"));
  const py = r.python;
  if (py.ok) add("ok", "Python-зависимости", esc(py.python));
  else add("bad", "Python-зависимости", `нет: ${esc(py.missing.join(", "))} — запусти setup.cmd`);
  const h = r.hdri;
  add(h.ok ? "ok" : "opt", "Панорамы 4k для фона — по желанию", `${h.have} из ${h.total}`, h.ok ? "" : '<button class="small" data-dl="hdri">Скачать ~80 МБ</button>');
  $("#checks").innerHTML = rows.join("");
  $("#checks").classList.remove("wf-mode");
  return states;
}
const setupReady = r => r.minimum ? r.minimum.ok :
  r.comfy.ok && r.comfy.nodes.every(n => n.ok) && r.comfy.models.filter(m => m.group === "core").every(m => m.ok) && r.blender.ok && r.python.ok;
$("#docRun").onclick = runDoctor;
// отладочный архив (tools/debug.py): сборка — секунды, а с проверкой ComfyUI и Blender бывает и минута, поэтому
// через fetch, а не простой ссылкой — так видно «собираю…» и ошибку сервера, а не пустое ожидание
$("#dbgGet").onclick = async () => {
  const btn = $("#dbgGet"), st = $("#dbgState"), was = st.textContent;
  btn.disabled = true; st.textContent = "собираю…";
  try {
    const r = await fetch("/api/debug");
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const name = (r.headers.get("content-disposition") || "").match(/filename="?([^";]+)/)?.[1] || "propgen-debug.zip";
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(await r.blob()), download: name });
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    st.textContent = `✓ ${name} — пришлите этот файл`;
  } catch (e) { st.textContent = "✗ " + e.message; setTimeout(() => (st.textContent = was), 8000); }
  btn.disabled = false;
};

async function startDownload(groups) {
  try { await api("/api/doctor/download", json("POST", { groups })); } catch (e) { return alert(e.message); }
  $("#dlBox").hidden = false;
  const tick = async () => {
    const d = await api("/api/doctor/download");
    const pct = d.total_bytes ? 100 * d.done_bytes / d.total_bytes : 0;
    $("#dlBar").style.width = pct.toFixed(1) + "%";
    $("#dlFile").textContent = d.error ? "✗ " + d.error : d.file ? "качаю " + d.file : d.active ? "готовлюсь…" : "готово";
    $("#dlNum").textContent = `${d.files_done}/${d.files_total} · ${(d.done_bytes / 2 ** 30).toFixed(2)} из ${(d.total_bytes / 2 ** 30).toFixed(2)} ГБ`;
    if (d.active) setTimeout(tick, 1000); else runDoctor();
  };
  tick();
}
$("#lmsGet").onclick = async () => {
  try { await api("/api/doctor/local_model", json("POST", {})); } catch (e) { return alert(e.message); }
  $("#lmsState").textContent = "качаю через LM Studio…";
  const tick = async () => {
    const s = await api("/api/doctor/local_model");
    if (s.active) return setTimeout(tick, 2000);
    $("#lmsState").textContent = s.ok ? "✓ модель скачана" : "✗ " + (s.log || "").split("\n").filter(Boolean).pop();
    runDoctor();
  };
  tick();
};
// ---- обновление из репозитория
// Новые коммиты видны в двух местах: плашка «Обновление · N» в заголовке окна (с поповером) и раздел «Обновление»
// в настройках. Состояние общее — updInfo (последний ответ GET /api/update) и updRun (идущее обновление), обе кнопки
// «Обновить и перезапустить» ведут в один runUpdate(), поэтому плашка и настройки не расходятся.
const fmtDate = iso => iso ? new Date(iso).toLocaleString("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
const plural = (n, one, few, many) => { const a = n % 10, b = n % 100; return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many; };
const UPD_EVERY = 30 * 60 * 1000;                  // GitHub спрашиваем не чаще раза в 30 минут
let updInfo = null, updAt = 0;                     // updAt — когда последний раз ходили на GitHub (fetch=1)
const updRun = { busy: false, msg: "", err: "" };  // этап идущего обновления и текст отказа сервера
const commitList = list => '<ul class="commits">' + list.map(c =>
  `<li><code>${esc(c.hash)}</code><span class="s" title="${esc(c.subject)}">${esc(c.subject)}</span><span class="d">${esc(fmtDate(c.date))}</span></li>`).join("") + "</ul>";
const dirtyNote = u => u.dirty?.length ? `<div class="upd-warn">⚠ Есть локальные правки — обновление их затрёт, поэтому сервер откажется обновляться: ${esc(u.dirty.join(", "))}</div>` : "";
const runNote = () => (updRun.msg ? `<div class="upd-msg">${esc(updRun.msg)}</div>` : "") + (updRun.err ? `<div class="upd-err">✗ ${esc(updRun.err)}</div>` : "");

function renderUpdate(u = updInfo) {
  const box = $("#updBox"), go = $("#updGo");
  go.hidden = true;
  if (!u) return;
  if (!u.git) {
    box.innerHTML = '<div class="hint">propgen установлен не через git — новую версию скачай с GitHub вручную.</div>';
    $("#navVer").textContent = "не через git"; setDot("update"); return;
  }
  const h = u.head || {}, behind = u.behind || [];
  let html = `<div class="upd-cur"><span class="lbl">Установлена</span><div class="v"><code>${esc(h.hash)}</code><span>${esc(h.subject)}</span></div>` +
    `<span class="hint">${esc(fmtDate(h.date))}${u.branch ? " · ветка " + esc(u.branch) : ""}</span></div>`;
  if (u.fetch_error) html += `<div class="upd-warn">Не удалось спросить GitHub: ${esc(u.fetch_error)}</div>`;
  if (!u.upstream) html += '<div class="hint">Ветка не связана с репозиторием на GitHub — обновляться неоткуда.</div>';
  else if (!behind.length) html += '<div class="upd-ok">✓ Это последняя версия</div>';
  else {
    html += `<div class="fg-h">Новое · ${behind.length}</div>` + commitList(behind) + dirtyNote(u);
    go.hidden = !!u.dirty?.length;
  }
  box.innerHTML = html + runNote();
  if (updRun.busy) go.hidden = false;
  go.disabled = updRun.busy;
  go.textContent = updRun.busy ? "Обновляюсь…" : "Обновить и перезапустить";
  $("#navVer").textContent = behind.length ? `новое: ${behind.length}` : h.hash ? "версия " + h.hash : "версия";
  setDot("update", behind.length ? "new" : "");
}

// плашка в заголовке: видна, только если есть новые коммиты (или обновление уже идёт)
const updPop = $("#updPop"), updBadge = $("#updBadge");
function renderBadge(u = updInfo) {
  const behind = u?.behind || [], n = behind.length;
  $("#updWrap").hidden = !n && !updRun.busy;
  if ($("#updWrap").hidden) closeUpdPop();
  updBadge.classList.toggle("busy", updRun.busy);
  $("#updBadgeTxt").textContent = updRun.busy ? "Обновляюсь…" : `Обновление · ${n}`;
  updBadge.title = updRun.busy ? "Обновляю propgen — страница перезагрузится сама" : `Доступна новая версия propgen: ${n} ${plural(n, "новый коммит", "новых коммита", "новых коммитов")}`;
  $("#updPopTitle").textContent = updRun.busy ? "Обновляю propgen…" : "Доступна новая версия";
  $("#updPopSub").textContent = n ? `сейчас ${u.head?.hash || "?"} · ${n} ${plural(n, "новый коммит", "новых коммита", "новых коммитов")}` : "";
  $("#updPopList").innerHTML = n ? commitList(behind) : "";
  $("#updPopNote").innerHTML = (u ? dirtyNote(u) : "") + runNote();
  const go = $("#updPopGo");
  go.disabled = updRun.busy;
  go.textContent = updRun.busy ? "Обновляюсь…" : "Обновить и перезапустить";
}
const renderUpd = () => { renderUpdate(); renderBadge(); };
function closeUpdPop() { updPop.hidden = true; updBadge.setAttribute("aria-expanded", "false"); }
updBadge.onclick = () => {
  if (!updPop.hidden) return closeUpdPop();
  renderBadge(); updPop.hidden = false; updBadge.setAttribute("aria-expanded", "true");
};
addEventListener("pointerdown", e => { if (!updPop.hidden && !e.target.closest("#updWrap")) closeUpdPop(); });
addEventListener("keydown", e => { if (e.key === "Escape" && !updPop.hidden) closeUpdPop(); });
$("#updPopMore").onclick = () => { closeUpdPop(); openSettings("", "update"); };

// quiet — фоновая проверка: не трогать раздел настроек надписью «спрашиваю…» и молчать об ошибке сети
async function checkUpdate(fetch = 1, quiet = false) {
  if (fetch) updAt = Date.now();
  if (fetch && !quiet && !updRun.busy) $("#updBox").innerHTML = '<div class="hint">спрашиваю GitHub…</div>';
  try {
    updInfo = await api(`/api/update?fetch=${fetch}`);
    renderUpd();
    return updInfo;
  } catch (e) { if (!quiet && !updRun.busy) $("#updBox").innerHTML = `<div class="upd-err">✗ ${esc(e.message)}</div>`; }
}
$("#updCheck").onclick = () => checkUpdate(1);

// git pull → перезапуск сервера → ждём, пока /api/version.started сменится, и перезагружаем страницу (свежий интерфейс).
// Отказ сервера (409: идёт задача, локальные правки) показываем там же, где нажали — в поповере и в разделе.
async function runUpdate() {
  if (updRun.busy) return;
  Object.assign(updRun, { busy: true, msg: "Скачиваю изменения…", err: "" }); renderUpd();
  try {
    const before = await api("/api/version");
    const r = await api("/api/update", json("POST", {}));
    if (!r.updated) { Object.assign(updRun, { busy: false, msg: "" }); updInfo = r; return renderUpd(); }
    updRun.msg = `Скачано изменений: ${r.pulled}${r.pip && r.pip !== "ok" ? " · ⚠ pip: " + r.pip : ""}. Перезапускаюсь…`; renderUpd();
    const t0 = Date.now();
    const wait = async () => {
      try { const v = await api("/api/version"); if (v.started !== before.started) return location.reload(); } catch {}
      if (Date.now() - t0 < 60000) return setTimeout(wait, 1000);
      Object.assign(updRun, { busy: false, err: "Сервер не поднялся за минуту — запусти start.cmd вручную." }); renderUpd();
    };
    setTimeout(wait, 1500);
  } catch (e) { Object.assign(updRun, { busy: false, msg: "", err: e.message }); renderUpd(); }
}
$("#updGo").onclick = runUpdate;
$("#updPopGo").onclick = runUpdate;

// тихая проверка: через 4 с после запуска (не мешать первой загрузке), дальше — раз в 30 минут. Таймер тикает
// раз в минуту и сверяется с updAt: ручная «Проверить» тоже сдвигает срок, а уснувшая вкладка догонит сразу.
setTimeout(() => checkUpdate(1, true), 4000);
setInterval(() => { if (!updRun.busy && Date.now() - updAt >= UPD_EVERY) checkUpdate(1, true); }, 60 * 1000);

// первый запуск: если чего-то не хватает — сразу показать проверку
api("/api/doctor").then(r => { if (!setupReady(r)) openSettings("Не всё готово к работе — вот что поправить.", "system"); else $("#settingsBtn").classList.remove("alert"); }).catch(() => {});

enhance();  // свои выпадающие списки, ползунки и числовые поля
setTab("crop");
loadList().then(() => { if (props.length) select(props[0].id); });
poll();
