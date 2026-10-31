// 事件的種類：名稱、圖示、顏色、篩選。顯示卡、硬碟、CPU 的圖示用硬體種類色（跟即時監控一樣），當機用紅色；
// 嚴重程度（標籤、數字、每一行前面的點）一律用等級色。
import { AppWindow, Cpu, Flame, Gpu, HardDrive, Info, MonitorX, Power, RotateCcw } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { EventCategory, EventLevel, Incident } from "../../types";

export type Filter = "all" | EventCategory;

export const LABELS: Record<EventCategory, string> = {
  crash: "當機與重開",
  gpu: "顯示卡",
  storage: "硬碟",
  hardware: "CPU／記憶體",
  system: "系統",
  app: "程式當掉",
};

const ICONS: Record<EventCategory, LucideIcon> = {
  crash: RotateCcw, gpu: Gpu, storage: HardDrive, hardware: Cpu, system: Info, app: AppWindow,
};
const CRASH_ICONS: Record<string, LucideIcon> = { 藍屏: MonitorX, 強制關機: Power, 過熱: Flame };
const KINDS: Partial<Record<EventCategory, string>> = { gpu: "kind-gpu", storage: "kind-storage", hardware: "kind-cpu" };

export function iconOf(i: Incident): { Icon: LucideIcon; tone: string } {
  if (i.category === "crash") return { Icon: CRASH_ICONS[i.badge] ?? RotateCcw, tone: "text-hot" };
  return { Icon: ICONS[i.category], tone: `${KINDS[i.category] ?? "kind-other"} text-(--kind)` };
}

// 事件等級 → level-* class：資訊（開機、傾印檔已存）用灰色
export function levelClass(level: EventLevel): string {
  return level === "info" ? "level-none" : `level-${level}`;
}

// 篩選看「牽涉到」：藍屏前有顯示卡錯誤的那次，選「顯示卡」也看得到（跟摘要的數字算法一樣）
export function matches(i: Incident, f: Filter): boolean {
  return f === "all" || i.involves.includes(f);
}
