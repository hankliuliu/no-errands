#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UAC（Windows 使用者帳戶控制）偵測與交接（純 stdlib）。

UAC 對話框在「安全桌面」上：任何自動化（UIA、訊息、截圖）都碰不到，這是 OS 刻意的設計，也是合理交人的
五類之一。工具能做的是：**偵測到它出現 → 給使用者一句精確的交接 → 偵測到它消失就自動續跑**，人不必回報。

  uac.py check              # {"uac_prompt": bool, "handoff": "..."}
  uac.py wait [--timeout 300]  # 等到 UAC 對話框消失（使用者按了是/否），回 {"cleared": bool}
"""
import json, os, subprocess, sys, time
import nowin  # noqa: E402,F401  Windows：子程序不開主控台視窗（使用者回報視窗一直彈出）

HANDOFF = ("Windows 跳出了「使用者帳戶控制」確認視窗（螢幕會變暗、中間一個對話框）。這一步只有你能按："
           "確認上面寫的程式是我剛剛要安裝/執行的那一個，再按「是」；不認得就按「否」。"
           "按完不用跟我說，我偵測到視窗消失就會繼續。（大約 2 分鐘沒人按，Windows 會自動當成「否」。）")
# 本機實測（2026-11-22）：沒人按時提示約 120 秒後自動消失、提權指令沒有執行（等同按「否」）。
AUTO_CANCEL_S = 115


def uac_prompt():
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq consent.exe", "/NH"], capture_output=True, text=True, errors="replace",
                             timeout=10).stdout
        return "consent.exe" in out.lower()
    except Exception:
        return False


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    if a[0] == "check":
        p = uac_prompt()
        print(json.dumps({"uac_prompt": p, "handoff": HANDOFF if p else None}, ensure_ascii=False)); return
    if a[0] == "wait":
        to = float(a[a.index("--timeout") + 1]) if "--timeout" in a else 300
        t0 = time.time()
        while time.time() - t0 < to:
            if not uac_prompt():
                w = round(time.time() - t0, 1)
                print(json.dumps({"cleared": True, "waited_s": w, "likely_timed_out": w >= AUTO_CANCEL_S,
                                  "note": "視窗消失 ≠ 核准：按「否」或約 2 分鐘沒人按都會消失。用提權動作的結果（檔案、服務、版本號）判斷有沒有成功。"},
                                 ensure_ascii=False)); return
            time.sleep(1)
        print(json.dumps({"cleared": False, "waited_s": to, "next": "逾時：保留現場，結束回合並告訴使用者"})); return


if __name__ == "__main__":
    main()
