# 手：桌面 GUI（agent-desktop）

單檔原生 binary；路徑見受管區塊（下稱 `ad`）。
區塊裡的指令不夠用時，再查官方指南：`ad skills get desktop` / `ad skills get platform`。

## 基本迴圈

```
ad list-apps / ad list-windows          # 找目標（用真實程序名：UWP 如 CalculatorApp.exe）
ad snapshot --app <App.exe> -i --compact  # a11y 樹 + ref
ad click '@<snap>:eN' / ad set-value '@..:eN' '文字'
ad get '@..:eN' --property value        # 讀回驗收
ad screenshot out.png --app <App.exe>   # 視覺驗收
ad close-app <App.exe>                  # 收尾
```

## 坑（實測）

- `find --name` 是**子字串**比對：「加」會先配到「在記憶中加入」(M+)。要嘛拿完整
  snapshot 自己挑精確名稱，要嘛認 `native_id`（automation_id）。playbook 一律記
  automation_id——a11y 顯示名稱跟系統語言走。
- headless `type` 可能 POLICY_DENIED（語意插入不可用）→ 改 `set-value`，或 `--headed`。
- `launch <跳板程序>`（如 notepad）會等滿 30 秒：加 `--timeout 5000`，就緒交給 snapshot 判斷。
- UWP 的 `close-app` 可能回 False 但實際已關（ApplicationFrameHost 代管）；以 list-apps 驗證。
- PowerShell 下 ref 一定單引號（`@` 是 splatting）。
- 多視窗 AMBIGUOUS_TARGET → 用 `--window-id`（從 list-windows 拿）。

## 安全

- 預設 headless 語意操作（不動游標、不搶焦點）；`--headed` 僅在必要時。
- **只操作與任務相關的視窗**；絕不碰使用者開著的編輯器/筆記（新版記事本是多分頁共用
  程序，snapshot 第一個文字框可能是使用者未存檔的筆記——要操作就自己 launch 新實例
  或用 --window-id 鎖定自己開的視窗）。
- 收尾只關自己開的東西；殺程序用 PID/參數指紋。

## 會不會打擾使用者：實測表（Windows 11，2026-11-21，`tests/n4_noninterference.py`）

量法：全程每 50ms 記錄前景視窗 handle 與游標位置（不記視窗標題）。視窗 A 以 `WM_CHAR` 持續收字
（每 100ms 一字、共 200 字），同時對視窗 B（假聊天）反覆 locate/open/read/截圖/匯出/close。

| 動作 | 改變前景 | 移動游標 | 備註 |
|---|---|---|---|
| `snapshot` / `click`（語意 press）/ `get` / `read` | 否 | 否 | 視窗 A 收到 200/200 字，完全一致 |
| `screenshot --window-id` | 否 | 否 | |
| chat_guard `export`（含系統另存新檔對話框） | 否 | 否 | 目標視窗未被啟用時，對話框也不會取得前景（假視窗實測；真實 app 待驗） |
| `launch calc`（從背景程序啟動新 app） | 否 | 否 | Windows 前景鎖：新視窗不會搶走使用者正在用的視窗 |
| 關閉自己開的視窗（點該視窗的關閉鈕） | 否 | 否 | 不要用 `close-app` 按程序名關：會波及使用者自己開的同名 app |
| agent-browser headless open/snapshot/click/screenshot/close | 否 | 否 | |
| 發件匣 `draft`（背景模式，不開頁面） | 否 | 否 | 觀看模式才會用預設瀏覽器開頁面（預期會搶焦點） |
| `--headed` 系列、`focus` / `focus-window` / `hover` / `drag` / `mouse-*` | **是** | **是** | `ned` 在背景模式直接拒絕 focus/type/hover/drag |

限制：這次量測期間前景從頭到尾沒變過，**沒有跑「刻意搶焦點」的正對照**，所以量測工具的
靈敏度是以 Win32 API 的行為推定，不是實測。視窗 A 的輸入是直接投遞到它的文字框，
不是真的使用者打字（為了不在使用者用電腦時搶走他的前景）。

**即興手段才是干擾來源**：沒有工具時，受測 agent 會自己寫 `SetForegroundWindow`、
`SetCursorPos`/`mouse_event`/`SendInput` 與全螢幕截圖（INC-2）。用 `ned`/chat_guard 的語意操作就不需要這些。

### 坑：在點擊處理函式裡開模態對話框的 app
WinForms 這類 app 若在按鈕/選單的 click 處理函式裡直接 `ShowDialog()`，UIA 的 press 要等對話框
關掉才返回（→ TIMEOUT），而且在那之前**對該程序的所有 UIA 查詢都會卡住**（連對話框本身也
snapshot 不到）。chat_guard 遇到會停手回報，不改用鍵盤。假視窗用 `-SyncDialog` 可重現。
