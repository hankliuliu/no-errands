---
target: 用 agent-desktop（UIA）操作 Windows 上的 Chrome 視窗（含網頁表單）
platforms: [windows]
rung: 5
verified: 2026-11-21（Chrome 157 + agent-desktop 1.3.1）
---

# Chrome 視窗的 UIA 操作

## 走得通的路
- 網頁表單（input/button）會出現在 UIA 樹：`snapshot --window-id <w> -i --compact`
  → textfield/button 帶網頁的 id/aria-label → `set-value` / `click` 都可用。
- **原生啟動的 Chrome 要 snapshot 兩次**：renderer accessibility 懶啟動，
  第一次只有瀏覽器框架（網址列、分頁鈕），第二次才出現網頁內容。
  由 agent-browser --headed 啟動的視窗一次就有（自動化通道已啟用 a11y）。

## 走不通的路與原因
- `close-app chrome.exe`：**會關掉使用者自己開的所有 Chrome**（同程序名）。
  關單一視窗 → 在該視窗的 snapshot 裡點它自己的「關閉」button。
- 殺程序用 `taskkill /IM chrome.exe` 同理災難：一律用命令列指紋
  （`--user-data-dir=<專屬路徑>`）過濾 PID。

## 驗收方式
操作後 `get <ref> --property value` 或看視窗標題變化（登入成功 → title 變目標頁）。

## 交人點
密碼/2FA 輸入（照 keys.md 兩種交接模式）。
