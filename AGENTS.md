# no-errands（不求人）— 給 Codex 等其他 agent

請先讀同一個資料夾的 `CLAUDE.md`：安裝流程與維護規約都在那裡（這裡刻意不用 symlink，因為 Windows clone 後 symlink 會壞）。

關鍵規約摘要：
- 使用者說「安裝不求人」：先說要改什麼並等他同意 → 跑 `setup.ps1` / `setup.sh` → **會開視窗的檢查與示範先問他方不方便** → 結果整理成表 → 告訴他怎麼移除。
- 讀寫使用者的檔案逐位元組保留；只操作自己開的視窗；機密值不輸出；會開視窗的測試放 CI。
- Codex 目標（`setup.py --codex`）：安裝位置（`<CODEX_HOME>/skills`、`~/.agents/skills`、`AGENTS.md` 受管區塊）與移除後乾淨，三平台 CI 有測試；Codex 實際載入 skill 後的行為**尚未實測**。
