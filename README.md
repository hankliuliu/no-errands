# no-errands 不求人

讓你的 AI agent 自己動手，不再叫你跑腿。

你一定遇過：agent 改完東西要你「截圖給我看」、要你「手動打開設定頁點一下」、
裝個工具要你全程按「下一步」。我們掃了一台機器 25 個 Claude Code session：
17 個裡 agent 總共使喚了人 28 次。

no-errands 給 agent 一套「能力階梯」：先自己試（CLI → 看圖 → 瀏覽器 →
已登入 profile → 桌面 a11y），真的只有人能做的事（密碼、UAC、實體操作、決定）
才來找你——而且一次問完、精確、你做完它自己偵測得到，不用回報。

## 安裝（M1 預覽版）

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File setup.ps1 --project <你的專案目錄>
```
```sh
# Linux / macOS
sh setup.sh --project <你的專案目錄>
```

跑完應該看到：JSON 輸出每步 ok、`~/.no-errands/bin/` 出現 agent-desktop 與
agent-browser（GitHub Releases 下載、sha256 核對）、專案 CLAUDE.md 多了一個
no-errands 區塊、`.claude/skills/no-errands/` 出現。

然後跑 doctor（每一階做真的 smoke test，約 30–60 秒）：

```
python doctor.py --home ~/.no-errands
```

跑完應該看到：各項 ✅ 與 `~/.no-errands/capabilities.md`（這台機器的實測能力圖）。

解除安裝：`python setup.py --uninstall --project <目錄>`。

## 狀態

M1 垂直切片：Windows 已驗證；Linux/macOS 的 doctor 自驗與更多情境在路上。
設計文件與證據：見專案 journal。
