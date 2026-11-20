# 通訊軟體（LINE / Mattermost / …）：使用者幫你登入後的鐵律

使用者願意把聊天帳號登入給 agent 用，是最高等級的信任。鐵律（違反一次就失去全部）：

1. **不開有未讀標記的對話**；不擷取含未讀預覽的畫面或 a11y 結構。
2. **永不送出**：不在輸入框輸入、不按送出、不按表情回應、不轉傳。
   要回覆 → 照交接契約擬稿給使用者核可，由使用者自己送（或明確授權的送出流程）。
3. **不改已讀狀態**：不把未讀變已讀、也不把已讀標回未讀。
4. **只碰使用者指名的對話**，其他對話連列表 hover 都不要。
5. **聊天內容不落地**到 log、playbook、對話紀錄——引用時只給「哪個對話、哪個時間點」。

## 判斷 app 登入狀態（不接觸內容的訊號）

程序存在（LINE.exe）→ 視窗標題 → **視窗幾何**（LINE 登入窗約 400×460；主介面 >1000 寬）。
已登入 → 到此為止，不 snapshot、不截圖（樹與圖都會含對話內容/未讀預覽）。

## Mattermost 唯讀路徑（設計草稿，待真實帳號驗證）

目標：用 agent 專用 profile 的已登入 session，讀「使用者已讀範圍內」的貼文，
不需要 PAT（省掉 admin 開 PAT 的跑腿）、不動已讀狀態。

依官方文件（docs.mattermost.com/api/reference，2026-11 查）：
- 讀貼文：`GET /api/v4/channels/{id}/posts`（權限只需 read_channel；文件未提它會動已讀）。
- **會動已讀的都是 POST**：`channels/members/{user}/view`、`mark-channels-read-*`、
  `mark-all-*`、`set-post-unread` ——**全部禁用**。
- 已讀上界：`GET /api/v4/channels/{id}/members/{user_id}` 的 `last_viewed_at`
  （⚠ 欄位與單位文件頁未渲染，待以 OpenAPI YAML 或實測確認）。
- `since` 參數：回「modified since」、上限 1000、不保證連續（原文明寫）→ 增量要自己補洞。

旁證：實驗室 context-gather 以 PAT + 純 GET 同步數月，未出現頻道被標已讀的副作用。

實作形狀（待驗證）：agent-browser `--profile <已登入>` 開站內任一頁 →
`eval fetch('/api/v4/...', {credentials:'same-origin'})`（同源帶 session cookie）→
只取 `create_at <= last_viewed_at` 的貼文。瀏覽器本體不進頻道畫面（避免客戶端自動送 view）。

## 驗證清單（等使用者登入後、由他在場時跑）
1. GET posts 前後，另一個客戶端看該頻道未讀數不變。
2. channel member 回應確認 last_viewed_at 存在＋單位。
3. 站內 fetch 的 CSRF/headers 需求（MM 對 GET 通常免 token，待確認）。
