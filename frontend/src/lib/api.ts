// 唯一發請求的地方。token 由後端寫進 index.html（只有改東西的請求需要）。
import type {
  BlackBoxWindow, ClosePlan, CloseResult, DeviceDetail, EventReport, HistoryWindow, MemoryRelease, MemoryStatus,
  ProcessRef, SettingsBody, SettingsPayload, Snapshot, TempCleanResult, TempScan,
} from "../types";

const token = document.querySelector<HTMLMetaElement>('meta[name="hwglance-token"]')?.content ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-HWGlance-Token": token, ...init?.headers },
  });
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    try {
      const body = (await r.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // 不是 JSON 就用狀態碼
    }
    throw new Error(detail);
  }
  return (await r.json()) as T;
}

export const api = {
  snapshot: () => request<Snapshot>("/api/snapshot"),
  history: (seconds: number) => request<HistoryWindow>(`/api/history?seconds=${seconds}`),
  device: (id: string) => request<DeviceDetail>(`/api/device?id=${encodeURIComponent(id)}`),
  events: (days: number) => request<EventReport>(`/api/events?days=${days}`), // 0＝全部
  blackbox: (start: number, end: number) => request<BlackBoxWindow>(`/api/blackbox?start=${start}&end=${end}`),
  settings: () => request<SettingsPayload>("/api/settings"),
  saveSettings: (body: SettingsBody) =>
    request<SettingsPayload>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  quit: () => request<{ ok: boolean }>("/api/app/quit", { method: "POST" }),
  // 迷你模式是本體的疊加層視窗：本體顯示它，再把這個主畫面關掉（瀏覽器開的就留著）
  openUi: (mode: "main" | "mini") =>
    request<{ ok: boolean; detail?: string }>("/api/ui/open", { method: "POST", body: JSON.stringify({ mode }) }),
  // 關閉顯存排行上的程式：先問（只查不動），使用者確認後才關；force＝強制結束（不會存檔）
  closePlan: (p: ProcessRef) =>
    request<ClosePlan>(`/api/processes/plan?pid=${p.pid}&created=${encodeURIComponent(p.created)}`),
  closeProcess: (p: ProcessRef, force: boolean) =>
    request<CloseResult>("/api/processes/close", { method: "POST", body: JSON.stringify({ ...p, force }) }),
  memory: () => request<MemoryStatus>("/api/memory"),
  releaseMemory: () => request<MemoryRelease>("/api/memory/release", { method: "POST" }),
  // 暫存資料夾：掃描（記住這次的 id）→ 刪可以刪的／被鎖住的那組排到下次開機刪除；關掉鎖住的程式用 closePlan／closeProcess
  tempScan: () => request<TempScan>("/api/temp/scan", { method: "POST" }),
  tempClean: (id: string) => request<TempCleanResult>("/api/temp/clean", { method: "POST", body: JSON.stringify({ id }) }),
  tempBootDelete: (id: string, key: string) =>
    request<{ scheduled: number; skipped: number }>("/api/temp/boot-delete", { method: "POST", body: JSON.stringify({ id, key }) }),
};

export function errorMessage(e: unknown): string {
  if (e instanceof TypeError) return "連不到監控本體（它可能已經關掉了）";
  return e instanceof Error ? e.message : String(e);
}
