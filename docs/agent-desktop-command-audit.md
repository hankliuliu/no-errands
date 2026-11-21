# agent-desktop 指令與選項對照（ned 的放行清單依據）

audited version: 1.3.1

升級 agent-desktop 時：用新版本的 `agent-desktop --help` 與各子指令 `--help` 逐項重對，更新 `scripts/ned.py` 的
`REF_CMDS`／`APP_CMDS`／`PASS_CMDS`／`OBSERVE_CMDS`／`FOCUS_CMDS`／`APP_SURFACES`，在 `tests/test_ned.py` 為新增的危險項加反例，
最後把上面的 `audited version` 改成新版本。`tests/test_ned.py` 會比對這一行和 `versions.json`，不一致就失敗。

ned 是**白名單**：不在下表「放行」的子指令一律以「未知或未允許的子指令」拒絕。

## 子指令

| 子指令 | 會做什麼 | 一般 app | 已授權的敏感 app（聊天/郵件/金融） |
|---|---|---|---|
| `snapshot` `find` `screenshot` `list-windows` | 觀察（自動綁 `--app`） | 放行 | 放行（未讀保護開時聊天 app 拒，走 chat_guard） |
| `get` `is` | 讀 ref 的屬性／狀態（ref 必須是 ned 為同一個 app 拍的） | 放行 | 放行 |
| `click` `set-value` `clear` `select` `toggle` `check` `uncheck` `expand` `collapse` `scroll` `scroll-to` `right-click` | 語意操作（ref 歸屬檢查） | 放行 | 拒（走 chat_guard） |
| `type` `focus` `hover` `drag` | 會搶焦點或動滑鼠鍵盤 | 背景模式拒；觀看模式放行 | 拒 |
| `double-click` `triple-click` | 需要 `--headed`（實體輸入） | 背景模式拒（`--headed` 被拒） | 拒 |
| `launch` | 啟動程式 | **目標必須等於宣告的 app**（事故一：曾借測試名義啟動 LINE） | 拒 |
| `close-app` | 關程式 | 目標必須等於宣告的 app | 拒 |
| `wait` | 等時間／元素／文字／視窗 | 放行；`--element` 的 ref 要屬於該 app；`--text`/`-w`/`--menu` 自動綁 `--app`；`--notification` 拒 | 拒 |
| `status` `version` `skills` | 說明 | 放行 | 放行 |
| `press` `key-down` `key-up` `mouse-*` | 鍵盤滑鼠注入 | 拒（不在白名單） | 拒 |
| `focus-window` `minimize` `maximize` `restore` `resize-window` `move-window` | 視窗管理 | 拒（chat_guard 內部用；收尾交還前景另有流程） | 拒 |
| `list-notifications` `notification-action` `dismiss-*` | **讀所有 app 的通知、按通知上的按鈕（含「回覆」）** | 拒 | 拒 |
| `clipboard-get` `clipboard-set` `clipboard-clear` | 讀寫剪貼簿（使用者的私人內容） | 拒 | 拒 |
| `batch` | 執行一串 JSON 指令（內容不經 ned） | 拒 | 拒 |
| `mcp` `sandbox`（含 `exec`） `session` `trace` `cursor-overlay` | 繞過逐步檢查、執行程式、讀追蹤紀錄 | 拒 | 拒 |
| `list-apps` `list-displays` `list-surfaces` `open-system-surface` `permissions` | 系統層 | 拒 | 拒 |

## 選項

| 選項 | 為什麼要管 | ned |
|---|---|---|
| `--headed` | 允許實體鍵盤滑鼠與搶焦點 | 背景模式（預設）拒 |
| `--surface <s>` | `desktop` `taskbar` `notification-center` `start-menu` `focused` … 會讀到**別的 app** 的內容 | 只允許 `window` `menu` `menubar` `sheet` `popover` `alert` `toolbar` |
| `--app` | 範圍 | 觀察類自動補上；呼叫者給的必須等於宣告的 app |
| `--screenshot <PATH.html>` `--debug` `--trace` | 寫本機檔案 | 放行（只寫呼叫者指定的新檔） |
