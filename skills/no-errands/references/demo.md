# 示範劇本（live demo）

讓 agent 照劇本操作一個網頁系統（例：架在 Mattermost 上的內部系統）做 live demo，教別人怎麼用；每次執行留下紀錄，下一次更穩。引擎是 `scripts/demo_runbook.py`。

## 三種模式

| 模式 | 做什麼 | 會不會開視窗 |
|---|---|---|
| `preflight` | 跑劇本裡的檢查指令（服務、模型、資料狀態）、確認每個示範帳號的 profile 登入有效且身分正確、核准是否有效 | 不會（無頭） |
| `rehearse` | 對重置後的資料完整跑一遍、量時間、每步截圖；成功的步驟更新 Plan B 截圖 | 不會（無頭） |
| `live` | 看得到（`--headed`）、只走劇本；遇到 `advance: cue` 的步驟停下、存檔並結束，講者示意後 `--resume` 接著跑；某步失敗先走劇本裡的退路，再不行就停下並給出 Plan B 截圖 | 會：只在使用者說可以時執行 |

## 送出要先核准：整份劇本一次核准

1. `demo_runbook.py request-approval <劇本>`：把劇本裡所有會送出的動作整理成送出清單，起草到發件匣（`service=runbook`）。
2. 使用者在發件匣頁面看到一張表：每一步由哪個示範帳號、**在哪個網址路徑**、對哪個欄位、輸入什麼（或變數的確切格式）、按什麼。選可執行次數（預設 3），按「核准整份劇本」。agent 不能操作這一頁。
3. 執行時每個送出動作都帶 `--runbook-approval <id> --step <n>` 經過 neb。neb 只放行：核准有效（未到期、未撤銷、清單雜湊相符）、主機是登記為示範用途的主機、profile 是登記的示範帳號且是該步核准的帳號、動作在該步清單內且在核准的網址路徑上、文字逐字相符（變數值另有硬上限：不含空白與換行、最長 64 字）、按鍵只在焦點欄位與其內容都等於核准的輸入時、次數未超過上限。任何不同 → 拒絕並要求重新核准。
4. 劇本的送出內容改了（新的欄位名、文字）→ 引擎拒絕執行，要重新核准；只改等待預算、選取方式的順序、講者台詞 → 不用。

這條例外**只**對使用者登記為示範用途的主機有效（`policy.py domain add <host[:port]> <app> --demo-accounts a,b`）。內建的敏感 app（聊天、郵件、真實 Mattermost…）帶了核准也一律只讀。主機比對用主機名＋埠，`localhost`／`127.x`／`[::1]`／`0.0.0.0` 視為同一台。**示範主機一定要登記**：沒登記的 localhost 會被當成一般網站。

## 示範帳號

- 每個示範帳號一個專用瀏覽器 profile，資料夾名＝帳號名。登入一次，之後示範不打密碼。
- 要不要讓 agent 代為登入由使用者決定，記在政策檔（`--agent-login <帳號>`）。`demo_runbook.py login --account <帳號> --secret-cmd "<印出密碼的指令>"` 由 neb 的 login 規則填入：只在登記主機的登入頁、只填頁面上唯一的帳號欄與 password 型別的欄位，密碼經 stdin 交給 agent-browser（不出現在命令列參數），不輸出、不寫檔。
- 兩道人工關卡需要不同人時，劇本裡每一步寫明帳號；引擎每個帳號一個 session。

## 留紀錄讓下次更穩

- 每次執行一個資料夾（`run.json`＋`shots/`）：每步耗時、實際用到的選取方式（哪個範圍、哪個名稱、幾個同名）、斷言結果、自動重試與退路經過。紀錄只含結構；錯誤只留 neb 的拒絕訊息或回傳碼，不含輸入文字與頁面內容。截圖留本機。
- `learn`：讀最近幾次紀錄，提出劇本修改的差異清單——等待預算（觀測最大值 ×1.5＋10 秒）、選取方式的優先順序、新的已知問題。**不自動套用**；使用者同意後 `apply … --reason "<原話>"`。重要示範前 `freeze`，凍結時不能套用。
- 政策：`demo.persist off` → 一次性（紀錄寫暫存、結束即刪，不更新 Plan B、不提出修改）；`demo.learn off` → 只留紀錄不提修改。
- 劇本與紀錄放使用者本機（例：`~/.no-errands/runbooks/<名稱>/`），不放進任何 git 工作目錄；`validate` 會警告。

## 斷言要對到「本次」產生的內容

聊天、通知這種頁面會累積歷史；而且資料重置通常清不掉聊天平台裡的訊息。所以「等某段文字出現」很容易比對到以前留下的同一段字——實測：排練 2 的「新的知識更新提案」2.6 秒就過，比對到的是舊通知。規則：

- 送出前放一個 `mark`（記下那段字目前出現幾次），等待時用 `wait_text` 加 `new_only: true`（次數要比 mark 時多）；或
- 等的文字帶本次才有的識別（例如 `{case_id}`）；或
- 頁面固定內容（頻道名稱、對話框標題）標 `static: true`，或把範圍限定在對話框（`[role=dialog]`）。

`validate` 會對不符合的 `wait_text` 給警告。`learn` 的紀錄也要照這個看：一步「秒過」但那段字本來就在頁面上，不算真的通過。

## 讓正式示範少停頓

就地重試（第一下失敗、等 2 秒再試）在正式示範時會讓畫面停住。`learn` 看到「重試才成功」的動作會提議 `settle_s`：先等目標出現最多幾秒再操作，不用重試。

## 重置示範資料

示範會改動目標系統的資料。劇本的 `reset.commands` 只在政策檔有使用者對**這組指令雜湊**的同意時才執行（`policy.py set demo.reset_consent <sha256> --reason "<原話>"`）；指令改了要重新同意。

## 劇本裡有 shell 指令

`preflight` 與 `reset` 是在本機執行的 shell 指令。`validate` 會全部列出。只跑自己寫的或看過的劇本。

## 劇本格式（摘要）

```json
{
 "title": "…", "target": {"base_url": "http://localhost:8065", "host": "localhost:8065",
                         "home_path": "/team/channels/town-square", "whoami_path": "/api/v4/users/me"},
 "accounts": {"demo_a": {"profile": "profiles/demo_a"}},
 "vars": {"case_id": {"pattern": "C-[0-9a-f]{8}"}},
 "steps": [{"id": "s1", "account": "demo_a", "advance": "auto|cue", "say": "給講者的一句話",
            "actions": [{"do": "open", "url": "/team/messages/@bot"},
                        {"do": "fill", "target": {"role": "textbox", "names": ["…"], "scopes": ["[role=dialog]", null]}, "text": "…"},
                        {"do": "press", "key": "Enter"},
                        {"do": "wait_text", "text": "…", "budget_s": 60},
                        {"do": "capture", "var": "case_id", "regex": "C-[0-9a-f]{8}"}],
            "fallback": […], "resume_at": 3, "on_fail": "講者的退路"}]
}
```

動作：`open` `fill` `press` `click` `check` `wait_text` `expect_absent` `capture` `snapshot_ok` `scroll` `pause`。目標用無障礙樹的角色＋名稱（名稱完全相同；同名多個時取最新的一個），可列多個名稱與多個範圍當備案。

## 已知限制

- 防線擋的是「太熱心」的 agent，不是惡意程式；能在這台電腦任意執行程式的東西本來就能做任何事。
- neb 由頁面讀焦點欄位的名稱（aria-label／placeholder／label），跟無障礙樹的名稱不一致的網站，按鍵會被擋下——寧可擋。
- 示範在目標系統留下的訊息（例如通知）不一定能隨資料重置清掉；劇本應只看最新的一則。
