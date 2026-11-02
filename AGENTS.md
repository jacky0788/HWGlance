# 給接手的 AI agent

**一律用繁體中文（台灣用語）回覆使用者。** 使用者習慣先拿提案再決定；範圍以外的改動先問；長任務分段做、每段回報。

HWGlance＝輕量的硬體溫度監控。介面參考 NZXT CAM，但只留監控：打開就一眼看到 CPU、GPU、主機板、每顆硬碟的溫度。
需求、已定案的決策（D1…）、溫度門檻與進度在 [docs/SPEC.md](docs/SPEC.md)。

## 怎麼運作

LibreHardwareMonitorLib（LHM，.NET，MPL-2.0）讀硬體：透過 PawnIO 驅動讀 CPU 暫存器與主機板 Super I/O，
透過顯卡驅動的 API 讀 GPU，透過 SMART 讀硬碟。Python 用 pythonnet（netfx）載入 LHM；背景執行緒每秒讀一次、
整理成快照；FastAPI 只綁 127.0.0.1 把快照給前端。本體還有系統匣（圖示畫 CPU 溫度）。
主畫面是另一個程序（pywebview＋WebView2）：關掉視窗就整個釋放，本體留在系統匣。迷你模式是本體自己的疊加層視窗
（Win32 分層視窗＋Pillow 畫字，像 RivaTuner：背景可以半透明、字照樣清楚），跟主畫面同時只開一個；遊戲 FPS 用 PresentMon。
本體要以系統管理員執行才讀得到 CPU／主機板／硬碟（`run.bat` 會自己跳 UAC）；PawnIO 由使用者自己安裝。

## 程式在哪裡

| 位置 | 放什麼 |
|---|---|
| `src/hwglance/sensors/lhm.py` | **唯一碰 .NET 的地方**：載入 DLL（CLR 啟動前設 `CSE_POLICY`，存取違規才接得到）、開關各類硬體、一層層更新（LHM 的 `Motherboard.Update()` 是空的，Super I/O 要自己更新）、把硬體樹轉成 `RawHardware`；某一類讀失敗交給 `guard.py` 停讀、到時候 `Is*Enabled` 關掉重開；`specs()` 讀規格（SMBIOS＋WMI 的硬碟與顯示卡驅動） |
| `src/hwglance/sensors/demo.py` | 示範資料：照這台電腦 2026-10-31 的 probe 模擬，名稱、Identifier、順序跟 LHM 一樣；`IntelLaptopDemo`（`--demo-profile intel-laptop`）是另一種電腦：Intel 12 代筆電（效能核／節能核、內顯＋筆電獨顯、電池、主機板沒有感測器），名稱照 LHM 原始碼 |
| `src/hwglance/sensors/model.py` | `RawHardware`／`RawSensor`（LHM 與示範資料共用的形狀） |
| `src/hwglance/sensors/normalize.py` | 整理：分類、總覽磚塊、主溫度、負載／時脈／功耗、風扇。純函式，`tests/test_normalize.py` 用假樹測 |
| `src/hwglance/sensors/details.py` | 點硬體跳出的詳細資訊：規格＋所有讀數分段（規格、溫度、負載…），中文名稱、單位、一句說明；每段帶 `measure`（量的是什麼，前端配圖示與顏色），「顯存被誰佔用」是 `ranking`；挑剩的放「其他讀數」（每行自己帶 `measure`）。純函式，`tests/test_details.py` 用示範資料測 |
| `src/hwglance/sensors/labels.py`、`thresholds.py` | 英文感測器名稱 → 中文；溫度門檻（正常／偏高／過熱） |
| `src/hwglance/sensors/history.py`、`service.py` | 最近 10 分鐘的歷史；背景執行緒＋快照快取（硬碟每 10 輪才讀一次） |
| `src/hwglance/sensors/blackbox.py` | 黑盒子（SPEC D26）：每 2 秒把主要讀數寫進 `data/blackbox/日期.jsonl`（寫檔在自己的執行緒、每 5 秒 fsync、留 7 天）、讀一段時間給事件頁（`GET /api/blackbox`）；`DemoBlackBox` 是示範用、算出來的；`tests/test_blackbox.py` |
| `src/hwglance/sensors/guard.py` | 讀取的保險（SPEC D25）：某一類讀失敗或顯示卡驅動報錯 → 停讀那一類（值顯示「—」、畫面上方說明）→ 穩定 1 分鐘後由 `lhm.py` 關掉重開；純邏輯，`tests/test_guard.py` |
| `src/hwglance/api/` | `app.py` 端點（清單在檔案開頭）、`security.py` Host 檢查與 token |
| `src/hwglance/settings.py` | 設定 `data/settings.json`：單位、更新頻率、關視窗留在系統匣、門檻、卡片顏色（門檻與顏色只存改過的）；壞掉就用預設 |
| `src/hwglance/core.py` | 本體：服務＋伺服器＋系統匣＋主畫面的視窗控制（`Window`：開、叫到前面、排指令、結束）＋主畫面與迷你模式二選一（`Views`）、套用設定、選埠 |
| `src/hwglance/tray.py` | 系統匣（pystray＋Pillow）：圖示畫 CPU 溫度、提示文字、右鍵選單；畫圖與文字是純函式 |
| `src/hwglance/ui.py` | 主畫面的視窗程序（pywebview）：只有主畫面（`UiApp.switch` 叫出來）、長輪詢本體指令（`poll_commands`） |
| `src/hwglance/mini/` | 迷你模式（SPEC D29）：`items.py` 能顯示什麼、從快照怎麼拿值（純函式）、`render.py` 用 Pillow 畫成有透明度的圖（顏色照 tokens.css 深色，`tests/test_mini.py` 會比對）、`window.py` Win32 分層視窗（自己的執行緒＋訊息迴圈、每個螢幕的 DPI、拖曳、雙擊、右鍵選單、鎖定＝滑鼠穿透，位置記在 `data/mini.json`）、`__init__.py` 的 `MiniMode` 接起來 |
| `src/hwglance/fps.py` | 遊戲 FPS：跑 PresentMon（`vendor/presentmon`）讀 CSV，前景程式最近 1 秒的 FPS、幀時間、10 秒的 1% Low；只在迷你模式開著、有勾 FPS 時才跑；`DemoFps` 示範用；`tests/test_fps.py` 用假的輸出測 |
| `src/hwglance/instance.py` | 單一執行個體：`data/instance.json`＋`python -m hwglance --activate [--mode mini]` |
| `src/hwglance/probe.py` | 探測工具（`probe.bat`）：列出讀得到的所有感測器 |
| `src/hwglance/system.py` | 是不是系統管理員、PawnIO 版本、WebView2 版本（沒有就用瀏覽器開）、CPU 快取大小（Windows API）、佔用顯存的程式（PDH `\GPU Process Memory(*)\Dedicated Usage`＋程式名稱）、畫面上的提醒文字 |
| `src/hwglance/events/` | 事件記錄（SPEC D24）：`reader.py` 讀 Windows 事件記錄（wevtapi，ctypes，唯讀）、`catalog.py` 要讀哪些事件（`WATCHED`）與每種的中文說明、藍屏代碼、建議、`incidents.py` 歸成一件件事（純函式）、`demo.py` 照這台 6～10 月的真實記錄、`service.py` 60 秒快取、`watch.py` 訂閱顯示卡驅動的新錯誤（一報錯就先停讀顯示卡，`core.py` 接到 `guard`）；`tests/test_events.py` |
| `scripts/fetch_lhm.py` | 從官方 release 取 LHM 0.9.6（比對 SHA-256），解到 `vendor/lhm/`（不進 git） |
| `scripts/fetch_presentmon.py` | 從官方 release 取 PresentMon 2.6.0 命令列版（比對 GitHub 公布的 SHA-256）＋LICENSE、THIRD_PARTY，放到 `vendor/presentmon/`（不進 git；`setup.bat` 第一次會跑，失敗只是沒有 FPS） |
| `scripts/build_portable.py` | 產生可攜版 `build/HWGlance-版本-portable-win64.zip`（SPEC D27）：python.org 嵌入版 Python（跟 venv 同版、比對 SHA-256，`EMBED` 表）＋執行用套件（版本照 venv）＋src、vendor/lhm、frontend/dist、`HWGlance.bat`、`probe.bat`、使用說明與第三方授權；`--no-zip` 只產生資料夾。`build/` 不進 git |
| `scripts/make_icon.py`、`make-shortcut.ps1` | 產生 `assets/app.ico`（弧形儀表，跟介面 logo 一樣）；建立桌面捷徑 `HWGlance.lnk`（指向 `run.bat`、最小化啟動） |
| `docs/images/dashboard.webp` | ProjectHub 卡片的封面（示範資料的主畫面截圖，16:10） |
| ProjectHub 的 `registry/hwglance/project.toml` | 登錄到 ProjectHub（只有「啟動」一個按鈕）；要改照 ProjectHub 的 `docs/SOP-edit-project.md` |
| `frontend/src/pages/` | `Dashboard.tsx` 主畫面（即時監控，`#live`）、`Events.tsx` 事件記錄頁（`#events`，`App.tsx` 的 `usePage` 看網址切換），只負責排版；迷你模式不是網頁（後端 `mini/`），「迷你模式」按鈕是叫 `POST /api/ui/open` |
| `frontend/src/features/` | `overview/`（總覽磚塊）、`devices/`（CPU／GPU 大卡、主機板、記憶體、硬碟）、`details/`（點硬體跳出的詳細資訊視窗，原生 `<dialog>`，開著時跟著快照每秒抓 `/api/device`）、`events/`（事件頁的摘要與篩選、清單（零星的收成一行）、`BlackBox.tsx` 點開一件事時的黑盒子讀數、`useEvents.ts` 抓 `/api/events` 與頂端列的 7 天徽章）、`status/`（頂端列與分頁、提醒、底部）、`settings/`（設定面板；`MiniSection.tsx` 是迷你模式那一段） |
| `frontend/src/components/` | `TempGauge`（弧形儀表）、`Sparkline`（折線）、`Card`（給 `onOpen` 就整張可點、標題列有「詳細」按鈕）、`Readout`（數值格與清單列）、`Segmented`（分段按鈕） |
| `frontend/src/lib/` | `api.ts`（唯一發請求的地方）、`useSnapshot.ts`（每秒抓、看不到就停）、`useHistory.ts`、`format.ts`（溫度一律傳 °C 進來，顯示時才換 °F）、`prefs.ts`（溫度單位的 context）、`colors.ts`（設定的卡片顏色寫到 `<html>` 的 `--kind-*`，只寫改過的）、`measures.ts`（數值量的是什麼 → 圖示，卡片與詳細資訊共用）、`bridge.ts`（桌面視窗的切換／關閉） |
| `frontend/src/styles/tokens.css` | 顏色與字級，全站視覺只在這裡調；溫度三色 `--ok`／`--warm`／`--hot`；硬體種類色 `--kind-*`（預設指向調色盤 `--swatch-*`，設定頁可換；外層加 `kind-cpu` 這類 class）用在圖示與裝飾（`--deco`：磚塊色條、儀表、走勢、長條）；數值的種類色 `--m-*`（負載、時脈、功耗、風扇、容量，外層加 `m-load` 這類 class，SPEC D28）；**溫度數字只用 `--level`（綠黃紅），不要套種類色**，外框也不要上色（使用者兩次糾正，SPEC D22） |

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
   新的溫度種類 → `thresholds.py` 的 `DEFAULTS` 與 `LABELS`；迷你模式的項目 → `mini/items.py` 的 `ITEMS`（設定頁照 `catalog()` 自己列）；
   `tokens.css` 深色的 `--swatch-*`、`--m-*`、等級色 → `mini/render.py` 的同名表（`test_palette_matches_tokens_css` 會擋）；
   詳細資訊的欄位（`details.py`）→ `types.ts` 的 `DeviceDetail`；規格的欄位（`lhm.specs`）→ `demo.specs` 要一樣；
   調色盤（`settings.COLOR_PRESETS`、`COLOR_DEFAULTS`）→ `tokens.css` 的 `--swatch-*`、`--kind-*` 與 `lib/colors.ts` 的 `PRESET_NAMES`；
   新的 `measure`（`details.MEASURES`）→ `types.ts` 的 `Measure`、`lib/measures.ts`、`index.css` 的 `.m-*`；
   事件記錄的欄位（`events/incidents.py` 的 `incident`／`report`）→ `types.ts`；`catalog.WATCHED` 加來源 → 要有對應的規則
   （`test_watched_covers_every_rule` 會擋）；黑盒子記的鍵就是 `normalize.history_values`（改了舊檔照樣讀得到，
   說明寫在每個檔裡），回傳的欄位（`blackbox.summarize`）→ `types.ts` 的 `BlackBoxWindow`。
8. commit 不加 AI 署名；repo 自設身分 `HWGlance <jacky0788@users.noreply.github.com>`；使用者說要才 commit。
   遠端是 GitHub **公開** repo `jacky0788/HWGlance`（2026-11-02 重開成公開、MIT；舊的私人 repo 改名 `HWGlance-old`，
   裡面有含真實硬碟序號的舊歷史，待使用者刪掉），使用者說要才 push。公開了：**測試、示範資料不能放真機的序號、路徑**，
   push 前掃一次（2026-11-02 在 tests/test_events.py 抓到一個，已改寫歷史拿掉）。

## 怎麼跑

| 要做 | 怎麼做 |
|---|---|
| 使用者開監控 | 雙擊 `run.bat`（第一次會跑 `setup.bat ui`：venv、LHM、建置介面；已經開著就叫出原視窗） |
| 看這台讀得到哪些感測器 | 使用者雙擊 `probe.bat`（跳 UAC）→ 結果在 `data/probe/probe-日期-時間.txt`，agent 讀這個檔 |
| 改介面時預覽 | `F:\Desktop\claude_project\.claude\launch.json` 的 `hwglance-demo`（埠 8797，示範資料，設定存在 `data/demo`；它是 `--serve`，沒有迷你模式） |
| 看顯示卡重設時的畫面 | launch.json 的 `hwglance-demo-gpu-reset`（埠 8798，`--demo-gpu-reset 15`：開啟 15 秒後模擬一次，約 1 分鐘後重新連上） |
| 看別種電腦的畫面 | launch.json 的 `hwglance-demo-intel`（埠 8799，`--demo-profile intel-laptop`） |
| 給另一台電腦 | `.venv\Scripts\python.exe scripts\build_portable.py` → `build\` 裡的 zip；改了程式或介面要重新產生（介面先 `npm run build`） |
| 不用系統管理員看桌面視窗 | `.venv\Scripts\pythonw.exe -m hwglance --demo --data data/demo-desktop`（`--data` 讓設定不跟真的混在一起；用完刪掉那個資料夾） |
| 叫出迷你模式／只開系統匣 | `python -m hwglance --activate --mode mini`；`python -m hwglance --hidden`；快速鍵 Ctrl+Alt+M 開關、Ctrl+Alt+L 鎖定／解除 |
| 使用者的迷你模式鎖住了、找不到系統匣 | 本機 API：GET `/api/settings`，把 `mini.locked` 改成 false 用 PUT 存回去（token 在 `data/instance.json`），其他欄位照原樣帶 |
| 看迷你模式（不用系統管理員） | `.venv\Scripts\python.exe -m hwglance --demo --mode mini --port 8811 --data <暫存資料夾>`（示範 FPS，不跑 PresentMon）；截圖要 `ImageGrab.grab(..., include_layered_windows=True)`，用 API 結束 |

## 環境事實（2026-10-29～31 驗證過）

- 這台：Ryzen 7 5800X、RTX 5070 Ti、ASUS TUF GAMING X570-PLUS (WI-FI)（Super I/O 是 NCT6798D）、
  DDR4 沒有溫度感測器、6 顆硬碟（2 NVMe＋2 SATA SSD＋2 HDD）。LHM 原始碼對這塊主機板有專屬設定（3 個溫度、7 組風扇）。
- 真機 probe（2026-10-31，PawnIO 2.2.0.0）：LHM 全部讀得到。主機板溫度的 Identifier 是 CPU 22、Motherboard 2、Chipset 10；
  RTX 5070 Ti 沒有 GPU Hot Spot；硬碟用實體磁碟編號；SATA 型號帶結尾空白；硬碟溫度的最低值會是 0（讀到 SMART 前）；
  LHM 有重複的 Identifier（GPU Bus 與 GPU Memory 都是 `/gpu-nvidia/0/load/3`），不要拿感測器 id 當唯一鍵（溫度沒有重複）。
  這台閒置時晶片組約 78°C、KIOXIA 約 57°C（所以這兩種的預設門檻調高了，SPEC 的 D18）；CPU Tctl 在 30% 負載就 77°C（主機板量的 CPU 低約 11°C）。
  開全部種類時 LHM 開啟 6.7 秒、每秒一輪約 89 ms。細節見 `docs/SPEC.md` 的「真機 probe」。
- 對照 HWMonitor 1.68（`data/probe/HWMonitor.txt`，「File → Save Monitoring Data」存的）：報告裡沒有解好的硬碟溫度，
  要從 SMART 原始資料解（SATA 屬性 0xC2 原始值的第 1 個位元組；NVMe 健康紀錄第 1～2 位元組，單位 K）。
  NCT6798D 在 HWMonitor 只有通用通道名（SYSTIN、AUXTIN1…）；LHM 的 Motherboard＝SYSTIN、CPU＝PECI_0_CAL（Tctl 扣約 11°C，
  CPU 與機殼風扇照它轉）、Chipset＝SMBUSMASTER1（晶片組風扇照它轉）。RTX 50 的 GPU 熱點只有 HWMonitor 讀得到（直接讀顯卡暫存器）。
- 真機實測（2026-10-31，開著主畫面）：本體 99 MB、視窗程序 66 MB、WebView2 128 MB（合計約 295 MB），CPU 約整台 0.18%；
  LHM 每秒一輪 85～90 ms（大多在等硬體）。晶片組 5 分半鐘都在 75.5～76.5°C、跟 CPU 無關（確定是晶片組）；
  CCD1 每秒在 48～78°C 之間跳（所以 CPU 卡片不列 CCD，SPEC 的 D19）。量法：`/api/history` 看走勢；記憶體與 CPU 用 `Win32_PerfRawData_PerfProc_Process` 取兩次相減，
  從 `data/instance.json` 的 pid 往下找子程序（本體以系統管理員執行，一般權限讀不到它的命令列，但效能計數器讀得到）。
- 規格（2026-10-31）：SMBIOS（BIOS 5021、主機板、AM4／8 核 16 緒、記憶體插槽）由 LHM 讀；硬碟韌體、介面、Windows 判斷的健康與
  顯示卡驅動查 WMI（`MSFT_PhysicalDisk`、`Win32_VideoController`，非管理員也查得到，在這台試過）。這兩顆機械硬碟 Windows 回報轉速
  4294967295（不知道），所以不顯示轉速。CPU 快取用 `GetLogicalProcessorInformation`（SMBIOS 的 L3 報 32.5 MB，不準）。
  NVIDIA 驅動版本＝Windows 版本的最後五位數（32.0.16.1088 → 610.88）。
- 事件記錄（2026-10-31）：一般權限讀得到系統與應用程式記錄（agent 自己也能讀，唯讀）；系統記錄 20 MB、最早到 6/11；
  90 天約 0.9 秒。6008 的「最後運作時間」可能比真的當機早半小時以上，不能當當機時間；即時傾印的 WER 報告可能幾個月後才送，
  時間看傾印檔名；nvlddmkm 沒有訊息定義（說明在第 2 個資料欄位）。這台的歸類結果在 SPEC「第 6 段①」。
- .NET 4 起原生程式碼裡的存取違規（Corrupted State Exception）一般的 catch 接不到，程式直接結束（10/31 顯示卡重設時本體就這樣沒了）。
  CLR 啟動前設 `COMPlus_legacyCorruptedStateExceptionsPolicy=1` 就接得到，啟動後拿掉也照樣有效（2026-11-01 用暫存程序驗證，
  P/Invoke 進原生 DLL 的存取違規 200 次都傳回 Python）。要驗證這類事不要故意讓程式當掉：會在事件記錄留下一筆假當機。
- 可攜版（2026-11-01 驗證）：嵌入版 Python 的 `python314._pth` 列 `python314.zip`、`.`、`Lib\site-packages`、`..\src`，
  **不寫 `import site`**（寫了會把那台使用者的 `AppData\Roaming\Python` 套件也加進來）；pythonnet 在嵌入版照樣載得起來
  （用的就是同一層的 python314.dll）。`proxy_tools`（pywebview 要的）只有原始碼包，pip 要用 `--prefer-binary` 不能 `--only-binary`。
  解壓到暫存資料夾驗過：伺服器、事件記錄、黑盒子、桌面視窗（WebView2）、用 API 結束後程序收乾淨。
- 沒有 WebView2 Runtime 時 pywebview 會退回 IE 引擎（MSHTML），React 介面一片空白；`system.webview2_version()` 看 EdgeUpdate 的登錄
  （這台在 `HKLM\SOFTWARE\WOW6432Node\…\{F3017226-…}` 的 `pv`），沒有就 `core.Window` 改用預設瀏覽器開。
  pywebview 預設 `OPEN_EXTERNAL_LINKS_IN_BROWSER`：`target="_blank"` 的連結用系統瀏覽器開。
- 別種硬體（照 LHM v0.9.6 原始碼）：Intel 每個核心有「`<核心> Distance to TjMax`」，類型是 Temperature 但不是溫度（不能當溫度顯示），
  TjMax＝核心溫度＋它；混合架構核心叫 `P-Core #n`／`E-Core #n`（效能核 1 和節能核 1 是不同核心）；負載的執行緒名稱還是 `CPU Core #n Thread #m`。
  AMD 顯示卡的整張卡功耗是 `GPU Package`，還有 `GPU VR *`（供電）、`GPU Liquid`、`GPU PLX` 溫度；Intel 內顯沒有溫度、負載用 `D3D 3D`。
  LHM 讀 NVIDIA 時不管顯示卡是不是在休眠，筆電獨顯可能因此一直醒著（使用說明有寫）。
- EvtSubscribe 訂閱系統記錄（拉的：Windows 設 event、EvtNext 拿）一般權限就能用；驗證時把 `events/watch.py` 的
  `SUBSCRIBE_FLAGS` 換成 2（從最舊的開始），就能拿現有的記錄重播，不用寫假事件。
- Python 3.14.3、pythonnet 3.2.1、pywebview 6.2.1、FastAPI 0.162；前端 React 19.3、Vite 8.3、TypeScript 7.0、Tailwind 4.3。
- 不靠驅動能讀的只有顯卡（nvidia-smi 非管理員實測 70°C）。WMI 的 ACPI 溫度回「不受支援」、硬碟 SMART 非管理員被拒。
- **auto mode 擋過 agent 執行下載的 LHM DLL**（理由 Code from External）：真的讀數由使用者跑 `probe.bat`／`run.bat`，
  agent 不要想辦法繞過去自己執行。PresentMon 也一樣：agent 只下載、比對雜湊，不自己跑；拿遊戲實測 FPS 是使用者的事。
- PowerShell 把 `$null` 傳給 P/Invoke 的 string 參數會變成空字串：`FindWindow` 要傳 `[NullString]::Value`。
- 截視窗要先 `SetProcessDpiAwarenessContext(-4)`，否則 `GetWindowRect` 拿到縮放過的座標，只截到左上角。
- 量記憶體看私有工作集（`Win32_PerfFormattedData_PerfProc_Process.WorkingSetPrivate`）；工作集直接加總會重複算共用的 DLL。
- venv 的 pythonw 會再開一個真正的子程序：找主畫面用標題（`HWGlance`），不要用 Popen 的 pid；迷你模式用類別名稱 `HWGlanceMini` 找。
  使用者自己開著的新版也有 `HWGlanceMini`：測試時用 EnumWindows＋程序樹（自己開的程序與它的子程序）分辨。
  使用者自己開著的 HWGlance 也叫 `HWGlance`：測試時用命令列（`--ui` 加測試的埠，只看 python 程序）分辨是不是自己開的。
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
- 實測（示範資料）：只剩系統匣時本體約 49 MB 私有記憶體；從系統匣叫回主畫面約 0.5 秒。
- 透明視窗（2026-11-01 實測）：pywebview＋WebView2 的視窗只能整個一起半透明（`transparent=True`、DWM blur／glass、color key 都透不過去），
  所以迷你模式用 `UpdateLayeredWindow`＋預乘 BGRA（Pillow 的 `RGBa` 是預乘過的）。全透明的像素滑鼠會穿過去：背景至少留 1/255 才拖得動。
  在 Pillow 的透明底上直接畫字邊緣會發黑：用灰階遮罩＋顏色圖層 `alpha_composite`。
