# EXPERIMENTS.md — no-errands 實驗紀錄（新到舊；沿用實驗室規約：失敗必記、附根因）

## 2026-11-21 · 全新安裝＋onboarding（巢狀 agent 照 repo CLAUDE.md 自己裝）
- **方法**：fresh clone → `claude -p --model claude-opus-5-5 "安裝不求人…用 --project…桌面檢查先不要跑"`，`NO_ERRANDS_HOME` 指向沙盒；
  事後比對真實 `~/.claude` 與 `~/.no-errands` 前後狀態。
- **第一輪**：97 秒、14 輪；裝好、區塊與 skill 都在 clone 裡、doctor 跑了且照指示跳過桌面。**但真實 home 被動到**——它回報了兩個 bug：
  (1) stage-0（setup.ps1/setup.sh）下載 uv 時不看 `NO_ERRANDS_HOME`，uv 落在真實 `~/.no-errands/bin`；
  (2) 跳過的桌面檢查在受管區塊被寫成兩行「❌ 不可用：doctor 未通過」。
  另一輪抓到 doctor 收尾用 `close --all` 會關掉使用者自己的 agent-desktop session。三個都已修（stage-0 順序 `--home` → `NO_ERRANDS_HOME` → 預設；
  沒跑的檢查寫「⚠ 未驗證（doctor 跳過）」且同一項只寫一次；doctor 只關自己開的 session）；殘留的 3 個 uv 檔已刪。
- **第二輪（修完重跑，同 prompt）**：84 秒、12 輪；真實 `~/.claude`、`~/.no-errands` 前後完全相同（後者不存在）；區塊顯示一行「⚠ desktop 未驗證」；
  agent 給使用者的結尾是一張每項可用／沒跑＋原因的表，附補跑方式、發件匣網址、移除指令——和 repo CLAUDE.md 的六步一致。
  modelUsage 除指定的 opus-5-5 外有 haiku-5-5（claude 內部的輔助呼叫，非受測主體）。
- **教訓**：讓真的 agent 照文件裝一次，比單元測試更早抓到「環境變數只在第二階段生效」這種跨階段的漏洞。

## 2026-11-21 · 跑腿稽核 skill 實跑（使用者自己的 25 個 session）
- **結果**：初篩 125 → 候選 121 → 逐筆複判：真跑腿 19（權限 6、GUI 5、帳號牆 1、驗收回傳 2、物理 5）；教學模式約 13、決定 4，其餘是誤判
  （主要是本專案自己的條列式進度報告）。現在的工具包接得住約 8 筆（GUI＋帳號牆＋驗收回傳）。
- **教訓**：「手把手教學」偵測對條列式文字誤判多，第二步人工式複判不能省。去識別化統計只放分享素材，不進 repo。

## 2026-11-21 · 桌面聊天 app（LINE 26.x，Qt）：開對話、驗身分、彈出選單匯出
- **真機（使用者在場、每次事前同意）**：清單是 listbox 底下的按鈕，名稱打包 `名稱, N人, 未讀數, 最後一則預覽, 日期`——
  清單本身就洩漏其他對話內容，所以讀取一律遮罩（只看符合的那一項）。
  1. 語意點清單項目 → LINE 自己把視窗拉到前景（游標沒動）。→ 會搶前景的步驟要等使用者閒置（閒置閘門）。
  2. 「現在開著哪個對話」不在清單裡；第四次才找到：右側**標頭工具列第一顆按鈕 = `名稱, N人`**。→ LINE profile 用它驗身分。
  3. 「其他功能」選單**不在 UIA 樹裡**。根因（CI 上用 Qt6 實驗室重現）：Qt 彈出選單是**獨立頂層視窗**，agent-desktop 的視窗清單把它濾掉；
     對那個 HWND 做 UIA FromHandle，項目名稱與位置都讀得到；投遞滑鼠訊息／方向鍵＋Enter／UIA Invoke 都能開存檔對話框，游標不動。
     UIA Invoke 會被「項目開模態對話框」卡住 → 產品用投遞訊息（`scripts/popup_menu.py`）。
  4. `minimize` 是把前景還給使用者的乾淨方式（不注入輸入）。
- **產品化**：chat_guard 匯出流程：閒置閘門 → 開對話 → 標頭驗身分 → 彈出選單只點名稱完全相符的項目（有「儲存並刪除」之類誘餌也不會誤點）→
  存檔對話框預設檔名要含對話名（否則取消）→ 寫入內層檔名欄並讀回 → 覆寫確認一律「否」→ 存完檢查檔案第一行含對話名（否則刪檔）→ 最小化交還前景。
  使用者一有輸入（100ms 守衛）就最小化目標並停手。
- **狀態**：假聊天（含誘餌項目）CI 驗證；真機匯出走閒置閘門，結果見 LOG 與分享素材的平台矩陣。

## 2026-11-21 · 核准發件匣與「信任設施」
- **發件匣**（`tests/test_outbox.py` 32 條）：無頁面密鑰／錯誤 Host／跨站 Origin／自動化瀏覽器 UA → 403；核准送原文、修改後送修改版、退回不送；
  頁面載入後草稿被改 → 409；收件對象由伺服器查出，查不到就不能核准；埠被占用 → 回 recovery、草稿保留。
- **agent 自己去按核准**：agent-browser 的 `navigator.webdriver=true`、UA 含 HeadlessChrome → 按鈕停用；用 JS 強制啟用再點 → 伺服器以 UA 拒絕，送出 0 則。
  限制照寫：會偽造 UA、用 curl 抓密鑰的 agent 仍繞得過——這是防「太熱心」，不是安全邊界。
- **Windows 踩坑**：`ThreadingHTTPServer` 預設 `SO_REUSEADDR`，在 Windows 上會搶走別的程式正在聽的埠 → `SO_EXCLUSIVEADDRUSE`；用 connect 判斷埠占用不可靠 → 改 bind。
- **不干擾（N4）**：WM_CHAR 投遞到另一個視窗 200/200 字一致，共用測試期間前景與游標都沒變；正對照（刻意搶焦點）在 CI 被偵測到，證明取樣靈敏度。
- **Mattermost 唯讀**：只發 GET；POST／非 `/api/v4`／搜尋（Mattermost 的搜尋是 POST）在本機拒絕；GET 讀貼文不動已讀（自架伺服器上用另一個使用者製造未讀後驗證）。
  token 檔可直接用頁面複製下來的格式（中文全形冒號或英文 `Access Token: …`）。

## 2026-11-21 · 事故（兩起，都已改成機制）
- **INC-1**：A/B 實驗的受測 agent 發現使用者剛登入的 LINE 在跑，自己去搜尋、截圖、點擊（零按鍵、零送出；不能排除動到已讀）。
  根因：考題前提被現實推翻；當時聊天守則還沒同步到受測環境；沙盒 guard 句只管「改檔」不管「讀取或操作其他 app」。
  → 改成機制：ned 對通訊類 app 未授權一律拒絕；guard 句改為「除列出目標外不得讀取或操作任何其他應用程式」。
- **INC-2**：沒有工具包的受測 agent 自己寫 Win32 呼叫完成桌面題——搶前景 10 次、模擬滑鼠 20 次、全螢幕截圖 6 次，當時使用者正在用電腦。
  guard 句只說「不得操作其他 app」，沒管「怎麼操作」。→ 不再在使用者桌面跑沒有工具包的桌面題；guard 句加「不得移動滑鼠、不得改變前景、不得擷取全螢幕」；
  會開視窗的測試一律放 CI 或等閒置閘門。殘留截圖未開啟即刪除。
- **教訓**：即興的手沒有規矩——這正是「信任靠基礎設施」的實證。

## 2026-11-21 · 三平台 CI 與「信任設施」驗證
- **狀態**：進行中（每次推送自動跑；以 `tests/ci_summary.py` 讀實際輸出判紅綠，不看步驟綠勾）
- **方法**：GitHub Actions windows-latest / ubuntu-latest（Xvfb＋openbox＋AT-SPI）/ macos-latest；
  stage-0 → setup（暫存 home 與 claude-dir）→ doctor → 各工具測試 → uninstall。會開視窗的測試只在 CI 跑。
- **doctor（實際輸出，各平台每一項一行）**：Windows、Ubuntu、macOS 都是 `python / pkg_manager / browsers / browser / profile` ok；
  `desktop`：Windows 寫入→點擊→讀回一致；Ubuntu 用 zenity 自有對話框、比對對話框自己印出的值一致（需要視窗管理員，否則
  agent-desktop 無法證明視窗型別）；macOS 用 JXA 建的自有 Cocoa 視窗寫入→讀回一致。
- **彈出選單（LINE「選單點不到」的根因）**：Qt 的彈出選單是獨立頂層視窗，agent-desktop 的視窗清單把它濾掉；
  對該 HWND 做 UIA 讀得到項目名稱與位置；投遞滑鼠訊息、投遞方向鍵＋Enter、UIA Invoke 都能打開存檔對話框，游標不動。
  UIA Invoke 會被「項目開模態對話框」卡住 → 產品用投遞訊息。
- **常見對話框**：訊息框 OK、開檔/存檔/列印取消、覆寫確認「否」——5/5。
- **安裝精靈**：NSIS（Notepad++）與 Inno Setup（WinMerge）都從第一頁點到 Finish、程式檔存在。關鍵兩個發現：
  (1) NSIS 的「Install」語意點擊／BM_CLICK／實體點擊都回 ok 但不換頁；**對父視窗投遞 WM_COMMAND(BN_CLICKED)** 才過（BM_CLICK 在對話框非作用中時會失效）。
  (2) Inno 的精靈 list-windows 看不到（owned 視窗、子程序 setup.tmp、按鈕類別 TNewButton）→ Win32 列舉＋WM_COMMAND 一路點完。
  完成頁「啟動 WinMerge」在 TNewCheckListBox 裡取消不了 → 程式被啟動鎖住檔案、移除失敗 → 移除前依安裝路徑找 PID 關掉。
- **自架服務**：Gitea 7/7（登入一次、新程序仍登入、讀私有內容、網頁建 token、token 有效、值不外洩）；Mattermost 18/18（管理介面開 PAT、網頁建 PAT、mm_ro 唯讀、發件匣核准後真的發出、退回的不發、GET 不動已讀）。
- **macOS**：doctor 桌面寫入→讀回一致（JXA 自有 Cocoa 視窗）；Office（LibreOffice）重算與轉檔 4/4。
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
