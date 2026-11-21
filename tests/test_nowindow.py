#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""背景行程不開視窗、不搶前景（Windows；會動到畫面，只在 CI 跑：需要 --i-am-ci）。

事故：分離啟動（DETACHED_PROCESS）的背景引擎沒有主控台，它每啟動一個 python／agent-browser 子行程，
Windows 就配一個新的主控台——預設終端機是 Windows Terminal 時，每次都是一個看得見、會搶前景的視窗。
修法：policy.popen_background（NEW_PROCESS_GROUP|CREATE_NO_WINDOW）＋ policy.run（CREATE_NO_WINDOW）。

  1 正對照：用舊的 DETACHED_PROCESS 啟動一個會連續產生子行程的行程 → 偵測器應該看到新視窗（證明偵測器有效）
  2 修好後：用 policy.popen_background 啟動同樣的行程（子行程用 policy.run）→ 沒有新的可見視窗、前景不變
"""
import ctypes, os, subprocess, sys, tempfile, time
from ctypes import wintypes

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
sys.path.insert(0, SC)
R = []

CHILD = r'''
import os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import policy
use_helper = sys.argv[2] == "fixed"
for i in range(8):
    cmd = [sys.executable, "-c", "import time; time.sleep(0.4)"]
    if use_helper:
        policy.run(cmd)
    else:
        subprocess.run(cmd)
'''


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]), flush=True)


def visible_windows():
    u = ctypes.windll.user32
    out = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h):
            r = wintypes.RECT(); u.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 50 and r.bottom - r.top > 50:
                out.add(h)
        return True
    u.EnumWindows(cb, 0)
    return out


def watch(proc, seconds=20):
    """行程執行期間每 50ms 列舉一次：回 (新出現的可見視窗數, 前景變化次數)。"""
    u = ctypes.windll.user32
    base = visible_windows()
    fg0 = u.GetForegroundWindow()
    new, fg_changes, last_fg = set(), 0, fg0
    t0 = time.time()
    while time.time() - t0 < seconds:
        now = visible_windows()
        new |= (now - base)
        fg = u.GetForegroundWindow()
        if fg != last_fg:
            fg_changes += 1
            last_fg = fg
        if proc.poll() is not None and time.time() - t0 > 1:
            break
        time.sleep(0.05)
    return len(new), fg_changes


def main():
    if os.name != "nt":
        print("SKIP (Windows only)"); return
    if "--i-am-ci" not in sys.argv:
        print("SKIP：這支會在畫面上開關視窗，只在 CI 跑（加 --i-am-ci）"); return
    import policy
    td = tempfile.mkdtemp(prefix="ne-nowin-")
    child = os.path.join(td, "child.py"); open(child, "w", encoding="utf-8").write(CHILD)
    # 1 正對照：舊做法
    p = subprocess.Popen([sys.executable, child, SC, "old"], creationflags=0x00000008 | 0x00000200,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    n_old, fg_old = watch(p)
    check("正對照：DETACHED 父行程的子行程會冒出視窗（偵測器有效）", n_old > 0, (n_old, fg_old))
    time.sleep(1)
    # 2 修好後
    p = policy.popen_background([sys.executable, child, SC, "fixed"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    n_new, fg_new = watch(p)
    check("修好後：背景行程連續產生 8 個子行程，沒有新的可見視窗", n_new == 0, n_new)
    check("修好後：前景沒有被搶", fg_new == 0, fg_new)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (len(R) - sum(R), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
