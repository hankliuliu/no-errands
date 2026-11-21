#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""閒置閘門測試（Windows CI only：會注入一個 Shift 鍵來模擬使用者回來）。"""
import ctypes, ctypes.wintypes as wt, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "no-errands", "scripts"))
import idle  # noqa: E402

if "--i-am-ci" not in sys.argv:
    print("refusing: injects a key press; run only on CI (--i-am-ci)"); sys.exit(2)
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)))


class KI(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INP(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KI), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


s0 = idle.idle_seconds()
check("idle_seconds 回數字", isinstance(s0, float), s0)
w = idle.IdleWatch()
time.sleep(3)
check("沒有輸入時不算被打斷", not w.interrupted(), (idle.idle_seconds(), w.idle0))
for flags in (0, 2):  # VK_SHIFT down/up
    i = INP(type=1); i.ki = KI(0x10, 0, flags, 0, None)
    ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INP))
time.sleep(0.5)
check("注入輸入後閒置秒數歸零附近", idle.idle_seconds() < 2, idle.idle_seconds())
check("IdleWatch 偵測到被打斷", w.interrupted(), (idle.idle_seconds(), w.idle0))
ok, info = idle.wait_idle(min_s=2, timeout=10, poll=1)
check("wait_idle 等到閒置 2 秒", ok, info)
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
