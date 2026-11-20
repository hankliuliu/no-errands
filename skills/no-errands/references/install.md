# 手：安裝軟體

順序（由靜默到吵）：

1. **套件管理器 user-scope 靜默**：
   - Windows：`winget install --id <Id> --scope user --silent --accept-package-agreements --accept-source-agreements`
     （有些套件不支援 user scope → 會要 UAC，見下方提權）
   - macOS：`brew install <formula>`；Linux：`apt-get`（要 sudo = 提權類）
2. **官方安裝檔的靜默參數**：下載後 `installer.exe /S`、`/quiet`、msiexec `/qn`……
   （參數查該軟體文件；下載一律核對官方 checksum）
3. **可攜版 / 單檔 binary**：GitHub Releases + sha256，放 `~/.no-errands/bin/`，不改 PATH。
4. **桌面 a11y 點安裝精靈**（agent-desktop）：下一步/同意/完成都點得了；
   UAC 跳出時就是交接點。
5. **交給人**：照交接契約。

## 提權（UAC / sudo）規則

- **收集合併**：把所有需要提權的動作攢成一批，一次問。
- **事前列明**：「等一下會跳一次 UAC，內容是安裝 X 與 Y，來源與雜湊已核對」。
- 不嘗試繞過 UAC/sudo（守則）。

## 裝完必驗（真的執行）

- `<tool> --version` 真的跑一次——PATH 裡的名字可能是假的
  （實測：Windows 的 `python` 是 Microsoft Store 捷徑，Get-Command 看得到、執行會跳商店）。
- 寫入紀錄：裝了什麼、怎麼回復。

## Windows 編碼坑（寫安裝腳本時）

- PS 5.1 把無 BOM 的 UTF-8 .ps1 當 ANSI：**.ps1 一律 ASCII-only**；
  `Get-Content` 讀 UTF-8 JSON 要 `-Encoding UTF8`。
- repo 裡的 .sh/.py 用 .gitattributes 鎖 LF，否則 Windows checkout 害 Linux 端跑不動。
