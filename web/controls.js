// Контролы форм в стиле интерфейса: свой выпадающий список поверх <select>, заливка ползунков, кнопки −/+ у чисел.
// Нативные элементы остаются в DOM и источником правды: value, onchange и oninput работают как раньше.

const SELECT_VALUE = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
const INPUT_VALUE = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");

// value, выставленное из кода, тоже должно обновлять вид — перехватываем сеттер у конкретного элемента
function watchValue(el, desc, onSet) {
  Object.defineProperty(el, "value", {
    configurable: true,
    get() { return desc.get.call(this); },
    set(v) { desc.set.call(this, v); onSet(); },
  });
}

let openMenu = null;
function closeMenu() {
  if (!openMenu) return;
  openMenu.menu.remove();
  openMenu.btn.setAttribute("aria-expanded", "false");
  openMenu = null;
}
addEventListener("pointerdown", e => { if (openMenu && !openMenu.menu.contains(e.target) && e.target !== openMenu.btn) closeMenu(); }, true);
addEventListener("resize", closeMenu);
addEventListener("scroll", e => { if (openMenu && !openMenu.menu.contains(e.target)) closeMenu(); }, true);

function enhanceSelect(sel) {
  if (sel.dataset.ux) return;
  sel.dataset.ux = "1";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ux-select";
  btn.setAttribute("aria-haspopup", "listbox");
  btn.title = sel.title || "";
  sel.after(btn);
  sel.classList.add("ux-native");
  const sync = () => {
    const o = sel.options[sel.selectedIndex];
    btn.innerHTML = `<span>${o ? o.textContent : ""}</span><i></i>`;
    btn.disabled = sel.disabled;
    btn.hidden = sel.hidden;
  };
  watchValue(sel, SELECT_VALUE, sync);
  new MutationObserver(sync).observe(sel, { attributes: true, attributeFilter: ["hidden", "disabled"], childList: true });
  sync();

  const choose = i => {
    if (sel.options[i]?.disabled) return;  // недоступный пункт (например, модели нет) — видно, но не выбрать
    if (sel.selectedIndex !== i) {
      sel.selectedIndex = i;
      sync();
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
    closeMenu();
    btn.focus();
  };
  const open = () => {
    if (openMenu?.btn === btn) return closeMenu();
    closeMenu();
    const menu = document.createElement("div");
    menu.className = "ux-menu";
    menu.setAttribute("role", "listbox");
    [...sel.options].forEach((o, i) => {
      const it = document.createElement("div");
      it.className = "ux-opt" + (i === sel.selectedIndex ? " on" : "") + (o.disabled ? " off" : "");
      it.textContent = o.textContent;
      it.title = o.title || "";
      it.onclick = () => choose(i);
      menu.append(it);
    });
    // меню — в слой поверх всего (и над <dialog>): внутрь того же диалога, иначе в body
    (btn.closest("dialog") || document.body).append(menu);
    const r = btn.getBoundingClientRect();
    menu.style.minWidth = r.width + "px";
    const h = menu.offsetHeight, below = innerHeight - r.bottom - 8;
    menu.style.left = Math.min(r.left, innerWidth - menu.offsetWidth - 8) + "px";
    menu.style.top = (below >= h || below > r.top ? r.bottom + 4 : r.top - h - 4) + "px";
    menu.querySelector(".on")?.scrollIntoView({ block: "nearest" });
    openMenu = { btn, menu, hi: sel.selectedIndex };
    btn.setAttribute("aria-expanded", "true");
  };
  btn.onclick = open;
  btn.onkeydown = e => {
    const n = sel.options.length;
    if (!openMenu || openMenu.btn !== btn) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); }
      return;
    }
    const items = openMenu.menu.children;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      openMenu.hi = (openMenu.hi + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
      [...items].forEach((x, i) => x.classList.toggle("hi", i === openMenu.hi));
      items[openMenu.hi].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(openMenu.hi); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(); }
  };
}

function enhanceRange(inp) {
  if (inp.dataset.ux) return;
  inp.dataset.ux = "1";
  inp.classList.add("ux-range");
  const fill = () => {
    const min = +inp.min || 0, max = +inp.max || 100;
    inp.style.setProperty("--p", ((INPUT_VALUE.get.call(inp) - min) / (max - min) * 100).toFixed(2) + "%");
  };
  watchValue(inp, INPUT_VALUE, fill);
  inp.addEventListener("input", fill);
  fill();
}

function enhanceNumber(inp) {
  if (inp.dataset.ux) return;
  inp.dataset.ux = "1";
  const wrap = document.createElement("span");
  wrap.className = "ux-num";
  inp.before(wrap);
  const minus = document.createElement("button"), plus = document.createElement("button");
  minus.type = plus.type = "button";
  minus.tabIndex = plus.tabIndex = -1;
  minus.textContent = "−"; plus.textContent = "+";
  wrap.append(minus, inp, plus);
  const step = d => {
    inp[d > 0 ? "stepUp" : "stepDown"]();
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    inp.dispatchEvent(new Event("change", { bubbles: true }));
  };
  minus.onclick = () => step(-1);
  plus.onclick = () => step(1);
}

export function enhance(root = document) {
  root.querySelectorAll("select:not([multiple])").forEach(enhanceSelect);
  root.querySelectorAll('input[type="range"]').forEach(enhanceRange);
  root.querySelectorAll('input[type="number"]').forEach(enhanceNumber);
}
