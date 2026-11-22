# no-errands 規格（定稿）

## 一句話
人負責決定，agent 負責動手——而且是人管得住的手。

## 原則
1. **先自己做**：依能力階梯往上試，最輕的手段優先；只有合理交人的五類才交給人。
2. **做完自己驗收**：有畫面的成果自己看過（單一視窗截圖或讀回），單步回 ok 不等於任務正確。
3. **守門靠機制，不靠叮嚀**：同意閘門、禁區、發件匣都寫在程式裡；文件只說明它們。
4. **不打擾**：不注入鍵盤滑鼠；會讓視窗跳到前景的步驟只在使用者閒置時做，偵測到就交還。
5. **說實話**：對使用者的每一句保證都要對得上一項實測；做不到的寫做不到。

## 能力階梯
0 查 playbook → 1 檔案／CLI／API（含跨環境借力，如 WSL）→ 2 眼（看圖、單一視窗截圖）→ 3 瀏覽器（乾淨 profile）→
4 瀏覽器（agent 專用、已登入的 profile；同意閘門）→ 5 桌面無障礙語意操作（經 `ned`／`chat_guard`）→ 6 截圖＋座標（最後手段）→ 7 交給人（合理交人＋交接契約）。

## 規則的三層
| 層 | 內容 | 誰能調 |
|---|---|---|
| 不變量 | 對外訊息只能起草到發件匣，由使用者核准、由發件匣送出；不碰密碼／2FA；不繞過存取控制；機密值不輸出；不覆寫使用者檔案 | 沒有人 |
| 閘門 | 第一次碰個人帳號（聊天、郵件、雲端、金融、社群——桌面 app 與網站都算）要使用者在對話中指名同意，記進 `policy.json` | 使用者逐一授權 |
| 偏好 | 未讀保護＋白名單；可見度（背景／觀看）；會搶前景的步驟何時做（等閒置／先問） | 使用者隨時調 |

## 機制（各自有測試）
| 機制 | 做什麼 | 測試 |
|---|---|---|
| `ned` | 桌面 app 代理（白名單，`docs/agent-desktop-command-audit.md`）：app 範圍鎖定（含 launch 目標、--surface）、敏感 app 閘門（三平台名稱）、背景模式擋搶焦點與實體輸入、發件匣頁面禁區 | `tests/test_ned.py` |
| `neb` | 瀏覽器代理：敏感網域閘門；已授權的只讀（白名單：對 agent-browser 每個子指令與選項逐項對過，`docs/agent-browser-command-audit.md`）、不點送出類按鈕；繞過逐步檢查的指令與選項任何網站都拒 | `tests/test_neb.py` |
| `chat_guard` | 聊天 app：精確定位、標頭驗證身分、禁區（輸入框／送出／貼圖／通話）、彈出選單只點完全相符的項目、另存新檔以預設檔名與首行驗身分、絕不覆寫、閒置閘門與交還前景 | `tests/test_chat_guard.py`、`tests/test_line_profile.py` |
| 發件匣 `outbox` | 起草→固定頁面→使用者核准→伺服器送出；一台一個核准頁面，只放要傳出去的訊息；對象由伺服器解析、修訂雜湊防竄改、自動化瀏覽器不可核准、有上限的等待。示範劇本兩種核准模式：`page`（預設，每份都看）／`standing`（使用者在頁面親自核准過同主機且帳號涵蓋的劇本後由伺服器自動核准、寫 decide-log；撤銷或退回即暫停；只限示範主機、內建敏感 app 不可） | `tests/test_outbox.py`、`tests/test_standing_approval.py`、`tests/selfhost/mm_flow.py` |
| `mm_ro` | Mattermost 唯讀（只發 GET；讀貼文不動已讀） | `tests/test_mm_ro.py` |
| `gather` | 聊天收集：起始時間＋增量同步；LINE 經 chat_guard 匯出、序列對齊合併、缺口明講；Mattermost 用 API 游標（含編輯與刪除）；可寫成 context-gather 格式 | `tests/test_gather.py`、`tests/selfhost/mm_flow.py`（f 段） |
| `idle` | 使用者閒置偵測、途中有輸入就中止交還 | `tests/test_idle.py` |
| `uac` | 辨識系統權限提示、交接、偵測消失 | `tests/test_uac.py`＋本機實測 |
| `--guard-rules [--guard-groups ...]` | Claude Code 設定三組、可分開開關：`direct-tools`（deny 直接呼叫兩個底層工具）、`token`（PreToolUse hook：預設不准讀 token 檔，`policy.py token-allow` 白名單專案放行）、`consent`（`policy.py grant/set/whitelist/domain/token-allow` 要詢問；chat_guard 不詢問） | `tests/test_guard_rules.py`＋巢狀 agent 實測 |
| `setup`／`doctor` | 偵測→重用→不覆蓋→可完整回復；每一階做真的測試 | `tests/test_coexist.py`、CI 三平台 |

## 交接契約
只有五類交人：密碼／2FA／CAPTCHA／QR、OS 信任提示（UAC 等）、物理世界、不可逆／對外／金錢／帳號層級的決定、使用者明說想自己學。
交法：先做完其他事 → 一次問完 → 精確（在哪、按什麼、預期看到什麼）→ 不等回話（能偵測就偵測；人在場前景最多等一下，不在場就結束回合）→ 結果持久化，同一件事不問第二次。

## 非目標
不做帳密保險庫；不接使用者日常瀏覽器 profile；不繞過任何存取控制；不做常駐背景服務（發件匣只在有草稿時啟動）；不替使用者送出任何訊息。

## 已知限制
- 防線擋的是「太熱心」的 agent，不是惡意程式；權限規則是前綴比對。
- 語意點擊仍可能讓目標 app 自己跳到前景（視系統前景鎖而定）；保證的是偵測、交還與閒置閘門。
- 有些模型起草後仍會在前景等待核准；工具提供上限與提示，無法強制。
