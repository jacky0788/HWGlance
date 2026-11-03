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
  prefs: { unit: Unit; colors?: Record<string, string> }; // colors：只有改過的卡片顏色
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
  colors: Record<string, string>; // 卡片顏色：調色盤的名稱（blue…）或 #rrggbb
  colorDefaults: Record<string, string>;
  colorLabels: Record<string, string>;
  presets: string[];
  mini: MiniSettings;
  miniDefaults: MiniSettings;
  miniCatalog: MiniGroup[];
  miniRange: { opacity: [number, number]; scale: [number, number] };
  fps: string | null; // FPS 讀不到的原因；null＝讀得到
  hotkeys: { keys: string; action: string; ok: boolean }[]; // 迷你模式的全域快速鍵；ok=false 是被別的程式佔用
}

// 迷你模式（後端 settings.py 的 MiniSettings、mini/items.py 的 catalog）
export interface MiniSettings {
  items: string[]; // 要顯示的項目（gpu.temp…）
  order: string[]; // 分組順序（gpu、cpu…），一組一行
  opacity: number; // 背景不透明度 %
  scale: number; // 大小 %
  locked: boolean; // 滑鼠穿透
}

export interface MiniGroup {
  group: string;
  label: string;
  items: { id: string; label: string }[];
}

export interface SettingsBody {
  unit: Unit;
  interval: number;
  closeToTray: boolean;
  bands: Record<string, [number, number]>;
  colors: Record<string, string>;
  mini: MiniSettings;
}

export interface HistoryWindow {
  t: number[];
  series: Record<string, (number | null)[]>;
}

// /api/device?id=（後端 src/hwglance/sensors/details.py）：點硬體跳出的詳細資訊，欄位改了兩邊一起改
// 量的是什麼：決定段落圖示與數值顏色（溫度另外照等級上色）
export type Measure = "temp" | "load" | "clock" | "power" | "fan" | "data" | "flow";

export interface DetailRow {
  label: string;
  value: number | string | null;
  unit?: string;
  note?: string; // 一句說明
  temp?: boolean; // 溫度：值是 °C，顯示時照設定換 °F
  level?: Level;
  min?: number | null;
  max?: number | null;
  measure?: Measure; // 只有「其他讀數」的行會帶（那一段什麼都有）
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

// 排行（顯存被誰佔用）：名次＋佔總量的長條；process 有的才有「關閉」按鈕
export interface ProcessRef {
  pid: number;
  created: string; // 啟動時間（FILETIME 的字串）：pid 會被重複使用，靠它認出同一個程式
}

export interface DetailRanking {
  total?: { value: number; unit: string };
  items: { label: string; note: string; value: number; unit: string; share: number; process?: ProcessRef }[];
}

// GET /api/processes/plan：按「關閉」前先問（processes.py）
export interface ClosePlan {
  pid: number;
  name: string;
  blocked: string | null; // 不能關的原因
  target?: ProcessRef & { name: string }; // 真的要關的程式（沒有自己視窗時是它的主程式）
  gentle?: boolean; // 有視窗、可以好好關；false＝只能強制結束
}

// GET /api/memory：記憶體的組成（memory.py；各段加起來＝total）
export type MemorySegmentKey = "used" | "compressed" | "modified" | "standby" | "free";

export interface MemoryRelease {
  before: number; // 「可用」之前／之後（位元組）
  after: number;
  freed: number;
  freeBefore: number; // 「完全空著」之前／之後：清待命快取只會讓這個變多
  freeAfter: number;
  trimmed: number;
  tried: number;
  skippedForeground: boolean;
  standby: "purged" | "needs_admin" | "failed";
  time: number;
}

export interface MemoryStatus {
  total: number;
  available: number;
  segments: { key: MemorySegmentKey; label: string; note: string; bytes: number }[];
  compression: { enabled: boolean; bytes: number };
  canPurge: boolean; // 清待命快取要系統管理員
  last: MemoryRelease | null;
}

// POST /api/temp/scan：暫存資料夾的掃描結果（tempfiles.py）。只管放超過 1 天的檔；動作只收 id（不收路徑）
export interface TempGroup {
  key: string; // 「pid:啟動時間」或 unknown
  name: string;
  protected: string | null; // 不能關的原因（系統程式）
  process: ProcessRef | null; // 有的才給「關掉這個程式」
  files: number;
  bytes: number;
  samples: string[]; // 前幾個檔名（相對於暫存資料夾）
  bootDelete: boolean; // 能不能「下次開機刪除」（只有使用者自己的暫存）
}

export interface TempScan {
  id: string;
  time: number;
  took: number;
  truncated: boolean;
  roots: { label: string; path: string; files: number; bytes: number; old: number; oldBytes: number; readable: boolean }[];
  deletable: { files: number; bytes: number };
  locked: TempGroup[];
  denied: { files: number; bytes: number };
  admin: boolean;
}

export interface TempCleanResult {
  deleted: number;
  bytes: number;
  dirs: number;
  failed: number;
  locked: number;
}

// POST /api/processes/close 的結果
export interface CloseResult {
  result: "closed" | "running" | "no_window" | "killed" | "failed";
  name: string;
}

export interface DetailSection {
  title: string;
  note?: string;
  measure?: Measure;
  rows?: DetailRow[];
  table?: DetailTable;
  bars?: DetailBars;
  ranking?: DetailRanking;
}

export interface DeviceDetail {
  id: string;
  kind: Kind;
  name: string;
  sub: string;
  sections: DetailSection[];
}

// /api/events?days=（後端 src/hwglance/events/incidents.py 的 incident／report）：事件記錄歸成的一件件事，
// 欄位改了兩邊一起改。時間都是 epoch 秒，顯示時才換成本地時間。
export type EventCategory = "crash" | "gpu" | "storage" | "hardware" | "system" | "app";
export type EventLevel = "hot" | "warm" | "info";

export interface EventLine {
  time: number; // 第一次
  last: number; // 最後一次（同一種事件合成一行）
  count: number;
  text: string;
  source: string; // 來源＋事件識別碼，例如「nvlddmkm 13」
  log: string; // System／Application
  level: EventLevel;
  message: string | null; // Windows 記錄的原文（序號已遮掉）
}

export interface Incident {
  id: string;
  category: EventCategory;
  level: "hot" | "warm";
  badge: string; // 藍屏、重開、已恢復、斷線…
  title: string;
  major: boolean; // 要特別注意的；其他的事件頁收成一行
  summary: string; // 「FECS 致命錯誤 → TDR 重設 ×101 → 74 秒後驅動重設顯示卡」
  explain: string[];
  advice: string[];
  start: number;
  end: number;
  involves: EventCategory[];
  disks: string[];
  lines: EventLine[];
}

export interface EventCounts {
  bluescreen: number;
  reset: number; // 沒有藍屏代碼的重開（含強制關機、睡眠恢復失敗、過熱）
  gpu: number; // 牽涉顯示卡的事件數
  gpuMajor: number; // 其中要特別注意的
  storage: number;
  hardware: number;
  app: number; // 程式當掉的次數（不是件數）
}

export interface EventMarker {
  time: number;
  kind: "boot" | "shutdown" | "sleep" | "wake";
  text: string;
}

// /api/blackbox?start=&end=（後端 src/hwglance/sensors/blackbox.py）：那段時間每 2 秒的主要讀數，欄位改了兩邊一起改
export interface BlackBoxKey {
  key: string; // 跟 /api/history 的鍵一樣：溫度的 id，或「硬體 id#load／#power」
  label: string; // CPU、GPU 核心、GPU 功耗…
  unit: string; // °C、%、W
  kind: Kind;
  role: string; // 溫度的門檻種類（cpu、gpu、nvme…），或 load、power
  device: string;
  max: number | null;
  level?: Level; // 溫度才有：最高溫照門檻的等級
}

export interface BlackBoxWindow {
  from: number;
  to: number;
  first: number | null; // 範圍內第一筆、最後一筆紀錄的時間
  last: number | null;
  count: number;
  step: number; // 每一點相隔幾秒（太多筆就分段取最大值）
  keepDays: number;
  gaps: [number, number][]; // 中間沒有紀錄的時段 [最後一筆, 下一筆]
  t: number[];
  keys: BlackBoxKey[];
  series: Record<string, (number | null)[]>;
}

export interface EventReport {
  source: "windows" | "demo";
  days: number | null; // null＝全部
  since: number | null;
  oldest: number | null; // 系統記錄最早的一筆
  generated: number;
  counts: EventCounts;
  incidents: Incident[]; // 新的在前
  markers: EventMarker[]; // 開關機、睡眠，新的在前
  errors: string[];
}
