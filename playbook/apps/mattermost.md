# Mattermost：唯讀查詢、經發件匣發文

> 狀態：工具有離線單元測試（`tests/test_mm_ro.py`、`tests/test_outbox.py`）；自架 Mattermost（官方 preview 映像，CI Linux）端到端見 `.github/workflows/selfhost.yml`。公司伺服器沒測過，要使用者先產生個人存取權杖。

## 走得通的路
1. **權杖**：使用者在網頁 Profile → Security → Personal Access Tokens 產生一個（伺服器要先由管理員打開這項功能），存成 `~/.no-errands/mattermost.txt`。
   檔案直接用頁面複製下來的格式就行：`存取 Token：xxxx`、`Access Token: xxxx` 或 `token=xxxx` 都可以（全形冒號、鍵名帶空格都認得）。
   伺服器網址寫在政策檔 `mattermost.url`；**沒寫就問使用者，不要猜**。
2. **權杖檔 agent 不讀**：只有 `mm_ro.py` 和發件匣在執行時讀。不 `cat`、不顯示、不複製（`setup.py --guard-rules` 會加一條拒絕讀取的權限規則）。
3. **唯讀查詢**（只發 GET）：
   - `python ~/.no-errands/bin/mm_ro.py whoami`
   - `... channels` → 回頻道數量
   - `... posts <channel_id> --n 20` → 回則數與時間範圍；要內文才加 `--content`
   - `... request GET /api/v4/...` → 其他 GET；給 POST/PUT/DELETE 一律拒絕
4. **要發文**：內容寫進檔案 → `python ~/.no-errands/bin/outbox.py draft --service mattermost --to <channel_id> --body-file <檔> --why "<理由>"`。
   頁面上顯示的對象名稱由伺服器查出，不是 agent 寫的。使用者在發件匣頁面按核准，才會真的送出；agent 沒有送出指令。
5. **等結果**：`outbox.py status <id>`；最多 `outbox.py wait <id>` 一次（上限 120 秒），沒結果就結束回合，不要一直問。

## 走不通的路與原因
- `mm_ro.py search` → 拒絕。Mattermost 的搜尋 API 是 POST，唯讀工具不發 POST。要搜尋就 `posts` 拿回來在本機篩。
- 用 POST `/channels/members/me/view` 標已讀 → 不做。讀貼文本身（GET）不會改未讀狀態（e2e 第 e 步驗證過）。
- 網頁版第一次開會先出現「要在哪裡開？」中繼頁 → 按「View in Browser」。

## 驗收方式
- 發文：核准之後用 `mm_ro.py posts` 讀回，確認貼文真的在頻道裡；退回的草稿不應出現。
- 查詢：回傳的數量和使用者在畫面上看到的一致。

## 交人點
- 產生權杖（要登入他的帳號）；伺服器沒打開 PAT 時找管理員。
- 每一則發文的核准。
