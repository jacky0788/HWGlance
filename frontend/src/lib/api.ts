// 唯一發請求的地方。token 由後端寫進 index.html（只有改東西的請求需要）。
import type {
  BlackBoxWindow, DeviceDetail, EventReport, HistoryWindow, SettingsBody, SettingsPayload, Snapshot,
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
};

export function errorMessage(e: unknown): string {
  if (e instanceof TypeError) return "連不到監控本體（它可能已經關掉了）";
  return e instanceof Error ? e.message : String(e);
}
