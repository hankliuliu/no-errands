# EXPERIMENTS.md — no-errands 實驗紀錄（新到舊；沿用實驗室規約：失敗必記、附根因）

## 2026-11-22 · 真實 Mattermost 送出與發件匣頁面的三個洞
- **真實送出**：真實安裝登記使用者的伺服器與 token（只給路徑），起草一則給他自己的私訊「test」；他在頁面按核准 → 送出 → 用唯讀工具讀回：貼文在、內容相符、未刪。
- **使用者說按了卻看不到**：其實已送出（在歷史頁）。但順著查出真的洞：(1) 舊分頁帶著上一個伺服器的頁面密鑰，按核准被拒只有一行小字；(2) 伺服器沒記決定請求，事後查不到；(3) 只有起草會起伺服器，重開機後待核准的草稿沒有頁面可按。
- **修法**：狀態回應帶伺服器實例 id，頁面發現不同就自動重新載入；被拒時頁面頂端紅色橫幅（role=alert），密鑰失效時自動重新載入；decide-log.jsonl 記每次決定的狀態碼與原因類別（不記內容與密鑰）；`outbox.py url` 會先確保伺服器在跑。test_outbox 38/38。

## 2026-11-22 · 聊天收集（gather）與 LINE 範圍實驗
- **C0 範圍實驗**（真實 LINE、白名單群組、螢幕鎖＋閒置閘門）：原樣匯出與「往上捲到底再匯出」位元組相同——2026.11.01–11.21、17 個日期、316 則；捲了 4 輪、訊息區項目數 33→33。結論：桌面版匯出＝已同步到這台的歷史（登入時回補約三週），捲動無效；更早的只能手機匯出再 import。
- **一路的坑**：(1) agent-desktop 的 scroll ref 在 LINE 上不唯一（AMBIGUOUS_TARGET）→ 改用 UIA 找訊息區（子項不是按鈕的 List）＋投遞 WM_MOUSEWHEEL；(2) 開完對話後第一次點選單沒出對話框（選單淡入中）→ 點前等、沒出就重來一次；(3) 第一次嘗試被輸入守衛中止——那是使用者本人還在打字，守衛照設計交還。
- **合併**：LINE 匯出沒有訊息 id → 以（日期、時間、發話者、文字）序列對齊；30 次隨機重疊切段、任意順序合併都等於原檔（含同分鐘三連發）；接不上 → 記缺口，重疊卻對不上 → 保留已存、不亂合。
- **Mattermost**：自架伺服器上「貼文 → 同步 → 編輯與刪除 → 再同步」：編輯更新、刪除標記、不默默消失；真實伺服器唯讀同步使用者自己的私訊頻道，第二次只拿新增的。
- **另一個順手抓到的**：token flow 測試在本機與 CI 同時壞掉，逐一試六種送出方式都有效 → 根因是固定 session 名稱沿用同名 session 的舊狀態；改成每次唯一。

## 2026-11-22 · 「視窗一直彈出」：不干擾測試的盲點
- **回報**：使用者說畫面有東西一直閃、是視窗反覆彈出、不定時出現。
- **量測**：每 0.1 秒列頂層視窗（只看類別與所屬程序，不讀標題），同時每 0.2 秒拍程序樹。抓到 Windows Terminal 視窗（`CASCADIA_HOSTING_WINDOW_CLASS`）與 `PseudoConsoleWindow` 反覆出現；主腦另外量到另一位執行手的測試跑的時候 20 秒 15 個、每個 0.2–0.36 秒、每次都搶前景。
- **原因**：沒有主控台的父程序（分離啟動的伺服器、背景測試、排程工作）再啟動主控台程式時，每個子程序都會拿到新的主控台；預設終端機是 Windows Terminal 的機器上，那就是一個看得見、會搶前景的視窗。我們的 neb／ned／chat_guard／popup_menu／uac／idle／demo／office 共 9 支腳本 17 處、加上 setup／doctor，都沒帶 `CREATE_NO_WINDOW`。之前的不干擾測試都從有主控台的程序跑，所以看不到。
- **修法**：`scripts/nowin.py`——在 Windows 上，沒有自己指定 creationflags 的子程序一律加 `CREATE_NO_WINDOW`（只影響主控台程式，GUI 視窗照常）；所有會開子程序的腳本與 setup／doctor 都 import 它。發件匣伺服器本來就自己帶了旗標。
- **驗證**：`tests/test_nowin.py`——以 DETACHED_PROCESS 啟動 neb×5、ned×5（父程序沒有主控台），20 毫秒取樣：彈窗 0、前景不變；靜態檢查每個會開子程序的腳本都有 import nowin。CI 加正對照：`NE_NOWIN_DISABLE=1` 關掉修正時必須抓得到彈窗。
- **教訓**：「不干擾」的測試要從使用者真實會遇到的父程序環境跑（背景、排程、分離），不能只從開發者的終端機跑。

## 2026-11-22 · 守門代理的洞：審查抓到、測試沒抓到
- **怎麼發現的**：主腦審查另一位執行手的分支時讀到 neb 的放行清單，對照 agent-browser 0.43.0 的說明：`find` 被當成唯讀，但它的預設動作就是 click，也能 fill；`wait --fn` 會在頁面上執行 JS（等同被擋掉的 eval）；`javascript:`／`data:` 網址被當成「https://javascript…」放行。原本 14 項 neb 測試沒有任何一項測到 `find` 或 `--fn`。
- **逐項對照後又找到**：`batch` 從一般網站出發、中途換到敏感網站再輸入，neb 只檢查起點；全域選項寫在子指令**後面**時 neb 沒解析（`--init-script`、`--headers`、`--user-agent`——最後一個還能騙過發件匣的自動化檢查）；`chat`（AI 代理）、`mcp`、`flow run`、`connect`、`auth login`、`get cdp-url` 等控制面指令完全繞過逐步檢查。ned：`launch` 不檢查目標（`ned Notepad.exe launch …LINE.exe` 會過——正是事故一的模式）、背景模式沒擋 `--headed`、`--surface notification-center/desktop/focused` 會讀到別的 app、`wait --notification` 讀所有通知。
- **更大的一個**：neb 根本沒有寫進受管區塊或 skill——agent 不會知道要用它；受管區塊的「看網頁」指令是直接呼叫 agent-browser。
- **修法**：neb 改成敏感網站白名單＋控制面指令與危險選項任何網站都拒＋每個導覽網址檢查 scheme 與網域＋`batch` 拆開逐一檢查；ned 補 launch 目標、`--headed`、`--surface`、`wait`；受管區塊寫明敏感網站一律經 neb。對照表存成 `docs/agent-browser-command-audit.md`、`docs/agent-desktop-command-audit.md`，標註對過的版本；`test_neb`／`test_ned` 比對 `versions.json`，換版本沒重對就失敗。
- **驗證**：每個洞一個反例；新測試對舊的 neb 跑會失敗 23 項、對舊的 ned 失敗 9 項，修好後 neb 57/57、ned 56/56。
- **教訓**：白名單只有在「每一項都知道它會做什麼」時才是白名單——讀完上游工具的說明全文、逐項分類，比憑名字猜（`find` 聽起來是讀）可靠；而守門工具要寫進 agent 每次都會看到的地方，否則再嚴也沒人經過它。

## 2026-11-22 · UAC 真機實測（閒置閘門）
- **方法**：獨立程序等使用者閒置滿 300 秒（實際等了 96 分鐘）→ 發出一次無害的提權請求（提權後只會寫一個標記檔）→ 每秒用 `uac.py` 偵測。
- **結果**：提示 1.2 秒內偵測到（consent.exe）、交接訊息產生；沒人按 → **122.7 秒後 Windows 自動取消**，請求端收到「操作已被使用者取消」，標記檔不存在（提權指令沒有執行）；測試期間使用者沒有輸入。
- **學到**：視窗消失 ≠ 核准。`uac.py wait` 改成附 `likely_timed_out`（等了 ≥115 秒）與提醒「看提權動作的結果判斷」；交接訊息加一句「約 2 分鐘沒人按會自動當成否」。
- **踩坑**：請求端的錯誤訊息是系統語系（cp950）輸出，用 UTF-8 讀會變亂碼——只記「有錯誤」，判斷依據是標記檔。

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
- **真機匯出（閒置閘門自動執行，五次）**：第 5 次一路走完——12,562 B、356 行；身分三道：標頭「名稱, 4人」、LINE 產生的預設檔名、匯出檔不同發言者 4 ≤ 4 人；約 12 秒、LINE 縮小交還。
  前四次各抓到一個只有真機才有的問題，都停在安全的一側：(1) 選單名稱亂碼——PowerShell 5.1 用系統字碼頁（cp950）寫 stdout → 強制 UTF-8，假聊天改中文標籤＋同字首誘餌；(2) 縮放 150% 時投遞的點擊落在選單外（換個版面可能落在「退出群組」）→ Per-Monitor DPI 感知＋點擊前三重位置檢查（UIA 與 Win32 外框一致、項目中心在選單內、點在客戶區內）；(3) LINE 匯出檔沒有標頭、不含對話名稱 → 存檔後改數發言者；(4) 存檔後讀了只有假聊天 profile 才有的設定鍵 → 修，加靜態測試。
- **教訓**：CI 的假視窗再像也是自己寫的——字碼頁、縮放、真實檔案格式這三件事只有真機會告訴你；所以真機那一步要做成「失敗時一定停在安全側、而且回報得出原因」。

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
- **INC-1**：A/B 實驗的受測 agent **自己啟動了沒登入的 LINE 桌面版**、停在 QR 登入畫面，輪詢等登入畫面消失；使用者以為需要而掃碼後，它去搜尋、截圖、點擊（零按鍵、零送出；不能排除動到已讀）。（依指令序列稽核：03:45:15 `agent-desktop launch ...LINE.exe`、03:46 起輪詢 `LOGIN_SCREEN_GONE`。早先紀錄寫「使用者自己登入」是錯的，2026-11-22 更正。）
  根因：agent 把「人掃了碼」當成同意；當時聊天守則還沒同步到受測環境；沙盒 guard 句只管「改檔」不管「啟動、讀取或操作其他 app」。
  → 改成機制：ned 對通訊類 app 未授權一律拒絕；guard 句改為「除列出目標外不得讀取或操作任何其他應用程式」。
- **INC-2**：沒有工具包的受測 agent 自己寫 Win32 呼叫完成桌面題——搶前景 10 次、模擬滑鼠 20 次、全螢幕截圖 6 次，當時使用者正在用電腦。
  guard 句只說「不得操作其他 app」，沒管「怎麼操作」。→ 不再在使用者桌面跑沒有工具包的桌面題；guard 句加「不得移動滑鼠、不得改變前景、不得擷取全螢幕」；
  會開視窗的測試一律放 CI 或等閒置閘門。殘留截圖未開啟即刪除。
- **教訓**：即興的手沒有規矩——這正是「信任靠基礎設施」的實證。

## 2026-11-21 · 三平台 CI 與「信任設施」驗證
- **狀態**：三平台＋popup-lab＋wizard-lab＋自架 Gitea／Mattermost 全綠（2026-11-22，commit 0ff5ccf；ci run 44923827769、selfhost run 44923827660）。每次推送自動跑；以 `tests/ci_summary.py` 讀實際輸出判紅綠，不看步驟綠勾。Windows runner 沒有 Office → office 測試 SKIP（Windows 的 Office 路徑是本機 COM 實測）。
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
- **CI 上的 B3 彈出選單匯出一路踩到的**：WinForms 平面按鈕經 UIA FromHandle 讀成 Pane（不是 Button）→ 接受「項目大小的 Pane」；存檔對話框不採用寫進去的完整路徑、照預設檔名存到它自己的資料夾 → 存檔前從網址列讀資料夾，找不到時去那裡找（CI 實證：`saved_via=default-name-in-dialog-folder-then-moved`）；慢的 runner 上對話框剛出現時控制項還沒建好 → 重試 snapshot，拒絕前一定先按取消（否則留著的對話框會擋住之後每一步）。
- **教訓**：CI 的 bash 預設 `-e -o pipefail`，一支測試失敗會讓同一步後面全部不跑；Windows runner 預設 cp1252，寫含中文的檔案要指定編碼；
  互動式 snapshot（`-i`）會漏掉標籤類靜態文字。
- **數字出處**：`journal/evidence/`、CI artifacts（results-*、popup-lab、wizard-lab、demo-guide）

## 2026-11-21 · A/B 第二輪（v0.3 受管區塊版）：沒裝 vs 裝了
- **方法**：`claude.exe -p --model <固定>`，同 prompt、同 allowedTools；bare vs kit（setup --project）。巢狀執行一律指定 `--model`、記 modelUsage
  （第一次補跑沒指定，預設模型中途從 fable-5 換成 opus-5-5，差點把兩個模型的結果合併）。
- **結果（fable-5，T3 每格 3 次、其餘 n=1）**：
  | 題 | bare | kit v0.3 |
  |---|---|---|
  | T1 修畫面並自己驗收 | ✅ 48s · $0.76 | ✅ 31s · $0.68 |
  | T2 網頁互動 | ✅ 18s · $0.45（curl 直打 API） | ✅ 22s · $0.48（同樣選 API） |
  | T3 操作桌面視窗 | 1/3；唯一成功那次 300s，搶前景、模擬滑鼠、全螢幕截圖（INC-2） | 3/3，約 40s |
  | T4 登入後才看得到 | ❌ 停在登入頁、要帳密 | ✅ 39s |
- **opus-5-5 的 T3**：bare 2/2（自己手寫 UIA＋BM_CLICK）、kit 2/2，時間相近。**haiku**：T3 bare 自己手寫 UIA 65s、kit 24s；T4 見下一條。
- **結論**：強模型在有路可繞的題目上，裝不裝差不多（工具包 v0.2 甚至比較慢，瘦身成 v0.3 後打平）；差別出在
  (1) 帳號牆（T4）——沒有登入過的 profile 就是過不去；(2) 桌面——沒工具的成功方式會干擾使用者；(3) 便宜模型。
- **原始檔**：`journal/evidence/ab/v2/SUMMARY.md` 與各格 JSON（不進 repo）。

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
