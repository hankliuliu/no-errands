# 示範劇本（demos）

這裡**不放**任何真實系統的劇本。真實劇本含主機、帳號與流程細節，放使用者本機（例：`~/.no-errands/runbooks/<名稱>/runbook.json`），紀錄與 Plan B 截圖也在那裡。

- 引擎與格式：`skills/no-errands/references/demo.md`
- 可公開的範例：`tests/selfhost/mm_runbook.py` 裡對自架 Mattermost 的迷你劇本（虛構資料）

寫新劇本的順序：`validate`（看清楚會執行的 shell 指令）→ `request-approval`（使用者在發件匣核准整份）→ `preflight` → `rehearse` → `learn`（使用者同意才 `apply`）→ 重要示範前 `freeze` → `live`。
