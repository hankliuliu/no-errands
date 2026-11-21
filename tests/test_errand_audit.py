#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""errand-audit 腳本測試（純 stdlib、合成的對話紀錄；不讀任何真實歷史）。"""
import json, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "skills", "errand-audit", "scripts", "errand_audit.py")
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


def rec(t, ts, text):
    if t == "user":
        return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


td = tempfile.mkdtemp(prefix="ne-audit-")
try:
    proj = os.path.join(td, "C--Users-alice-Documents-proj"); os.makedirs(proj)
    with open(os.path.join(proj, "s1.jsonl"), "w", encoding="utf-8") as f:   # 安裝前
        for r in (rec("user", "2026-10-01T10:00:00Z", "幫我把設定改好"),
                  rec("assistant", "2026-10-01T10:00:05Z", "我改好了。請你手動打開瀏覽器確認一下畫面，檔案在 C:\\Users\\alice\\x.txt"),
                  rec("user", "2026-10-01T10:01:00Z", "好")):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(proj, "s2.jsonl"), "w", encoding="utf-8") as f:   # 安裝後、沒有跑腿
        for r in (rec("user", "2026-11-25T10:00:00Z", "修一下"), rec("assistant", "2026-11-25T10:00:05Z", "修好了，我截圖確認過。")):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    out = os.path.join(td, "cands.json")
    p = subprocess.run([sys.executable, SCRIPT, "--root", td, "--candidates", out, "--split-date", "2026-11-21"],
                       capture_output=True, text=True, encoding="utf-8")
    check("腳本執行成功", p.returncode == 0, p.stderr[-300:])
    d = json.load(open(out, encoding="utf-8"))
    check("掃到 2 個 session", d["session_count"] == 2, d["session_count"])
    check("安裝前有命中、安裝後沒有", d["split"]["hits_before"] >= 1 and d["split"]["hits_after"] == 0, d["split"])
    blob = json.dumps(d, ensure_ascii=False)
    check("候選清單不含使用者名稱（專案名與路徑都去識別化）", "alice" not in blob, blob[:300])
    c = d["candidates"][0]
    check("候選附前一則使用者訊息與日期", c["prev_user"] == "幫我把設定改好" and c["date"] == "2026-10-01", c)
finally:
    shutil.rmtree(td, ignore_errors=True)
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
