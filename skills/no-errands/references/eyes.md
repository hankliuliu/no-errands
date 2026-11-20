# 眼：自己看成果（驗收迴圈）

「完成」= 你親眼看過成果，不是「我改好了請你看看」。工具路徑見 CLAUDE.md 受管區塊。

## 各類成果怎麼看

| 成果 | 看法 |
|---|---|
| 圖片 / PDF | 直接 Read（多模態）。PDF 要看版面就 Read；要 grep 文字再抽取 |
| 網頁 | `agent-browser screenshot out.png` → Read；結構用 `snapshot -i -c`；錯誤用 `console`/`errors` |
| 桌面視窗 | `agent-desktop screenshot out.png --app <App.exe>`（原生 PNG） |
| 全桌面（Windows） | PowerShell + System.Drawing（CopyFromScreen）。注意 DPI 縮放時座標≠像素 |
| 全桌面（macOS / Linux） | `screencapture` / `gnome-screenshot`、`grim`(Wayland)、`import`(X11) |
| 投影片 / Office | 轉 PDF 再 Read（有 M365 用 COM；否則 `soffice --headless --convert-to pdf`） |
| 圖表程式碼（matplotlib 等） | 存成 PNG 後 Read——豆腐字、label 重疊、空圖都要親眼確認 |

## 規則

1. 每個「操作」後用**獨立手段**驗證結果：語意層回 ok ≠ 任務正確
   （實測：find 子字串誤配按到 M+，五步全 ok、答案錯）。
2. 截圖驗收完描述你看到了什麼（一句話），再下結論。
3. 截圖即個資：留本機、用完即刪、分享前去識別化
   （實測：第一張全桌面截圖就拍到使用者本名與 email）。
4. 比對型驗收（改版面前後）：`agent-browser diff screenshot --baseline`。
