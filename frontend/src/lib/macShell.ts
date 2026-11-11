// Mac 的桌面視窗（後端 ui.py 開的網址帶 ?shell=mac&titlebar=28）：標題列是透明的，紅黃綠按鈕疊在頂端列左邊。
// 這裡把 <html> 標成 data-shell="mac"、記下標題列多高（index.css 照它排頂端列），並把頂端列多高、哪些地方是按鈕
// 回報給視窗程序（ui.py 的 MacShell.drag_region）：按在頂端列的空白處＝拖曳視窗、連按兩下＝縮放，按鈕照常能按。
// 瀏覽器、Windows 的視窗都沒有這個參數，什麼都不做。

declare global {
  interface Window {
    pywebview?: { api?: { drag_region?: (height: number, holes: number[][]) => Promise<unknown> } };
  }
}

const INTERACTIVE = "a, button, input, select, textarea, summary, [role='button'], [tabindex]:not([tabindex='-1'])";

function report(header: HTMLElement) {
  const api = window.pywebview?.api;
  if (!api?.drag_region) return;
  const holes = [...header.querySelectorAll<HTMLElement>(INTERACTIVE)].map((el) => {
    const r = el.getBoundingClientRect();
    return [r.left, r.top, r.width, r.height];
  });
  void api.drag_region(header.getBoundingClientRect().bottom, holes).catch(() => undefined);
}

// 頂端列（<header data-titlebar>）出現後就盯著它：大小、內容一變（例如快照到了多出按鈕、視窗變窄換行）就重新回報
function watch() {
  let frame = 0;
  let observed: HTMLElement | null = null;
  const schedule = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const header = document.querySelector<HTMLElement>("[data-titlebar]");
      if (header) report(header);
    });
  };
  const resize = new ResizeObserver(schedule);
  const mutations = new MutationObserver(() => {
    const header = document.querySelector<HTMLElement>("[data-titlebar]");
    if (header && header !== observed) {
      if (observed) resize.unobserve(observed);
      observed = header;
      resize.observe(header);
    }
    schedule();
  });
  mutations.observe(document.body, { childList: true, subtree: true, attributes: true });
  window.addEventListener("resize", schedule);
  window.addEventListener("pywebviewready", schedule);
  schedule();
}

export function startMacShell() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("shell") !== "mac") return;
  // 視窗程序量好的：標題列多高、紅黃綠按鈕多寬（點）。這兩個是 macOS 定的固定大小，不跟著網頁的字級縮放，
  // 所以用「幾個 1px」而不是 rem（這個專案的根字級不是 16 像素，換成 rem 會差好幾點、跟按鈕對不齊）
  const bar = Number(params.get("titlebar")) || 28;
  const lights = Number(params.get("lights")) || 78;
  const root = document.documentElement;
  root.dataset.shell = "mac";
  root.style.setProperty("--titlebar-h", `calc(${bar} * 1px)`);
  root.style.setProperty("--lights-w", `calc(${lights} * 1px)`);
  watch();
}
