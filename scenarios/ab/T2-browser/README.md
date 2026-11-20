# T2-browser：必須在瀏覽器裡按按鈕才能拿到的答案

模擬 R4 真實跑腿：「我沒辦法點瀏覽器裡的按鈕，這一步要請你自己做」。

- `index.html`：按「產生驗證碼」後，JS 跑 500 萬輪整數混合運算，顯示 8 位十六進位碼。
  結果是確定性的（同一份檔案永遠同一個碼），但人腦與「讀原始碼」推不出來，
  必須真的執行 JS——在本機沒有 node 的前提下，實務上等於必須開瀏覽器按按鈕。
- Ground truth 取得方式：用任一自動化瀏覽器實際點一次（例：
  `agent-browser open file:///...` → `click #gen` → `get text #code`），把碼記進
  `journal/evidence/ab/`（不要放在這個目錄——受測 agent 讀得到這裡）。
- A/B 跑法：把 `index.html` 複製到兩個乾淨工作目錄（bare / kit），同一句使用者語氣的
  prompt、同一組 `--allowedTools`，用 `claude -p` 各跑一次，記錄全文、是否答對、
  是否叫人跑腿、耗時。
- 若 bare 條件自己想出合法解法（如 Edge headless、WSL 重寫演算法）：照實記錄——
  那是「考題要更難」或「價值主張要修正」的訊號，不是失敗。
