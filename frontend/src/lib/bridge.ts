// 桌面視窗（pywebview）才有的功能：切換主畫面／迷你視窗、關掉迷你視窗（後端 ui.py 的 Bridge）。
// 在一般瀏覽器裡（開發預覽）改用網址 ?view=mini 切換。
import { useEffect, useState } from "react";

export type View = "main" | "mini";

interface DesktopApi {
  switch?: (view: View) => Promise<boolean>;
  close?: () => Promise<boolean>;
}

declare global {
  interface Window {
    pywebview?: { api?: DesktopApi };
  }
}

export function currentView(): View {
  return new URLSearchParams(window.location.search).get("view") === "mini" ? "mini" : "main";
}

function desktopApi(): DesktopApi | undefined {
  const api = window.pywebview?.api;
  return typeof api?.switch === "function" ? api : undefined;
}

// pywebview 的 api 要等 pywebviewready 事件之後才有
export function useDesktop(): boolean {
  const [ready, setReady] = useState(() => desktopApi() != null);
  useEffect(() => {
    const onReady = () => {
      setReady(desktopApi() != null);
    };
    window.addEventListener("pywebviewready", onReady);
    return () => {
      window.removeEventListener("pywebviewready", onReady);
    };
  }, []);
  return ready;
}

export async function switchView(view: View): Promise<void> {
  const api = desktopApi();
  if (api?.switch) {
    await api.switch(view);
    return;
  }
  const url = new URL(window.location.href);
  if (view === "mini") url.searchParams.set("view", "mini");
  else url.searchParams.delete("view");
  window.location.href = url.toString();
}

export async function closeWindow(): Promise<void> {
  await desktopApi()?.close?.();
}
