# GUI 安裝精靈從頭點到完成（Windows）

> 狀態：CI（windows-latest）實測——NSIS（Notepad++ 8.6.9）與 Inno Setup（WinMerge 2.16.44）都從第一頁點到 Finish、程式檔存在、靜默移除。
> 測試程式 `tests/wizard/run_installer.py`。使用者電腦上沒跑過（會安裝軟體、開視窗）。

## 走得通的路
1. **先找靜默安裝參數**（最輕）：NSIS `/S`、Inno `/VERYSILENT /SUPPRESSMSGBOXES`、MSI `msiexec /i x.msi /qn`。有就不用點精靈。
2. 要走精靈（使用者想看選項、或沒有靜默參數）時，每一頁：
   - 找名稱在「下一步類」清單裡、而且 enabled 的按鈕（Next > / I Agree / Install / 下一步(N) > / 安裝(I)…，比對前去掉 `&`）。
   - 授權頁先選「I accept…」單選鈕。
   - 完成頁先取消「執行／啟動」勾選框，再按 Finish。
3. **點擊的升級順序**（同一頁按了不換頁才往下一階）：
   1. agent-desktop 語意點擊。
   2. **對按鈕的父視窗投遞 `WM_COMMAND(BN_CLICKED, 控制項 ID, 按鈕 HWND)`**——真實點擊最後產生的就是這個訊息；不需要前景、不注入輸入。**NSIS 卡在「Install」就是靠這一階過的。**
   3. BM_CLICK。
   4. 實體點擊（`--headed`；要先把視窗拉到前景）——只在 CI 或使用者同意、閒置閘門下。
4. **list-windows 看不到精靈時**（Inno：精靈是隱藏應用程式視窗擁有的 owned 視窗，而且實際跑的是子程序 `setup.tmp`）：
   用 Win32 列舉「安裝程式＋它的子程序」的可見頂層視窗 → 列子視窗找類別含 `Button` 的（Inno 是 `TNewButton`）→ 同樣投遞 WM_COMMAND。
5. 驗收後要移除：先關掉「從剛裝好的路徑啟動」的程序（依路徑找 PID），再跑移除程式；Inno 的移除程式會把自己複製到暫存再跑、立即返回，要等它（最多約 2 分鐘）。

## 走不通的路與原因
- NSIS 的「Install」：語意點擊、BM_CLICK、實體點擊都回 ok 但不換頁。BM_CLICK 在對話框不是作用中視窗時可能失效（MSDN 註明）；UIA Invoke 對 Win32 按鈕走的也是 BM_CLICK。
- Inno：agent-desktop 的 list-windows 從頭到尾看不到精靈（同 LINE 的彈出選單，是被濾掉的頂層視窗）；舊版只找類別 `Button` 也找不到 `TNewButton`。
- Inno 完成頁的「啟動 WinMerge」是 TNewCheckListBox 裡的一項，不是按鈕，投遞訊息取消不了 → 程式會被啟動、鎖住檔案，移除就失敗。
- 用程序名關程式（`taskkill /IM`）→ 會關到使用者自己開的同名程式。

## 驗收方式
- 目標程式檔存在（例：`C:\Program Files\Notepad++\notepad++.exe`），不是「按到了 Finish」。
- 要移除的話：檔案消失。

## 交人點
- UAC 提示（安裝到 Program Files 通常會跳）：見 `uac-handoff.md`。偵測到就交給使用者，自己等它消失。
- 安裝選項（元件、路徑、開機啟動）：先問使用者要什麼，不要全部預設。
