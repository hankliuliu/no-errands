#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uac.py 單元測試（純 stdlib、三平台；不需要真的 UAC）。偵測邏輯用假 tasklist 輸出驗證；
真機 UAC 未實測（CI runner 關閉了 UAC）。"""
import json, os, subprocess, sys, io, contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "no-errands", "scripts"))
import uac  # noqa: E402

R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:200]))


class Fake:
    def __init__(self, out): self.stdout = out


real_run, real_os = subprocess.run, uac.os.name
try:
    uac.os.name = "nt"
    uac.subprocess.run = lambda *a, **k: Fake("consent.exe                  4321 Console    1    12,345 K\n")
    check("tasklist 有 consent.exe → 偵測到 UAC", uac.uac_prompt() is True)
    uac.subprocess.run = lambda *a, **k: Fake("INFO: No tasks are running which match the specified criteria.\n")
    check("沒有 consent.exe → 沒有 UAC", uac.uac_prompt() is False)
    def boom(*a, **k): raise OSError("x")
    uac.subprocess.run = boom
    check("tasklist 失敗 → 回 False 不崩潰", uac.uac_prompt() is False)
    uac.subprocess.run = lambda *a, **k: Fake("consent.exe 1 Console 1 1 K\n")
    buf = io.StringIO()
    sys.argv = ["uac.py", "check"]
    with contextlib.redirect_stdout(buf):
        uac.main()
    o = json.loads(buf.getvalue())
    check("check 輸出交接訊息", o["uac_prompt"] and "只有你能按" in o["handoff"] and "不用跟我說" in o["handoff"], o)
    uac.os.name = "posix"
    check("非 Windows → 一律 False", uac.uac_prompt() is False)
finally:
    uac.subprocess.run, uac.os.name = real_run, real_os
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
