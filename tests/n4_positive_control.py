#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N4 正對照（**只在 CI 的 Windows runner 跑**——它會刻意搶焦點、模擬鍵盤與滑鼠；
有人在用的電腦上不要跑）。

確認 sampler.ps1 真的抓得到干擾：
  1. 開一個一般（會啟用）的 NE-POSCTRL 視窗，SetForegroundWindow 拉到前景
  2. SendInput 打 30 個字到有焦點的文字框 → 視窗收到的字串要完全一致
  3. SetCursorPos 移動游標
  4. sampler 必須記錄到：前景變成 NE-POSCTRL、游標位置改變
用法：python tests/n4_positive_control.py --i-am-ci
"""
import ctypes, ctypes.wintypes as wt, json, os, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCD = os.path.join(REPO, "scenarios", "fake-chat")

WIN = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = 'NE-POSCTRL'; $f.Width = 400; $f.Height = 150
$tb = New-Object System.Windows.Forms.TextBox; $tb.Width = 360; $tb.Left = 10; $tb.Top = 10
$f.Controls.Add($tb)
$f.Add_Shown({ $tb.Focus() })
$f.Add_FormClosing({ [IO.File]::WriteAllText('%OUT%', $tb.Text) })
[System.Windows.Forms.Application]::Run($f)
"""


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def send_text(s):
    u = ctypes.windll.user32
    for ch in s:
        for flags in (0x0004, 0x0004 | 0x0002):  # KEYEVENTF_UNICODE, | KEYUP
            inp = INPUT(type=1)
            inp.ki = KEYBDINPUT(0, ord(ch), flags, 0, None)
            u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.03)


def main():
    if "--i-am-ci" not in sys.argv:
        print("refusing: this test steals focus and injects input; run only on a CI runner (--i-am-ci)")
        sys.exit(2)
    u = ctypes.windll.user32
    tmp = tempfile.mkdtemp(prefix="ne-posctrl-")
    out = os.path.join(tmp, "typed.txt")
    samples = os.path.join(tmp, "samples.jsonl")
    ps1 = os.path.join(tmp, "win.ps1")
    open(ps1, "w", encoding="ascii").write(WIN.replace("%OUT%", out))
    smp = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            os.path.join(FCD, "sampler.ps1"), "-Out", samples, "-Seconds", "25", "-IntervalMs", "50"])
    time.sleep(1.5)
    win = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", ps1])
    hwnd = 0
    for _ in range(50):
        time.sleep(0.2)
        hwnd = u.FindWindowW(None, "NE-POSCTRL")
        if hwnd:
            break
    u.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    text = "positivecontrol0123456789abcd"
    send_text(text)
    pt = wt.POINT(); u.GetCursorPos(ctypes.byref(pt))
    u.SetCursorPos(pt.x + 37, pt.y + 23)
    time.sleep(1.0)
    u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    win.wait(timeout=20)
    smp.wait(timeout=40)
    got = open(out, encoding="utf-8-sig").read() if os.path.exists(out) else ""
    ss = [json.loads(l) for l in open(samples, encoding="utf-8") if l.strip()]
    res = {"typed_identical": got == text, "typed_len": len(got),
           "sampler_saw_our_foreground": any(s["ours"] for s in ss),
           "sampler_saw_cursor_move": len({(s["x"], s["y"]) for s in ss}) > 1,
           "samples": len(ss)}
    print(json.dumps(res))
    ok = res["typed_identical"] and res["sampler_saw_our_foreground"] and res["sampler_saw_cursor_move"]
    print("POSITIVE CONTROL " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
