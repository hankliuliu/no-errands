---
name: errand-audit
description: >-
  跑腿稽核：掃使用者本機的 Claude Code 對話歷史，找出 agent 叫人類動手的時刻（截圖給我、手動點一下、
  照這份教學做…），由你逐筆複判後產出個人報告，含安裝前後對照。Use when the user asks to audit how often
  their agent asked them to do things by hand, or wants their own before/after numbers.
---

# 跑腿稽核

全程本機，不上傳、不貼到任何外部服務。報告寫到 `~/.no-errands/audit/`。

## 步驟

1. 跑初篩（關鍵字＋「手把手教學」偵測），輸出候選清單：
   `python <這個 skill>/scripts/errand_audit.py --candidates ~/.no-errands/audit/candidates.json`
   （預設讀 `~/.claude/projects`；`--root` 可改。安裝日讀 `~/.no-errands/config.json` 的 `installed_at`，
   也可用 `--split-date YYYY-MM-DD` 指定。）
2. **逐筆複判**候選（每筆有前一則使用者訊息、下一則訊息、日期）。分成：
   - 真跑腿：A 權限／系統設定、B 瀏覽器或 GUI 操作、C 帳號牆（要使用者登入後拿資料）、D 驗收回傳（截圖、貼輸出）、E 物理世界
   - 教學模式（使用者明說想自己學）
   - 誤判
3. 寫報告 `~/.no-errands/audit/report-<日期>.md`：
   - session 數、有命中的 session 數、真跑腿次數、各類統計、每 session 平均
   - 安裝前後對照（每 session 真跑腿次數）
   - 去識別化的代表例句 5 則（腳本已遮掉路徑、email、網址；人名請你再檢查一次）
   - 「這些裡面哪些現在不求人接得住」：對應眼（自己截圖驗收）、手（桌面／瀏覽器操作）、鑰匙（登入過的 profile）、交接（一次問完）
4. 用一張小表把結果告訴使用者，附報告路徑。

## 隱私

- 對話歷史可能含私人內容：只把統計與去識別化例句放進報告；不要把整段對話抄進報告或對話。
- 專案資料夾名稱常含使用者名稱，腳本只輸出雜湊。
