# HWGlance

輕量的硬體溫度監控：打開就一眼看到 CPU、顯示卡、主機板、每顆硬碟的溫度。介面參考 NZXT CAM，只留監控的部分。

## 需要

- Windows 10／11、Python 3.11 以上；第一次建置介面要 Node.js 20 以上
- [PawnIO](https://github.com/namazso/PawnIO.Setup/releases) 驅動：讀 CPU 與主機板的溫度用（顯示卡溫度、各種用量不需要）
- 以系統管理員執行：`run.bat`、`probe.bat` 會自己跳 UAC 要權限

## 使用

- 桌面的「HWGlance」捷徑或 `run.bat`：開監控。第一次會建虛擬環境、解出 LibreHardwareMonitor、建置介面；已經開著就叫出原本的視窗。
  捷徑不見了就重建：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\make-shortcut.ps1`。
- 關掉視窗後還在系統匣繼續監控，圖示上的數字就是 CPU 溫度；左鍵開主畫面、右鍵有迷你模式與結束。
- 主畫面右上角「迷你模式」換成置頂的小視窗；「設定」可以改溫度單位、更新頻率、各種硬體的溫度門檻。
- `probe.bat`：探測這台電腦讀得到哪些感測器，結果寫到 `data\probe\`。

技術說明、決策與進度見 [docs/SPEC.md](docs/SPEC.md)；給接手的 agent 看 [AGENTS.md](AGENTS.md)。

感測器讀取用 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)（MPL-2.0）。
