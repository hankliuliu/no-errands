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
