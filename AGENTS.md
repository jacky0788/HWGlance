# 給接手的 AI agent

**一律用繁體中文（台灣用語）回覆使用者。** 使用者習慣先拿提案再決定；範圍以外的改動先問；長任務分段做、每段回報。

HWGlance＝輕量的硬體溫度監控。介面參考 NZXT CAM，但只留監控：打開就一眼看到 CPU、GPU、主機板、每顆硬碟的溫度。
需求、已定案的決策（D1…）、溫度門檻與進度在 [docs/SPEC.md](docs/SPEC.md)。

## 怎麼運作

LibreHardwareMonitorLib（LHM，.NET，MPL-2.0）讀硬體：透過 PawnIO 驅動讀 CPU 暫存器與主機板 Super I/O，
透過顯卡驅動的 API 讀 GPU，透過 SMART 讀硬碟。Python 用 pythonnet（netfx）載入 LHM；背景執行緒每秒讀一次、
整理成快照；FastAPI 只綁 127.0.0.1 把快照給前端。本體還有系統匣（圖示畫 CPU 溫度）。
視窗是另一個程序（pywebview＋WebView2）：主畫面與迷你視窗在同一個程序裡切換；關掉視窗就整個釋放，本體留在系統匣。
本體要以系統管理員執行才讀得到 CPU／主機板／硬碟（`run.bat` 會自己跳 UAC）；PawnIO 由使用者自己安裝。

## 程式在哪裡

| 位置 | 放什麼 |
|---|---|
| `src/hwglance/sensors/lhm.py` | **唯一碰 .NET 的地方**：載入 DLL、開關各類硬體、一層層更新（LHM 的 `Motherboard.Update()` 是空的，Super I/O 要自己更新）、把硬體樹轉成 `RawHardware` |
| `src/hwglance/sensors/demo.py` | 示範資料：照這台電腦的硬體模擬，名稱與 Identifier 跟 LHM 一樣 |
| `src/hwglance/sensors/model.py` | `RawHardware`／`RawSensor`（LHM 與示範資料共用的形狀） |
| `src/hwglance/sensors/normalize.py` | 整理：分類、總覽磚塊、主溫度、負載／時脈／功耗、風扇。純函式，`tests/test_normalize.py` 用假樹測 |
| `src/hwglance/sensors/labels.py`、`thresholds.py` | 英文感測器名稱 → 中文；溫度門檻（正常／偏高／過熱） |
| `src/hwglance/sensors/history.py`、`service.py` | 最近 10 分鐘的歷史；背景執行緒＋快照快取（硬碟每 10 輪才讀一次） |
| `src/hwglance/api/` | `app.py` 端點（清單在檔案開頭）、`security.py` Host 檢查與 token |
| `src/hwglance/settings.py` | 設定 `data/settings.json`：單位、更新頻率、關視窗留在系統匣、門檻（只存改過的）；壞掉就用預設 |
| `src/hwglance/core.py` | 本體：服務＋伺服器＋系統匣＋視窗控制（`Window`：開、叫到前面、排切換指令、結束）、套用設定、選埠 |
| `src/hwglance/tray.py` | 系統匣（pystray＋Pillow）：圖示畫 CPU 溫度、提示文字、右鍵選單；畫圖與文字是純函式 |
| `src/hwglance/ui.py` | 視窗程序（pywebview）：主畫面／迷你視窗（`UiApp.switch`）、網頁按鈕的 `Bridge`、長輪詢本體指令（`poll_commands`）、迷你位置記在 `data/ui-state.json` |
| `src/hwglance/instance.py` | 單一執行個體：`data/instance.json`＋`python -m hwglance --activate [--mode mini]` |
| `src/hwglance/probe.py` | 探測工具（`probe.bat`）：列出讀得到的所有感測器 |
| `src/hwglance/system.py` | 是不是系統管理員、PawnIO 版本、畫面上的提醒文字 |
| `scripts/fetch_lhm.py` | 從官方 release 取 LHM 0.9.6（比對 SHA-256），解到 `vendor/lhm/`（不進 git） |
| `scripts/make_icon.py`、`make-shortcut.ps1` | 產生 `assets/app.ico`（弧形儀表，跟介面 logo 一樣）；建立桌面捷徑 `HWGlance.lnk`（指向 `run.bat`、最小化啟動） |
| `docs/images/dashboard.webp` | ProjectHub 卡片的封面（示範資料的主畫面截圖，16:10） |
| ProjectHub 的 `registry/hwglance/project.toml` | 登錄到 ProjectHub（只有「啟動」一個按鈕）；要改照 ProjectHub 的 `docs/SOP-edit-project.md` |
| `frontend/src/pages/` | `Dashboard.tsx` 主畫面、`Mini.tsx` 迷你視窗（`/?view=mini`），只負責排版 |
| `frontend/src/features/` | `overview/`（總覽磚塊）、`devices/`（CPU／GPU 大卡、主機板、記憶體、硬碟）、`status/`（頂端列、提醒、底部）、`settings/`（設定面板） |
| `frontend/src/components/` | `TempGauge`（弧形儀表）、`Sparkline`（折線）、`Card`、`Readout`（數值格與清單列） |
| `frontend/src/lib/` | `api.ts`（唯一發請求的地方）、`useSnapshot.ts`（每秒抓、看不到就停）、`useHistory.ts`、`format.ts`（溫度一律傳 °C 進來，顯示時才換 °F）、`prefs.ts`（溫度單位的 context）、`bridge.ts`（桌面視窗的切換／關閉） |
| `frontend/src/styles/tokens.css` | 顏色與字級，全站視覺只在這裡調；溫度三色 `--ok`／`--warm`／`--hot` |

## 硬性規則

1. **測試絕不碰真的硬體、不載入 LHM**：用 `tests/conftest.py` 的假樹或 `DemoSource`。
2. **只有 `sensors/lhm.py` 可以 import clr／pythonnet**（`tests/test_consistency.py` 會擋）。
3. **API 幾乎只讀**。會動作的只有：存自己的設定（`PUT /api/settings`）、開自己的視窗（`POST /api/ui/open`）、
   結束自己（`POST /api/app/quit`），以及視窗程序拿指令（`GET /api/ui/commands`），全都要 token。本體以系統管理員執行，
   不要加會執行指令、寫任意檔案、改硬體設定（例如風扇轉速）的 API，要加先跟使用者討論。
4. 前端不寫 px（用 rem／em／%／fr，1px 細線例外）、`useEffect` 一律用大括號、顏色只用 `tokens.css` 的變數（測試會擋）。
5. `.bat` 只能 ASCII＋CRLF（cmd.exe 用系統碼頁讀）。Write 工具寫出來是 LF，要用 PowerShell 轉：
   `[IO.File]::WriteAllText(路徑, ($文字 -replace "`r?`n", "`r`n"), [Text.Encoding]::ASCII)`。
6. 改後端跑 `.venv\Scripts\python.exe -m pytest`；改前端跑 `npm run build`（含 `tsc`）。都過才算完成。
7. 連動修改：快照欄位改了 → 同步 `frontend/src/types.ts`；歷史的鍵 → `normalize.history_values` 與
   `frontend/src/lib/useHistory.ts` 的 `valuesOf` 要一樣；`tokens.css` 的 `--bg` → `ui.py` 的 `BACKGROUND`；
   設定欄位 → `settings.py`、`api/app.py` 的 `settings_payload`／`SettingsBody`、`types.ts` 的 `SettingsPayload`；
   新的溫度種類 → `thresholds.py` 的 `DEFAULTS` 與 `LABELS`；迷你視窗的內容變高 → `ui.py` 的 `MINI_SIZE`。
8. commit 不加 AI 署名；repo 自設身分 `HWGlance <jacky0788@users.noreply.github.com>`；使用者說要才 commit。

## 怎麼跑

| 要做 | 怎麼做 |
|---|---|
| 使用者開監控 | 雙擊 `run.bat`（第一次會跑 `setup.bat ui`：venv、LHM、建置介面；已經開著就叫出原視窗） |
| 看這台讀得到哪些感測器 | 使用者雙擊 `probe.bat`（跳 UAC）→ 結果在 `data/probe/probe-日期-時間.txt`，agent 讀這個檔 |
| 改介面時預覽 | `F:\Desktop\claude_project\.claude\launch.json` 的 `hwglance-demo`（埠 8797，示範資料，設定存在 `data/demo`）；迷你畫面開 `/?view=mini` |
| 不用系統管理員看桌面視窗 | `.venv\Scripts\pythonw.exe -m hwglance --demo --data data/demo-desktop`（`--data` 讓設定不跟真的混在一起；用完刪掉那個資料夾） |
| 叫出迷你視窗／只開系統匣 | `python -m hwglance --activate --mode mini`；`python -m hwglance --hidden` |

## 環境事實（2026-10-29～30 驗證過）

- 這台：Ryzen 7 5800X、RTX 5070 Ti、ASUS TUF GAMING X570-PLUS (WI-FI)（Super I/O 是 NCT6798D）、
  DDR4 沒有溫度感測器、6 顆硬碟（2 NVMe＋2 SATA SSD＋2 HDD）。LHM 原始碼對這塊主機板有專屬設定（3 個溫度、7 組風扇）。
- Python 3.14.3、pythonnet 3.2.1、pywebview 6.2.1、FastAPI 0.162；前端 React 19.3、Vite 8.3、TypeScript 7.0、Tailwind 4.3。
- 不靠驅動能讀的只有顯卡（nvidia-smi 非管理員實測 70°C）。WMI 的 ACPI 溫度回「不受支援」、硬碟 SMART 非管理員被拒。
- **auto mode 擋過 agent 執行下載的 LHM DLL**（理由 Code from External）：真的讀數由使用者跑 `probe.bat`／`run.bat`，
  agent 不要想辦法繞過去自己執行。
- PowerShell 把 `$null` 傳給 P/Invoke 的 string 參數會變成空字串：`FindWindow` 要傳 `[NullString]::Value`。
- 截視窗要先 `SetProcessDpiAwarenessContext(-4)`，否則 `GetWindowRect` 拿到縮放過的座標，只截到左上角。
- 量記憶體看私有工作集（`Win32_PerfFormattedData_PerfProc_Process.WorkingSetPrivate`）；工作集直接加總會重複算共用的 DLL。
- venv 的 pythonw 會再開一個真正的子程序：找視窗用標題（`HWGlance`／`HWGlance 迷你`），不要用 Popen 的 pid。
- Claude 桌面版環境有 `NoDefaultCurrentDirectoryInExePath=1`：.bat 裡一律寫完整路徑（`%~dp0…`）。
- pywebview 6.2.1（WinForms）踩過的坑（2026-10-30）：
  - `webview.start(func=…)` 開的執行緒**不是 daemon**，視窗都關了它還讓程序活著 → 輪詢執行緒自己開 daemon。
  - 網頁透過 js_api 呼叫、而那個呼叫會關掉發出呼叫的視窗時，回傳結果的執行緒會永遠等 → `Bridge` 先回傳、動作丟給別的執行緒。
  - 事件處理函式在新執行緒裡跑；參數照函式的參數個數傳（有叫 `window` 的參數就多傳視窗）。座標、大小都是邏輯像素。
  - 保險：最後一個視窗關掉 5 秒後程序還在就 `os._exit(0)`（`UiApp._exit_soon`）。
- 自動化測真的桌面視窗：UI Automation 看不到 WebView2 裡的按鈕；要點網頁裡的東西，就在**測試時**設
  `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333`，用 CDP 執行 JS（正式程式不加這個）。
  PowerShell 變數不分大小寫：`$main` 和 `$MAIN` 是同一個變數（2026-10-30 因此誤判過一次）。
- 系統匣：工作列是自動隱藏的，截不到；要確認系統匣有開，看本體程序底下有沒有類別名稱含 `SystemTrayIcon` 的視窗。
- 實測（示範資料）：只剩系統匣時本體約 49 MB 私有記憶體；網頁按鈕切換迷你／主畫面不重開程序，從系統匣叫回主畫面約 0.5 秒。
