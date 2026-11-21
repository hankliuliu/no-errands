#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows：子程序不彈出主控台視窗（純 stdlib）。這是全專案唯一的機制。

為什麼：沒有主控台的程序（分離啟動的伺服器、背景測試、排程工作）每啟動一個主控台程式（agent-browser、agent-desktop、
powershell、tasklist…），Windows 都會給它一個新的主控台；預設終端機是 Windows Terminal 的機器上，那就是一個看得見、
會搶前景的視窗（2026-11-22 使用者回報「視窗一直彈出」，實測 20 秒 15 個）。

規則：
1. `import nowin` 之後，所有 subprocess 在**父程序沒有主控台**、且呼叫者沒有自己指定 creationflags 時，加上 CREATE_NO_WINDOW。
   父程序有主控台時不加：子程序直接沿用父程序的主控台，本來就不會彈窗；加了反而會讓沒擷取的輸出跑到新的隱藏主控台而不見。
2. 獨立存活的背景程序用 `popen_background()`：Windows 上是 NEW_PROCESS_GROUP|CREATE_NO_WINDOW——**不要用 DETACHED_PROCESS**。
   DETACHED 讓程序本身沒有主控台（而且 DETACHED 時 NO_WINDOW 不生效），之後它的子孫只要有一個沒帶旗標就會彈窗；
   NO_WINDOW 則給它一個「有主控台但沒有視窗」的主控台，子孫會繼承這個隱藏主控台（另一位執行手實測發現）。
"""
import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
BACKGROUND_FLAGS = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def has_console():
    """目前程序有沒有接上主控台（包括隱藏的）。沒有的話，它的主控台子程序會彈出新視窗。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        return ctypes.windll.kernel32.GetConsoleCP() != 0
    except Exception:
        return False


def add_flag(kw, console=None):
    """父程序沒有主控台、且呼叫者沒給 creationflags 時，加上 CREATE_NO_WINDOW。"""
    if os.name == "nt" and not kw.get("creationflags"):
        if not (has_console() if console is None else console):
            kw = dict(kw, creationflags=CREATE_NO_WINDOW)
    return kw


def popen_background(cmd, **kw):
    """獨立存活的背景程序（例如發件匣伺服器）：Windows 用 NEW_PROCESS_GROUP|NO_WINDOW（不用 DETACHED），其他平台開新 session。"""
    if os.name == "nt":
        kw["creationflags"] = BACKGROUND_FLAGS
    else:
        kw.setdefault("start_new_session", True)
    return subprocess.Popen(cmd, **kw)


# NE_NOWIN_DISABLE=1 只給 CI 的正對照用：關掉修正，證明 tests/test_nowin.py 真的抓得到彈窗
if os.name == "nt" and os.environ.get("NE_NOWIN_DISABLE") != "1" and not getattr(subprocess.Popen, "_ne_nowin", False):
    _orig_init = subprocess.Popen.__init__

    def _init(self, *args, **kw):
        _orig_init(self, *args, **add_flag(kw))

    subprocess.Popen.__init__ = _init
    subprocess.Popen._ne_nowin = True
