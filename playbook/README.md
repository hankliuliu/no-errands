# playbook：走通過的路

agent 走通一條路，就寫一頁放這裡，下一個人的 agent 直接照走。動手前先查。

## 格式（四段）
1. **走得通的路**：一步一步，指令可以照抄；元素用 automation id／角色＋名稱，不用顯示座標。
2. **走不通的路與原因**：省下一個人的試錯。
3. **驗收方式**：怎麼確認真的成功（不是「每一步都回 ok」）。
4. **交人點**：真的只有人能做的那一步，以及怎麼交。

開頭寫狀態：在哪個平台、哪個版本實測過；沒實測的寫「未實測」。含私人資訊（帳號、群組名、內部網址）的放 `~/.no-errands/playbook-local/`，不要放這裡。

## 現有
- `apps/line-desktop.md`：LINE 桌面版找到指名對話、確認身分、匯出
- `apps/chrome-windows-uia.md`：在 Chrome 設定頁用 UIA 切換設定
- `apps/mattermost.md`：Mattermost 唯讀查詢、經發件匣發文
- `tasks/linux-no-sudo-browser-libs.md`：Linux 沒有 sudo 時補瀏覽器系統庫
- `tasks/second-machine-via-wsl.md`：把 WSL 當第二台機器
- `tasks/office-recalc-and-convert.md`：xlsx 重算、舊版 Office 轉 PDF
- `tasks/token-via-web-ui.md`：網頁介面產生 token 並寫進設定檔
- `tasks/uac-handoff.md`：系統權限提示的交接
- `tasks/gui-installer-wizard.md`：GUI 安裝精靈

## 通用坑
- 偵測工具要真的執行（`python --version`），不能只看在不在 PATH（Windows 的 Microsoft Store 假捷徑）。
- `find --name` 是子字串比對；每一步回 ok 不等於答案對。
- 只操作自己開的視窗；收尾用 PID 或該視窗自己的關閉鈕，不按程序名關。
- 彈出選單常是獨立頂層視窗，agent-desktop 的視窗清單看不到 → 用 `popup_menu.py` 依 HWND 讀。
- 在點擊處理函式裡開模態對話框的 app，UIA 會卡住 → 停手回報，不要改用鍵盤。
- 系統檔案對話框的檔名欄要寫內層 Edit，寫完讀回；出現覆寫確認一律按「否」。
- 截圖即個資：截單一視窗，不截全螢幕。
- agent-browser `batch` 會吃掉反斜線與引號：路徑用正斜線。
