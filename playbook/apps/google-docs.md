# Google 文件：需要登入才看得到的文件怎麼讀

> 狀態：Windows 11、agent-browser 0.43.0（無頭）、專用 profile 已由使用者登入一次。2026-11-22 實測：自己建一份測試文件、打一句測試句，
> 再用下面的方法讀回、比對相符；匿名請求同一份文件被拒（401），證明它確實要登入。測完已移到垃圾桶。

## 走得通的路

1. **使用者先在專用 profile 登入 Google 一次**：用一般方式啟動的 Chrome（不帶自動化旗標，否則 Google 常拒絕登入）開
   `--user-data-dir=~/.no-errands/browser-profile` 到 `accounts.google.com`，他自己登入、關掉視窗。
   檢查有沒有登入：neb 無頭開 `https://myaccount.google.com/`，停在原址＝已登入，被導去 `accounts.google.com`＝沒登入（只看網址）。
2. **讀文件內容：開 `/mobilebasic`**：
   `neb --profile <profile> open "https://docs.google.com/document/d/<文件 id>/mobilebasic"` → `neb get text body`。
   這是純 HTML 版，文字讀得到。
3. 讀完把內文寫進要落地的地方（例如 context-gather 的 `cache/extracted/<文件 id>__<名稱>.md`），frontmatter 附 canonical 網址。

## 走不通的路與原因

- **一般的編輯頁 `/edit` 用 `get text`**：內文畫在 canvas 上，讀不到。
- **`/export?format=txt` 用瀏覽器開**：瀏覽器把它當下載，頁面上不是文件內容。要用這條路得帶登入的 cookie 直接發 HTTP 請求，
  但我們不把 cookie 拿出瀏覽器（機密值不離開 profile），所以不走。
- **匿名的 HTTP 請求**：需要登入的文件回 401。這就是 context-gather 記成 `denied` 的原因。

## 驗收方式

讀回的文字裡有你預期的段落（例如文件標題或第一段）；網址停在 `docs.google.com`，沒被導去登入頁。

## 交人點

- 第一次登入（帳密、兩步驟驗證）——只有使用者能做，做一次之後 profile 就記住。
- **不瀏覽使用者的雲端硬碟**：只開對方指定的那一份文件。
