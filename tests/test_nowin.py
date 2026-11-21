#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不干擾：從「沒有主控台的父程序」呼叫 ned／neb 時，不可以彈出任何終端機視窗、不可以改變前景（Windows）。

背景（2026-11-22）：使用者回報「視窗一直彈出」。量測：背景程序（沒有主控台）每啟動一個命令列程式，Windows 11 就開一個
Windows Terminal 視窗並搶前景（20 秒 15 個）。之前的不干擾測試都從有主控台的程序跑，所以沒抓到。
修法：scripts/nowin.py——子程序一律帶 CREATE_NO_WINDOW。

  1 單元：nowin.add_flag 會加旗標、不覆蓋呼叫者自己的旗標
  2 靜態：scripts 裡每個會開子程序的檔案都有 import nowin
  3 端對端（只在 Windows）：以 DETACHED_PROCESS 啟動 neb.py／ned.py（各連續 5 次，假的執行檔是主控台程式），
    20 毫秒取樣頂層視窗：新出現的終端機類視窗必須是 0、前景不變
"""
import ctypes, json, os, re, shutil, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
sys.path.insert(0, SC)
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


import nowin  # noqa: E402
if os.name == "nt":
    check("父程序沒有主控台 → 加 CREATE_NO_WINDOW", nowin.add_flag({}, console=False).get("creationflags") == 0x08000000)
    check("父程序有主控台 → 不加（子程序沿用主控台、輸出不會不見）", "creationflags" not in nowin.add_flag({}, console=True))
    check("不覆蓋呼叫者自己的旗標", nowin.add_flag({"creationflags": 0x8}, console=False).get("creationflags") == 0x8)
    check("背景啟動旗標是 NEW_PROCESS_GROUP|NO_WINDOW、不含 DETACHED", nowin.BACKGROUND_FLAGS == 0x08000200
          and not nowin.BACKGROUND_FLAGS & 0x8)
else:
    check("非 Windows：add_flag 不動參數", nowin.add_flag({}) == {})

for fn in sorted(os.listdir(SC)):
    if fn.endswith(".py") and fn != "nowin.py":
        s = open(os.path.join(SC, fn), encoding="utf-8").read()
        if re.search(r"subprocess\.(run|Popen|call|check_output|check_call)\(", s):
            check("%s 有 import nowin" % fn, "import nowin" in s)

if os.name == "nt":
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    CONSOLE = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow"}

    def wins():
        s = {}

        def f(h, _):
            if u.IsWindowVisible(h):
                c = ctypes.create_unicode_buffer(128); u.GetClassNameW(h, c, 128); s[h] = c.value
            return True
        u.EnumWindows(CB(f), 0)
        return s

    home = tempfile.mkdtemp(prefix="ne-nowin-")
    fake = os.path.join(home, "fake_tool.py")
    open(fake, "w", encoding="utf-8").write(
        'import json,sys\nsub=sys.argv[1] if len(sys.argv)>1 else ""\n'
        'print(json.dumps({"ok":True,"data":{"snapshot_id":"splain1","tree":{"name":"x"}}}) if sub=="snapshot" '
        'else ("about:blank" if sys.argv[1:3]==["get","url"] else "ok"))\n')
    json.dump({}, open(os.path.join(home, "policy.json"), "w"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, NE_FAKE_AB_PY=fake, NE_FAKE_AD_PY=fake, PYTHONIOENCODING="utf-8")
    DETACHED = 0x00000008
    jobs = [[sys.executable, os.path.join(SC, "neb.py"), "--session", "t", "snapshot"] for _ in range(5)] + \
           [[sys.executable, os.path.join(SC, "ned.py"), "Notepad.exe", "snapshot"] for _ in range(5)]
    before = wins(); fg0 = u.GetForegroundWindow(); popped = []; fg_changed = False
    procs = []
    for cmd in jobs:   # 父程序沒有主控台（DETACHED_PROCESS）——就像排程工作、分離啟動的伺服器
        procs.append(subprocess.Popen(cmd, env=env, creationflags=DETACHED, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL))
        t_end = time.time() + 0.6
        while time.time() < t_end:
            cur = wins()
            popped += [c for h, c in cur.items() if h not in before and c in CONSOLE]
            if u.GetForegroundWindow() != fg0:
                fg_changed = True
            time.sleep(0.02)
    for p in procs:
        p.wait(timeout=30)
    t_end = time.time() + 1.0
    while time.time() < t_end:
        cur = wins(); popped += [c for h, c in cur.items() if h not in before and c in CONSOLE]; time.sleep(0.02)
    if "--expect-popups" in sys.argv:   # 正對照（CI；NE_NOWIN_DISABLE=1）：關掉修正時必須抓得到彈窗
        print("POSITIVE CONTROL " + ("PASS" if popped else "FAIL") + "：關掉修正時彈出 %d 個主控台視窗" % len(popped))
        sys.exit(0 if popped else 1)
    check("沒有主控台的父程序連續呼叫 neb×5、ned×5：沒有彈出任何終端機視窗", not popped, popped[:5])
    check("期間前景視窗沒有改變", not fg_changed)
    check("10 次呼叫都正常結束", all(p.returncode == 0 for p in procs), [p.returncode for p in procs])
    # 4 背景引擎（demo_runbook live 的做法）：nowin.popen_background 啟動的程序再連續產生「沒帶任何旗標」的子程序
    #   → 子孫繼承隱藏主控台，不彈窗、不搶前景（若用 DETACHED_PROCESS 啟動，這裡每個子程序都會彈一個視窗）
    chain = [sys.executable, "-c", "import subprocess,sys\nfor _ in range(6): subprocess.run([sys.executable,'-c','import time;time.sleep(0.3)'])"]
    before = wins(); fg0 = u.GetForegroundWindow(); popped4 = []; fg4 = False
    bp = nowin.popen_background(chain, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t_end = time.time() + 15
    while time.time() < t_end and bp.poll() is None:
        cur = wins(); popped4 += [c for h, c in cur.items() if h not in before and c in CONSOLE]
        fg4 = fg4 or u.GetForegroundWindow() != fg0
        time.sleep(0.02)
    check("背景程序（popen_background）連續產生 6 個子程序：沒有彈出終端機視窗", not popped4 and bp.poll() == 0, popped4[:5])
    check("背景程序期間前景視窗沒有改變", not fg4)
    shutil.rmtree(home, ignore_errors=True)

print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
