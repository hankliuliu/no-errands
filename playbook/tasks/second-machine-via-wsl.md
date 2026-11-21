# 「請你用另一台試試看」：把 WSL 當第二台機器（Windows）

> 狀態：Windows 11 + WSL2 Ubuntu 24.04 實測（2026-11-21）。純網路、不開任何視窗。

## 走得通的路
- **服務放在 WSL、從 Windows 連**：WSL 裡 `python3 -m http.server <port> --bind 127.0.0.1`，Windows 端
  `curl http://127.0.0.1:<port>/` 就連得到（WSL2 會把 localhost 轉送進去）。適合驗「別台機器裝起來跑不跑得動」：
  在 WSL 裡從乾淨的 clone 安裝、啟動，再從 Windows 當客戶端打。
- 從 Git Bash 呼叫 WSL 一律 `wsl -e bash -lc '...'`（PowerShell 5.1 的引號規則會把參數打碎）。
- 背景啟動用 `nohup … & echo $! > pidfile`，收尾用 pidfile 的 PID 關，不要按程序名。

## 走不通的路與原因
- **服務放在 Windows、綁 0.0.0.0 讓 WSL 連**：Windows 防火牆會跳出「允許存取」對話框——那是使用者要處理的視窗，
  背景執行時不要這樣做。真的需要時改綁 127.0.0.1，並查 WSL 的網路模式（mirrored 模式下 WSL 可直接連 Windows 的 localhost）。

## 驗收方式
從「另一台」打回應內容比對（例如固定字串或版本號），而不是只看連線成功。

## 交人點
無。
