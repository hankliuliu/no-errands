# no-errands（不求人）— 給在這個 repo 裡開工的 agent

## 使用者說「安裝不求人」時

1. **先用一句話說要改什麼**，等他說好：
   「我會在 `~/.no-errands/` 放工具與設定、在你的 `~/.claude/CLAUDE.md` 加一段受管區塊、裝一個 skill；
   你原本的內容一個字都不動，可以完整移除。只想在一個專案試用的話我改用 `--project`。」
2. 安裝（Windows 用 `powershell -ExecutionPolicy Bypass -File setup.ps1 --user`；macOS/Linux 用 `sh setup.sh --user`）。
   輸出是 JSON，每一步有 `ok`；失敗的步驟附 `recovery`，照它做，不要叫使用者自己查。
3. **自我檢查的桌面項會開一個測試小視窗**：先問「現在方便讓一個測試視窗跳出來幾秒嗎？」
   方便 → `python doctor.py`；不方便 → `python doctor.py --skip-desktop`，並說之後怎麼補跑。
4. 把 doctor 的結果整理成一張小表給他看（每一項：可用／不可用＋原因）。
5. **示範要用到畫面**：問他要不要現在看 60 秒示範（開測試頁截圖描述、操作測試面板讀回）。不要就跳過，告訴他之後說「示範一下」即可。
6. 告訴他：規則用講的就能改（例：「我授權你用 Mattermost」「要跳到前景的事先問我」）；發件匣網址；
   移除方式 `python setup.py --uninstall --user`（`~/.no-errands/` 會留著，要不要刪他決定）。

## 維護規約（改這個 repo 時）

- **`.ps1` 只用 ASCII。** 為什麼：PowerShell 5.1 把沒有 BOM 的 UTF-8 當 ANSI 讀，中文註解會讓 parser 壞掉（M1 踩過）。
- **不用 symlink。** 為什麼：Windows clone 後變成內含路徑的純文字檔（實驗室另一個 repo 的 Codex 支援就是這樣壞掉沒人發現）。
- **讀寫使用者的檔案要逐位元組保留。** `CLAUDE.md` 受管區塊以外、`settings.json` 我們以外的設定、同名 skill——都有測試（`tests/test_coexist.py`）。曾經把使用者最後一行的行尾空白吃掉、把同名 skill 直接覆蓋。
- **會開視窗、可能搶前景的測試不要在使用者的電腦上跑，放到 CI。** 真的要本機跑，先問他現在方便嗎，或用閒置閘門（`scripts/idle.py`）。
- **只操作自己開的視窗；收尾用 PID 或該視窗自己的關閉鈕，不按程序名關。** 為什麼：`close-app`／`taskkill /IM` 會關掉使用者自己開的同名程式。
- **機密值不輸出**（token、cookie、密碼）：驗證用雜湊或布林。
- **工具版本與 sha256 釘在 `versions.json`**；更新流程寫在那個檔案的註解。
- **每個檢查都要留下結果**（成功或失敗），失敗靜默等於誤報全綠。CI 的紅綠以 `tests/ci_summary.py` 讀實際輸出為準，不看步驟綠勾。
- **巢狀測試一律明確指定 `--model`**，結果表記實際的 modelUsage。為什麼：預設模型會在一天內換掉，差點把兩個模型的結果合併。
