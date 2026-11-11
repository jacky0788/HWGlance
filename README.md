# HWGlance

輕量的硬體溫度監控：打開就一眼看到 CPU、顯示卡、主機板、每顆硬碟的溫度。介面參考 NZXT CAM，只留監控的部分。
另外有事件記錄（當機、顯示卡、硬碟出錯，整理成一件件事）與黑盒子（當機前幾分鐘的讀數）。

## 裝到另一台電腦：可攜版（建議）

在這台產生：`.venv\Scripts\python.exe scripts\build_portable.py` → `build\HWGlance-版本-portable-win64.zip`（約 29 MB）。
裡面有 python.org 官方的嵌入版 Python 與需要的套件、LibreHardwareMonitor、PresentMon（遊戲 FPS）、建好的介面，**另一台不用裝 Python、Node.js、git**：

1. 把 zip 解壓到想放的地方，雙擊 `HWGlance.bat`；跳出權限視窗按「是」才讀得到 CPU、主機板、硬碟的溫度。
2. CPU 與主機板的溫度要 [PawnIO](https://pawnio.eu) 驅動（裝一次，要系統管理員）；沒裝畫面上會提醒。
3. 設定、log、黑盒子都存在解壓位置的 `data\`。詳細步驟在 zip 裡的「使用說明.txt」。

另一台需要：Windows 10／11 64 位元、WebView2 Runtime（Windows 11 內建；沒有的話改用預設瀏覽器開畫面）。
不能用系統管理員或不能裝驅動的電腦（例如公司電腦），還是看得到顯示卡溫度、各種用量、事件記錄與黑盒子。
讀數不對或缺的，在那台跑 `probe.bat`，把 `data\probe\` 的結果帶回來就能補規則。

## 開發用（這台）

- 需要 Windows 10／11、Python 3.11 以上；第一次建置介面要 Node.js 20 以上
- 桌面的「HWGlance」捷徑或 `run.bat`：開監控。第一次會建虛擬環境、解出 LibreHardwareMonitor、建置介面；已經開著就叫出原本的視窗。
  捷徑不見了就重建：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\make-shortcut.ps1`。
- 關掉視窗後還在系統匣繼續監控，圖示上的數字就是 CPU 溫度；左鍵開主畫面、右鍵有迷你模式、鎖定迷你模式與結束。
- 主畫面右上角「迷你模式」換成疊在遊戲上的小視窗（像 RivaTuner）：背景透明度、大小、要顯示的項目（溫度、負載、時脈、功耗、
  顯存、遊戲 FPS…）都在「設定」裡改；拖曳移動、雙擊回主畫面、右鍵有選單（含「迷你模式設定…」），鎖定後滑鼠會穿過去。
  快速鍵：**Ctrl+Alt+M** 開關迷你模式、**Ctrl+Alt+L** 鎖定／解除鎖定（鎖住時也能用）。
  遊戲 FPS 用 Intel 的 [PresentMon](https://github.com/GameTechDev/PresentMon)（MIT，第一次 `setup.bat` 會下載），要系統管理員；
  獨佔全螢幕的遊戲會蓋過迷你模式，遊戲改成無邊框視窗就看得到。
- 「設定」還可以改溫度單位、更新頻率、各種硬體的溫度門檻與卡片顏色（調色盤或自訂）。
- `probe.bat`：探測這台電腦讀得到哪些感測器，結果寫到 `data\probe\`。

技術說明、決策與進度見 [docs/SPEC.md](docs/SPEC.md)；給接手的 agent 看 [AGENTS.md](AGENTS.md)。

## Mac（Apple 晶片，開發中）

- 需要 macOS 11 以上的 Apple 晶片 Mac、Python 3.11 以上（`brew install python@3.12`）；第一次建置介面要 Node.js 20 以上。
- `./run.sh`：開監控。第一次會建虛擬環境、裝套件、建置介面；已經開著就叫出原本的視窗。**一般權限就讀得到溫度**（SMC），不用驅動、不用 sudo。
- `scripts/make-mac-app.sh`：在「應用程式」建好 `HWGlance.app`，之後從 Launchpad、Spotlight 打開或拖到 Dock；搬了資料夾就重跑一次。
- 選單列上的數字就是 CPU 溫度（偏高變橘、過熱變紅），點開看每個溫度、開主畫面或結束；關掉視窗後還在選單列繼續監控。
- 看得到 CPU、GPU、電池、內建 SSD 的溫度，風扇轉速、CPU／GPU 使用率與記憶體。
- 「事件記錄」頁：macOS 的核心錯誤（kernel panic）報告，加上每次開機時記下的上次關機原因（看門狗重開、按住電源鍵、斷電、過熱）；
  上次關機原因系統只留最近一次，所以從第一次開 HWGlance 之後才有歷史。迷你模式、暫存檔、時脈與功耗 Mac 版還沒有。

## 授權

HWGlance 本身是 [MIT](LICENSE)。用到的第三方元件各有自己的授權，都不放在這個 repo 裡（第一次執行時從官方 release 下載、比對 SHA-256）：

- 感測器讀取：[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)（MPL-2.0）
- 遊戲 FPS：[PresentMon](https://github.com/GameTechDev/PresentMon)（MIT）
- 可攜版另外附上嵌入版 Python 與套件，授權清單在可攜版的「第三方授權.txt」
