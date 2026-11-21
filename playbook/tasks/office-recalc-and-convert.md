# xlsx 公式重算、舊版 .ppt/.doc/.xls 轉 PDF（不開視窗）

> 狀態：Windows（Microsoft Office COM）本機實測；Linux（LibreOffice headless）CI 實測。macOS（LibreOffice）CI 實測 4/4。

## 走得通的路
- `python ~/.no-errands/bin/office.py recalc <in.xlsx> --out <out.xlsx>`：公式沒有快取值的 xlsx（程式產生的常見）重算後存檔。
- `python ~/.no-errands/bin/office.py convert <in.ppt|.doc|.xls|...> --to pdf --outdir <dir>`。
- Windows 有 Office → COM，`Visible=False`、`DisplayAlerts=False`，結束只關自己開的那個程序（以視窗 handle 找 PID）。
- 沒有 Office → `soffice --headless`，用暫存的獨立 profile（不碰使用者的 LibreOffice 設定、不跟開著的實例衝突）。

## 走不通的路與原因
- Office 第一次以 COM 啟動可能跳授權／首次使用對話框 → 在使用者機器上第一次跑放進閒置閘門（`idle.py wait`），或先告知。
- 用 `Get-Process EXCEL | Stop-Process` 收尾 → 會關掉使用者自己開的 Excel。只關自己的 PID。

## 驗收方式
重算：打開輸出 xlsx 的 sheet XML，公式格有 `<v>` 值且等於預期。轉檔：輸出檔開頭是 `%PDF`。

## 交人點
無。
