# 瀏覽器層用法（agent-browser）

單檔原生 binary，免 node；路徑見 CLAUDE.md 受管區塊（下稱 `ab`）。
第一次用先跑 `ab skills get core`（版本配對的官方指南）。預設 headless、不搶焦點。

## 基本迴圈（開 → 操作 → 驗收）

```
ab open <url|file:///...>        # 自動偵測系統 Chrome/Edge/Brave
ab snapshot -i -c                # a11y 樹 + ref（給你讀的結構）
ab click '@e12'                  # 或 CSS 選擇器：ab click '#submit'
ab get text '#result'            # 讀回驗收
ab screenshot out.png            # 視覺驗收（Read 這張圖）
ab close                         # 收尾必做
```

批次低延遲：`ab batch "open ..." "click ..." "get text ..."`。
JSON 輸出加 `--json`。MCP 模式：`ab mcp`（stdio）。

## 已登入 session（鑰匙）

- 一律用 agent 專用 profile：`ab --profile ~/.no-errands/browser-profile ...`
  cookie/localStorage 跨程序持久（已實測）。**不要**碰使用者日常 profile
  （`--profile Default`、`--auto-connect`）：有鎖檔與隱私問題，且安全守則禁止。
- 未登入的站：`ab --headed --profile <同上> open <登入頁>` → 告訴使用者
  「請在跳出的視窗登入，我看不到密碼」→ 你**輪詢**（`get url`／cookie／頁面元素）
  偵測登入完成即自動續跑。擋自動化瀏覽器的站（預期：Google）：改用原生瀏覽器開同一
  profile 目錄，訊號是使用者關窗，再 headless 驗證；把該站記進 playbook。

## 內部設定頁

`chrome://settings/`、`brave://settings/` 可以 open + snapshot + click（已實測 Chrome）。
「我沒辦法點瀏覽器裡的按鈕」在這條路線下不成立。

## 零依賴 fallback（連 ab 都沒有時）

系統 Chrome/Edge 直驅（Windows 11 必有 Edge）：
```
chrome --headless --dump-dom <url>                 # 讀 DOM
chrome --headless --screenshot=<絕對路徑.png> --window-size=1200,800 <url>
```
CDP：`--remote-debugging-port=9222` **必須**配非預設 `--user-data-dir`
（Chrome 136+ 對 default profile 直接忽略旗標，死路，別試）。

## 已知坑

- `cookies set --expires` 寫入後列不出來；改用頁面 JS：`ab eval "document.cookie='k=v; max-age=86400'"`。
- Git Bash 下 `--screenshot=` 要給**絕對 Windows 路徑**（MSYS 會吃掉相對路徑）。
- PowerShell 下 ref 要單引號：`ab click '@e12'`（`@` 是 splatting）。
- `--headed` 會搶前景焦點（實測）：只用在首次登入交接與 demo。
- 收尾：`ab close`；大掃除 `ab close --all`。它有背景 daemon 與 `~/.agent-browser` 狀態目錄。
- 殺殘留瀏覽器程序：用命令列指紋（profile 路徑）過濾 PID，絕不用 `taskkill /IM`。
