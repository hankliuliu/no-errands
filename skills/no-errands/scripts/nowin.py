#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows：子程序一律不開主控台視窗（純 stdlib）。

為什麼：agent 的工具呼叫常常跑在沒有主控台的程序裡；這時每啟動一個命令列程式（agent-browser、agent-desktop、
powershell、tasklist…），Windows 11 都會替它開一個終端機視窗、跑完就關——使用者看到的就是「視窗一直彈出」
（2026-11-22 使用者回報）。neb 每個指令就要呼叫兩次，所以特別明顯。

用法：在會開子程序的腳本最上面 `import nowin`。之後所有 subprocess.Popen／run／check_output 在沒有自己指定
creationflags 時，都會加上 CREATE_NO_WINDOW。這個旗標只影響主控台程式；有視窗的 GUI 程式照常顯示。
已經自己指定 creationflags 的呼叫（例如發件匣伺服器的 DETACHED_PROCESS）不動。
"""
import os
import subprocess

CREATE_NO_WINDOW = 0x08000000


def add_flag(kw):
    """回傳加了 CREATE_NO_WINDOW 的 kwargs（呼叫者自己給了非 0 的 creationflags 就不動）。"""
    if os.name == "nt" and not kw.get("creationflags"):
        kw = dict(kw, creationflags=CREATE_NO_WINDOW)
    return kw


# NE_NOWIN_DISABLE=1 只給 CI 的正對照用：關掉修正，證明 tests/test_nowin.py 真的抓得到彈窗
if os.name == "nt" and os.environ.get("NE_NOWIN_DISABLE") != "1" and not getattr(subprocess.Popen, "_ne_nowin", False):
    _orig_init = subprocess.Popen.__init__

    def _init(self, *args, **kw):
        _orig_init(self, *args, **add_flag(kw))

    subprocess.Popen.__init__ = _init
    subprocess.Popen._ne_nowin = True
