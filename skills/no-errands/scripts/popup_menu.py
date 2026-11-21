#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""彈出選單操作（Windows；純 stdlib + PowerShell UIA）。

很多 app（Qt、WinForms ContextMenuStrip…）的彈出選單是**獨立的頂層視窗**，agent-desktop 的 list-windows
會把它濾掉，所以「點開選單後在樹裡找不到項目」。實測（CI，Qt6）：
  - 用 Win32 列舉同一個 pid 的可見頂層視窗，就找得到彈出選單的 HWND；
  - 對那個 HWND 做 UIA FromHandle，選單項目（名稱、位置）都讀得到；
  - 對該 HWND 投遞 WM_LBUTTONDOWN/UP（client 座標）就能點下項目——不移動實體游標、不是全域輸入注入；
    而 UIA Invoke 會被「項目開模態對話框」卡住，所以點擊用投遞訊息。

  popup_windows(pid, exclude)            → [{hwnd, class, rect}]
  popup_items(hwnd)                      → [{name, type, rect}]（UI 字串，不是內容）
  click_item(hwnd, item)                 → 投遞點擊到項目中心
  close_popup(hwnd)                      → 投遞 Esc 給該彈出視窗
"""
import ctypes, json, os, subprocess, tempfile, time

DUMP_PS = r"""
param([long]$Hwnd)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$items = @()
try {
  $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($e in $all) {
    $r = $e.Current.BoundingRectangle
    $items += @{ name = $e.Current.Name; type = ($e.Current.ControlType.ProgrammaticName -replace 'ControlType.',''); rect = @([int]$r.Left, [int]$r.Top, [int]$r.Right, [int]$r.Bottom); enabled = $e.Current.IsEnabled }
  }
  ConvertTo-Json -Compress -Depth 4 @{ ok = $true; items = $items }
} catch { ConvertTo-Json -Compress @{ ok = $false; error = $_.Exception.Message } }
"""


def _u32():
    return ctypes.windll.user32


def popup_windows(pid, exclude=()):
    import ctypes.wintypes as wt
    u32 = _u32()
    out = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        p = wt.DWORD(); u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(h) and h not in exclude:
            cls = ctypes.create_unicode_buffer(256); u32.GetClassNameW(h, cls, 256)
            r = wt.RECT(); u32.GetWindowRect(h, ctypes.byref(r))
            out.append({"hwnd": h, "class": cls.value, "rect": [r.left, r.top, r.right, r.bottom]})
        return True
    u32.EnumWindows(PROC(cb), 0)
    return out


def visible_hwnds(pid):
    return {w["hwnd"] for w in popup_windows(pid)}


def popup_items(hwnd, timeout=20):
    with tempfile.TemporaryDirectory() as td:
        ps1 = os.path.join(td, "dump.ps1")
        open(ps1, "w", encoding="ascii").write(DUMP_PS)
        try:
            p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, str(hwnd)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "UIA timeout"}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": (p.stderr or p.stdout)[-200:]}


def click_item(hwnd, item):
    """投遞滑鼠訊息到彈出視窗（client 座標）；不移動實體游標。"""
    import ctypes.wintypes as wt
    u32 = _u32()
    l, t, r, b = item["rect"]
    pt = wt.POINT((l + r) // 2, (t + b) // 2)
    u32.ScreenToClient(hwnd, ctypes.byref(pt))
    lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
    u32.PostMessageW(hwnd, 0x0200, 0, lp)          # WM_MOUSEMOVE（給該視窗，不是游標）
    time.sleep(0.05)
    u32.PostMessageW(hwnd, 0x0201, 0x0001, lp)     # WM_LBUTTONDOWN
    time.sleep(0.05)
    u32.PostMessageW(hwnd, 0x0202, 0, lp)          # WM_LBUTTONUP
    return {"client_xy": [pt.x, pt.y]}


def close_popup(hwnd):
    u32 = _u32()
    u32.PostMessageW(hwnd, 0x0100, 0x1B, 0)
    u32.PostMessageW(hwnd, 0x0101, 0x1B, 0xC0000000)
