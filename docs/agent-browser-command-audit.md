# agent-browser 指令與選項對照（neb 的放行清單依據）

audited version: 0.43.0

升級 agent-browser 時：用新版本的 `agent-browser --help` 全文逐項重對這張表，更新 `scripts/neb.py` 的
`SENSITIVE_ALLOWED`／`CONTROL_CMDS`／`BAD_OPTS`／`FIND_ACTIONS`，在 `tests/test_neb.py` 為新增的危險項加反例，
最後把上面的 `audited version` 改成新版本。`tests/test_neb.py` 會比對這一行和 `versions.json`，不一致就失敗。

判斷原則：凡是能**執行 JavaScript、操作元素、注入腳本或標頭、改身分、接管或換掉瀏覽器、繞過逐步檢查**的，
在敏感網域一律不放行；繞過逐步檢查的控制面指令與危險選項在**任何網站**都拒。

## 子指令

| 子指令 | 會做什麼 | 敏感網域（已授權） | 一般網站 |
|---|---|---|---|
| `snapshot` `get`（`cdp-url` 除外）`is` `screenshot` `pdf` `console` `errors` `session` | 只讀 | 放行 | 放行 |
| `scroll` `scrollintoview` `back` `forward` `reload` `close` | 不改頁面內容的導覽與捲動 | 放行 | 放行 |
| `wait <sel\|ms>`、`wait --load/--url/--text` | 等待 | 放行 | 放行 |
| `wait --fn <js>` | **在頁面上執行 JS**（等同 eval） | 拒 | 放行 |
| `find <locator> <value> text` | 讀文字 | 放行 | 放行 |
| `find … click/fill/type/check/…`、`find` 不寫動作 | **操作元素；預設動作是 click** | 拒（請用 snapshot＋ref） | 放行 |
| `click` `dblclick` `hover` `tap` | 點擊 | 只接受 neb snapshot 的 ref，且名稱不像送出/回覆/發佈 | 放行 |
| `open` `goto` `navigate` `read [url]` `tab new <url>` | 導覽到網址 | 每個網址都檢查：只接受 http(s) 與 about:blank；目標若是敏感網域要已授權 | 同左（scheme 檢查） |
| `tab list/close/<n>` | 切換分頁 | 放行（下一個指令會重新問目前網址） | 放行 |
| `diff snapshot` `diff screenshot` | 比較 | 放行 | 放行 |
| `diff url <u1> <u2>` | 開兩個網址 | 拒 | 放行（scheme 檢查） |
| `fill` `type` `press` `keyboard` `select` `check` `uncheck` `drag` `upload` `focus` `mouse` `clipboard` | 輸入與操作 | 拒 | 放行 |
| `eval` `pushstate` `vitals` `a11y` `highlight` `react` | **執行或注入 JS** | 拒 | 放行 |
| `download` | 點元素觸發下載 | 拒 | 放行 |
| `set headers/credentials/offline/geo/…` `cookies` `storage` `network route/har` | 改請求、存取 cookie／儲存、攔截流量 | 拒 | 放行 |
| `record` `trace` `profiler` | 錄製頁面與流量 | 拒 | 放行 |
| `batch "cmd" …` | 連續執行多個指令（中途可換頁） | **拆開逐一經 neb 檢查**；從 stdin 讀時拒 | 同左 |
| `chat` `mcp` `webmcp` `flow` `plugin` `connect` `auth` `confirm` `deny` `stream` `dashboard` `inspect` `removeinitscript` `upgrade` | AI 代理、MCP、錄製重播、外掛、直接 CDP、自動填帳密、確認待決動作 | **拒** | **拒** |
| `get cdp-url` | 交出瀏覽器的直接控制權 | **拒** | **拒** |
| `install` `doctor` `profiles` `skills` | 安裝與說明 | 不經 neb（直接用 agent-browser） | 同左 |

## 全域選項（出現在指令列任何位置都檢查）

| 選項 | 為什麼危險 | neb |
|---|---|---|
| `--init-script` `--enable` `--extension` | 在每個頁面注入腳本／載入擴充 | 拒 |
| `--headers` `--cdp-headers` | 對站台送任意標頭（例如帶別人的 token） | 拒 |
| `--user-agent` | 改身分；會騙過發件匣「自動化瀏覽器不能核准」的檢查 | 拒 |
| `--cdp` `--auto-connect` `--executable-path` `--provider`/`-p` `--device` `--engine` `--args` | 接管別的瀏覽器、換掉瀏覽器、改啟動參數 | 拒 |
| `--state` `--restore*` `--session-name` | 載入或替換登入狀態；`--restore-check-fn` 會執行 JS | 拒 |
| `--proxy` `--proxy-bypass` `--ca-cert` `--no-ca-cert` `--ignore-https-errors` | 改網路路徑與信任 | 拒 |
| `--allow-file-access` | 讓頁面讀本機檔案 | 拒 |
| `--action-policy` `--confirm-actions` `--confirm-interactive` `--config` `--capture` `--namespace` `--model` | 改掉工具自己的政策、設定或隔離 | 拒 |
| `--session` `--profile` `--headed` `--json` `--annotate` `--max-output` `--content-boundaries` `--screenshot-*` `--hide-scrollbars` `--color-scheme` `--idle-timeout` `--pin-tab` `--download-path` `--debug` | 不改變能做的事 | 放行 |

## 已知限制

- neb 只守**它被呼叫的那一次**。agent 若直接呼叫 agent-browser（不經 neb），這些檢查都不生效；受管區塊的「看網頁」指令就是直接呼叫，
  所以敏感網站的唯讀是靠「敏感網站一律經 neb」這條規則＋權限規則，不是靠瀏覽器本身。
- 一般網站上的 `eval` 仍放行；頁面上的腳本理論上能對已登入的敏感網站發跨站請求。這擋的是太熱心的 agent，不是惡意程式。
