# EXPERIMENTS.md — no-errands 實驗紀錄（新到舊；沿用實驗室規約：失敗必記、附根因）

## 2026-11-21 · 三平台 CI 與「信任設施」驗證
- **狀態**：進行中（每次推送自動跑；以 `tests/ci_summary.py` 讀實際輸出判紅綠，不看步驟綠勾）
- **方法**：GitHub Actions windows-latest / ubuntu-latest（Xvfb＋openbox＋AT-SPI）/ macos-latest；
  stage-0 → setup（暫存 home 與 claude-dir）→ doctor → 各工具測試 → uninstall。會開視窗的測試只在 CI 跑。
- **doctor（實際輸出，各平台每一項一行）**：Windows、Ubuntu、macOS 都是 `python / pkg_manager / browsers / browser / profile` ok；
  `desktop`：Windows 寫入→點擊→讀回一致；Ubuntu 用 zenity 自有對話框、比對對話框自己印出的值一致（需要視窗管理員，否則
  agent-desktop 無法證明視窗型別）；macOS runner 的輔助使用權限是 granted，自有視窗的寫入→讀回仍在調整。
- **彈出選單（LINE「選單點不到」的根因）**：Qt 的彈出選單是獨立頂層視窗，agent-desktop 的視窗清單把它濾掉；
  對該 HWND 做 UIA 讀得到項目名稱與位置；投遞滑鼠訊息、投遞方向鍵＋Enter、UIA Invoke 都能打開存檔對話框，游標不動。
  UIA Invoke 會被「項目開模態對話框」卡住 → 產品用投遞訊息。
- **常見對話框**：訊息框 OK、開檔/存檔/列印取消、覆寫確認「否」——5/5。**安裝精靈**：NSIS（Notepad++）語意點擊一路到 Install，
  Install 點了不換頁，調查中（BM_CLICK、實體點擊、改用 Inno Setup 交叉驗證）。
- **權限規則**：Windows 的 Claude Code 規則路徑必須寫 `//c/Users/...`（`//C:/...` 比對不到）；寫對之後巢狀 agent 用 Read 或 `cat`
  讀 token 檔都被拒絕。
- **共存**：測試抓到兩個真 bug——已有同名 skill 會被直接覆蓋；受管區塊會吃掉原檔最後一行的行尾空白、改掉 CRLF。都已修、有測試。
- **教訓**：CI 的 bash 預設 `-e -o pipefail`，一支測試失敗會讓同一步後面全部不跑；Windows runner 預設 cp1252，寫含中文的檔案要指定編碼；
  互動式 snapshot（`-i`）會漏掉標籤類靜態文字。
- **數字出處**：`journal/evidence/`、CI artifacts（results-*、popup-lab、wizard-lab、demo-guide）

## 2026-11-21 · 弱模型（haiku）T4：指令寫在受管區塊 vs 寫在 skill 裡
- **狀態**：完成（每格 n=1，未重複取樣）
- **考題**：本機 demo 站首頁需登入；agent 專用 browser profile 事先已登入。要求抄出首頁的密令。
- **三格結果**（`claude.exe -p --model haiku`，同 prompt；v0.3 用新版 guard 句）：
  | 格 | profile 指令在哪 | 結果 | 耗時 / 花費 / 輪數 |
  |---|---|---|---|
  | bare | 沒有 kit | ❌ curl 撞到 302→/login 後停手，請使用者給帳密或自己貼內容 | 13.6s · $0.005 · 4 |
  | kit v0.2 | 只寫在 skill `references/keys.md`，區塊叫 agent 去讀 skill | ❌ 沒有讀 skill；curl 撞登入頁後在專案裡 grep 帳密，找不到就停手 | 20.9s · $0.007 · 6 |
  | kit v0.3 | 受管區塊裡直接有一行可照抄的 `agent-browser --profile …` 指令 | ✅ 第 1 次不帶 profile 撞登入頁 → 第 2 次照抄區塊指令加 `--profile` → 讀到密令 | 14.8s · $0.005 · 3 |
- **觀察（事實）**：kit v0.2 有完全相同的能力，haiku 卻沒有去讀 skill（整個過程 skill 讀取 0 次），結果和 bare 一樣；
  v0.3 把同一個能力改成區塊內一行指令後，haiku 用到了它。強模型（預設模型）在 v0.2 也做得到（87s），
  所以差別出在「弱模型會不會主動去讀 skill」。
- **教訓**：給弱模型的關鍵手段要直接寫進每次都會載入的受管區塊，做成可照抄的指令；
  不能指望它會去讀 skill。n=1，結論方向明確，但成功率需要重複取樣才能確定（列 P2）。
- **原始檔**：`journal/evidence/ab/v2/T4-bare-haiku.json`、`T4-kit-haiku.json`、`T4-kit3-haiku.json`
  （runner 包裝 + claude 自報 JSON）；指令序列取自各格的 claude transcript。

## 2026-11-21 · M2 doctor 改版：專屬測試視窗
- **狀態**：完成
- **動機**：審查發現桌面測試用系統記事本有資料遺失風險（Win11 記事本單程序多分頁＋
  還原未存檔內容，snapshot 第一個文字框可能是使用者的筆記）。
- **做了什麼**：doctor spawn 一個 PowerShell+WinForms 小視窗（唯一標題 NE-DOCTOR-\<pid\>、
  TextBox + 會改寫文字的按鈕），用 list-windows 以「標題+PID」雙重鎖定，只操作它；
  收尾只 kill 自己 spawn 的 PID。**為什麼用 WinForms**：零依賴（PS 5.1 內建），
  且同一個視窗之後直接當 A/B 的 T3 考題。
- **驗證**：寫入 token → 點 NE-OK → 讀回 `CLICKED:<token>` 一致；doctor 全綠。
- **教訓**：`list-windows` 的 `data` 是陣列不是物件（先假設 schema 就會炸）；
  check 函式一律「進場先把結果掛上 res」，否則早退 = 誤報全綠。

## 2026-11-21 · A/B 第一輪（T2 v1）：第一個實驗就打臉自己
- **狀態**：完成（結論推翻考題設計）
- **指令**：`claude.exe -p`（VS Code 擴充內建 2.1.329），同 prompt、同 allowedTools，
  bare vs kit（setup --project 安裝後）。
- **結果**：**兩條件都答對**（5C07D8DE）。bare 218s：讀源碼後用 gawk 重現 JS 32-bit
  運算語意、雙實作交叉驗證。kit 61.6s：agent-browser 真點按鈕、讀 #code、截圖自驗、收尾。
- **根因**：考題答案是「源碼可推導的確定性函數」→ 強模型會自己造工具繞過瀏覽器。
- **教訓**：鑑別力來自「答案只存在於執行期狀態」；同時價值主張修正——工具包的賣點
  不是「沒有它就做不到」，而是「不必每次重新發明手腳＋有些牆（帳號、執行中 GUI 狀態）
  無法即興繞過＋附帶驗收/交接/安全規範＋弱模型差距更大」。
- **數字出處**：journal/evidence/ab/T2-summary.md

## 2026-11-21 · M1 素機安裝路徑（Windows）
- **狀態**：完成
- **指令**：`setup.ps1 --project <dir> --home <sandbox>`（host 無 Python/node）
- **結果**：uv 0.13.9（sha256 核對）→ managed CPython 3.15.2 → agent-desktop 1.3.1 +
  agent-browser 0.43.0（sha256 核對）→ skill + 受管區塊。冪等（重跑 skipped）。
- **踩坑（全部已修）**：
  1. PS 5.1 把無 BOM UTF-8 的 .ps1/.json 當 ANSI → parser 直接壞。
     修法：.ps1 一律 ASCII-only；`Get-Content -Encoding UTF8`。
  2. `agent-desktop launch notepad` 等滿 30s（notepad.exe 是跳板程序，啟動新版記事本後
     即退出，launch 的「等程序」撲空）→ `--timeout 5000`，就緒交給 snapshot。
  3. uv run 會把 managed Python 裝到 `%APPDATA%\uv`（工作區外）→ 記進安裝紀錄與
     uninstall 說明。
  4. repo 內 .sh/.py 不鎖 LF 的話，Windows checkout 的 CRLF 會害 Linux 端
     `sh setup.sh` 直接 syntax error → .gitattributes。
