# 「請你去設定頁產生一個 token 貼給我」→ agent 自己產生、直接寫進設定檔

> 狀態：自架的真實 Gitea 與 Mattermost（CI）實測；demo 頁三平台實測。真實的 GitLab／公司 Mattermost 要使用者在專用 profile 登入一次。

## 走得通的路
1. 使用者在 agent 專用 profile 登入該站一次（登入時勾「記住這台裝置」，關掉瀏覽器後才仍是登入狀態——Gitea 實測）。
2. agent 用 agent-browser（headless、同一個 profile）開 token 頁，**用 snapshot 依角色＋名稱找欄位與按鈕**（不寫死 CSS selector）：
   填描述 → 需要時選權限範圍 → 按建立。
3. token 只出現一次：用 `get text` 讀進程式、直接寫進設定檔（`KEY=VALUE`，保留檔案其他內容），**不印出、不經過對話**。
4. 用該 token 打一次 API 驗證有效（例如 `/api/v1/user`、`/api/v4/users/me`），只回布林／使用者名稱。

## 走不通的路與原因
- 沒勾「記住這台裝置」→ session cookie 在瀏覽器關閉後就沒了，下一次 agent 開瀏覽器又在登入頁。
- 點了按鈕馬上讀 → 頁面還沒換，讀不到；要等「證明成功的那個元素」出現（例如 token 欄位），不要只等網路閒置。
- macOS 新 profile 第一次開瀏覽器會回錯（瀏覽器其實起來了）→ 先開一次空白頁暖機。
- Mattermost 要先由管理員在 System Console 打開「個人存取權杖」；網頁版第一次會先出現「要在哪裡開」的中繼頁，按「在瀏覽器中檢視」。

## 驗收方式
設定檔裡的值的雜湊 = 伺服器發出的那一個；用它打 API 成功；過程輸出不含 token。

## 交人點
只有「在專用 profile 登入一次」。之後同一站不再問。
