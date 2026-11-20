# 鑰匙：已登入 session（個人帳號後的資訊）

核心：**agent 專用持久 profile**（路徑見受管區塊），與使用者日常瀏覽器完全隔離。
使用者只在想授權的網站登入一次，之後 cookie/localStorage 跨程序永久有效（doctor 已驗）。

絕不：碰使用者日常 profile（`--profile Default`、`--auto-connect`）；詢問/輸入/儲存密碼或
2FA；繞過 CAPTCHA。Chrome 136+ 已封死 CDP 接 default profile——別浪費時間試。

## 首次登入交接：兩種模式

**模式 a（預設）— headed agent-browser + 輪詢續跑**
```
ab --headed --profile <profile> open <登入頁>
→ 告訴使用者：「請在跳出的視窗登入 <站名>，我看不到你的密碼。」
→ 你輪詢（每 3–5 秒）：ab get url / ab eval "document.cookie" / 登入後才有的元素
→ 偵測到登入成功 → 告知「收到，繼續」→ 轉回 headless 做事
```
使用者**不需要**回來說「好了」。輪詢上限約 5 分鐘，逾時禮貌收攤、保留現場。

**模式 b — 原生瀏覽器開同一 profile（給擋自動化瀏覽器的站，預期：Google 帳號）**
```
<chrome路徑> --user-data-dir=<profile> <登入頁>     # 不帶任何自動化旗標
→ 告訴使用者：「登入完成後把那個視窗關掉即可。」
→ 你輪詢該程序是否結束（用 user-data-dir 參數指紋找 PID，不用程序名）
→ 程序結束 → ab --profile <同一目錄> headless 開登入後頁面驗證 → 繼續
```
注意：模式 b 期間 profile 被原生瀏覽器鎖住，agent-browser 不要同時碰。

哪個站需要模式 b → 記進 playbook（sites/<站>.md 的「交人點」段）。

## 登入後

- 用該 profile headless 取資訊：`ab --profile <profile> open <url>` + read/snapshot/screenshot。
- 登入態過期的訊號（被導回登入頁、401 頁面）→ 重走交接，一樣一次問完。
- 下載檔案：`ab --download-path <dir>` 或 `download <sel> <path>`。
