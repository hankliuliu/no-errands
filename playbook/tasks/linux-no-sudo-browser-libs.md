---
target: Linux 無 sudo 環境（實驗室共用 GPU server、學生帳號）缺瀏覽器系統庫
platforms: [linux]
rung: 3
verified: 2026-11-21（WSL2 Ubuntu 24.04 + agent-browser 0.43.0 / Chrome 158）
---

# 無 sudo 補 Chrome 共享庫（libnspr4 / libnss3 / libasound2）

## 症狀
`agent-browser install` 下載 Chrome 成功，啟動時
`error while loading shared libraries: libnspr4.so`。

## 走得通的路（免 root）
```sh
mkdir -p ~/ne-libs/deb && cd ~/ne-libs/deb
apt-get download libnspr4 libnss3 libasound2t64      # 只下載 .deb，不需要 root
for d in *.deb; do dpkg -x "$d" ~/ne-libs/extract; done
export LD_LIBRARY_PATH=$HOME/ne-libs/extract/usr/lib/x86_64-linux-gnu
agent-browser open <url>    # 之後所有呼叫都帶這個環境變數
```
缺別的庫同理：看錯誤訊息裡的 .so 名 → `apt-file search` 或 packages.ubuntu.com 查套件名 → download+extract。

## 走不通的路與原因
- `agent-browser install --with-deps`：要 sudo。
- `sudo apt-get install ...`：共用機器學生帳號沒有 sudo（本機實測也會被 agent 權限層擋）。

## 驗收方式
`agent-browser --session t open <測試頁>` → `click` → `get text` 讀回預期內容。

## 交人點
無（全程免 root、免交人）。若連 `apt-get download` 都被網管擋，才回頭請管理員。
