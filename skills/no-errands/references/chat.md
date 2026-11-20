# 通訊軟體（LINE / Mattermost / …）

使用者願意把聊天帳號給 agent 用，是為了省時間——所以**授權過的 app 內，讀取是放手的**。
能不能碰、碰到哪裡，由三層規則決定（見 SKILL.md〈規則的三層〉）：

| 層 | 聊天情境的具體內容 |
|---|---|
| 不變量 | 你**沒有送出能力**：不在訊息輸入框輸入、不按送出/表情/貼圖/附件、不轉傳。要回覆 → `outbox.py draft` 起草到核准發件匣，使用者核准後由發件匣程序送出。token/cookie 值不輸出。 |
| 閘門 | app 不在 `policy.json` 的 `granted_apps` → 不讀不碰，包含把它開到登入畫面。先在對話中取得指名同意。 |
| 偏好 | `chat.unread_protection`（預設 on）：不開「有未讀且不在白名單」的對話。`chat.whitelist`：使用者指名、不受未讀保護限制的對話。 |

## 怎麼做

- **桌面聊天 app**：一律經 `chat_guard`（不是 `ned`、不是 agent-desktop 直呼）。
  `locate --chat <精確名稱>` → `open --chat <名稱> [--expect <指紋>]` → `read` 或 `export --to <資料夾>` → `close`。
  輸入框、送出鈕、表情/貼圖/附件鈕、清單中非目標項目都是程式擋下的禁區；全程不送鍵盤事件。
  未讀保護 on 時，非白名單對話要帶 `locate` 回的指紋，狀態一變（可能來了新訊息）就拒開。
- **Mattermost**：用 `mm_ro.py`（只能發 GET，路徑限 `/api/v4/`）。token 由腳本自己從設定檔讀，
  你不要讀那個檔案、也不要輸出 token。會動已讀狀態的端點（`channels/members/{user}/view`、
  `mark-*`、`set-post-unread`）都是 POST，唯讀工具本來就發不出去。
- **要傳訊息**：`outbox.py draft --service <fake|mattermost|…> --to <對象> --body-file <檔> --why "<來由>"`
  → 告訴使用者草稿在發件匣頁面等他核准（給網址）→ 背景監看 `outbox.py status <id>` →
  核准或退回後續跑。**不要開啟或操作發件匣頁面**，那是使用者的介面。

## 內容的去處

聊天內容只回給當下的任務。不寫進 log、playbook、repo、分享素材、commit 訊息。
引用時給「哪個對話、哪個時間點」，不貼原文。對話名稱本身也可能是私人資訊（透露合作單位），
文件裡寫「使用者指名的群組」。

## 判斷 app 登入狀態（不接觸內容的訊號）

程序存在（LINE.exe）→ 視窗標題 → 視窗幾何（LINE 登入窗約 400×460；主介面 >1000 寬）。
這些訊號不需要授權也可以看；再往下（snapshot、截圖）就屬於讀取，要先過閘門。

## Mattermost API 參考（2026-11 查官方文件）

- 讀貼文：`GET /api/v4/channels/{id}/posts`（權限只需 read_channel）。
- 已讀上界：`GET /api/v4/channels/{id}/members/{user_id}` 的 `last_viewed_at`（單位待實測）。
- `since` 參數：回「modified since」、上限 1000、不保證連續 → 增量要自己補洞。
- 旁證：實驗室 context-gather 以 PAT + 純 GET 同步數月，未出現頻道被標已讀的副作用。
