// /api/snapshot 的形狀。後端在 src/hwglance/sensors/normalize.py 與 service.py；欄位改了兩邊一起改。

export type Level = "ok" | "warm" | "hot" | "none";
export type Kind = "cpu" | "gpu" | "board" | "cooler" | "memory" | "storage" | "psu" | "other";

export interface Temp {
  id: string;
  name: string; // LHM 的英文名稱
  label: string; // 中文
  role: string; // 決定門檻：cpu、gpu、gpu_hotspot、nvme、hdd…
  value: number | null;
  min: number | null;
  max: number | null;
  level: Level;
}

export interface Stat {
  key: string; // load、clock、power、fan、vram、used、activity、free
  label: string;
  value: number;
  unit: string; // %、MHz、W、RPM、MB、GB
  total?: number;
  note?: string;
}

export interface Fan {
  id: string;
  label: string;
  value: number | null;
  max: number | null;
  unit: string;
  percent: number | null;
}

export interface Device {
  id: string;
  kind: Kind;
  name: string;
  sub: string;
  headline: string | null; // 卡片儀表顯示哪個溫度
  temps: Temp[];
  stats: Stat[];
  fans: Fan[];
  unplugged: number; // 沒接（從沒轉過）的風扇接頭數
  tiles: string[];
  media?: "nvme" | "ssd" | "hdd" | "unknown";
}

export interface Tile {
  id: string; // 溫度的 id
  device: string;
  kind: Kind;
  label: string;
  sub: string;
  value: number | null;
  max: number | null;
  level: Level;
  role: string;
}

export interface Source {
  kind: "lhm" | "demo";
  version: string | null;
  admin: boolean;
  pawnio: string | null;
  notes: string[];
}

export type Unit = "C" | "F";

export interface Snapshot {
  seq: number;
  time: number | null;
  interval: number;
  state: "starting" | "running" | "error";
  source: Source;
  perf: { readMs?: number };
  prefs: { unit: Unit };
  devices: Device[];
  overview: Tile[];
}

// /api/settings（後端 api/app.py 的 settings_payload）。門檻一律 °C：[偏高, 過熱]
export interface SettingsPayload {
  unit: Unit;
  interval: number;
  closeToTray: boolean;
  bands: Record<string, [number, number]>;
  defaults: Record<string, [number, number]>;
  labels: Record<string, string>;
  intervals: number[];
  range: [number, number];
}

export interface SettingsBody {
  unit: Unit;
  interval: number;
  closeToTray: boolean;
  bands: Record<string, [number, number]>;
}

export interface HistoryWindow {
  t: number[];
  series: Record<string, (number | null)[]>;
}

// /api/device?id=（後端 src/hwglance/sensors/details.py）：點硬體跳出的詳細資訊，欄位改了兩邊一起改
export interface DetailRow {
  label: string;
  value: number | string | null;
  unit?: string;
  note?: string; // 一句說明
  temp?: boolean; // 溫度：值是 °C，顯示時照設定換 °F
  level?: Level;
  min?: number | null;
  max?: number | null;
}

export interface DetailTable {
  columns: { label: string; unit?: string }[];
  rows: { label: string; cells: (number | string | null)[] }[];
}

export interface DetailBars {
  label: string;
  unit: string;
  max: number;
  items: { label: string; value: number | null }[];
}

export interface DetailSection {
  title: string;
  note?: string;
  rows?: DetailRow[];
  table?: DetailTable;
  bars?: DetailBars;
}

export interface DeviceDetail {
  id: string;
  kind: Kind;
  name: string;
  sub: string;
  sections: DetailSection[];
}
