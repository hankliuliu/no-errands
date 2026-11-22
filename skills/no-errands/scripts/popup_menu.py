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
import nowin  # noqa: E402,F401  Windows：子程序不開主控台視窗（使用者回報視窗一直彈出）

# 座標一律用實體像素：UIA 回報的是實體像素，而沒宣告 DPI 感知的程序在縮放 150% 的螢幕上，
# GetWindowRect／ScreenToClient 會給「縮放後」的邏輯座標 → 投遞的點擊落在錯的位置
# （真機實測：點擊落在選單外，選單關掉、什麼都沒發生；換個版面就可能落在別的項目上）。
if os.name == "nt":
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

DUMP_PS = r"""
param([long]$Hwnd)
# stdout is a pipe read as UTF-8 by Python; PowerShell 5.1 would otherwise write the system code page (cp950 on zh-TW)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$items = @()
try {
  $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
  $rr = $root.Current.BoundingRectangle
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($e in $all) {
    $r = $e.Current.BoundingRectangle
    $sel = $null; $p = $null
    if ($e.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) { $sel = [bool]$p.Current.IsSelected }
    elseif ($e.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$p)) { $sel = ([string]$p.Current.ToggleState -eq 'On') }
    $items += @{ name = $e.Current.Name; type = ($e.Current.ControlType.ProgrammaticName -replace 'ControlType.',''); rect = @([int]$r.Left, [int]$r.Top, [int]$r.Right, [int]$r.Bottom); enabled = $e.Current.IsEnabled; sel = $sel }
  }
  ConvertTo-Json -Compress -Depth 4 @{ ok = $true; items = $items; root = @([int]$rr.Left, [int]$rr.Top, [int]$rr.Right, [int]$rr.Bottom) }
} catch { ConvertTo-Json -Compress @{ ok = $false; error = $_.Exception.Message } }
"""


def capture_window(hwnd, path):
    """單一視窗擷取（PrintWindow，PW_RENDERFULLCONTENT）：只取這個 hwnd 自己的內容，被擋住也一樣；不是螢幕擷取。
    存成 PNG（純 stdlib 編碼）。回 (寬, 高)。"""
    w, h, raw = grab_window(hwnd)
    write_png(path, w, h, raw)
    return w, h


def grab_window(hwnd):
    """單一視窗擷取的原始像素：回 (寬, 高, BGRA bytes，由上往下)。"""
    import ctypes.wintypes as wt
    u32, g32 = _u32(), ctypes.windll.gdi32
    r = wt.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        raise RuntimeError("window has no size")
    hdc = u32.GetDC(hwnd); mdc = g32.CreateCompatibleDC(hdc); bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    try:
        if not u32.PrintWindow(hwnd, mdc, 2):
            raise RuntimeError("PrintWindow failed")

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]
        bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    finally:
        g32.DeleteObject(bmp); g32.DeleteDC(mdc); u32.ReleaseDC(hwnd, hdc)
    return w, h, buf.raw


def write_png(path, w, h, raw):
    import struct, zlib
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        for px in range(y * w, (y + 1) * w):
            rows += bytes((raw[px * 4 + 2], raw[px * 4 + 1], raw[px * 4]))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    sig = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    png = sig + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")
    open(path, "wb").write(png)


DEEP_PS = r"""param([long]$Hwnd)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
# Structure-only deep scan: RawView walk; counts per control type; name length buckets;
# for document/list/pane/edit/text/window elements: whether Text/Value/LegacyIAccessible patterns exist and the total text length. Never outputs text.
$types = @{}; $buckets = @{ "0" = 0; "1-5" = 0; "6-20" = 0; "21-100" = 0; "101+" = 0 }
$pat = @{ text = 0; text_len = 0; value = 0; value_len = 0; legacy = 0; legacy_len = 0 }
$n = 0; $maxDepth = 0
try {
  $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
  $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
  $stack = New-Object System.Collections.Stack
  $stack.Push(@($root, 0))
  while ($stack.Count -gt 0 -and $n -lt 20000) {
    $pair = $stack.Pop(); $e = $pair[0]; $d = $pair[1]; $n++
    if ($d -gt $maxDepth) { $maxDepth = $d }
    $t = ($e.Current.ControlType.ProgrammaticName -replace 'ControlType.','')
    if ($types.ContainsKey($t)) { $types[$t]++ } else { $types[$t] = 1 }
    $L = ([string]$e.Current.Name).Length
    if ($L -eq 0) { $buckets["0"]++ } elseif ($L -le 5) { $buckets["1-5"]++ } elseif ($L -le 20) { $buckets["6-20"]++ } elseif ($L -le 100) { $buckets["21-100"]++ } else { $buckets["101+"]++ }
    if (@('Document','List','Pane','Edit','Text','Window','Custom','Group') -contains $t) {
      $p = $null
      if ($e.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$p)) { $pat.text++; try { $pat.text_len += $p.DocumentRange.GetText(-1).Length } catch {} }
      $p = $null
      if ($e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$p)) { $pat.value++; try { $pat.value_len += ([string]$p.Current.Value).Length } catch {} }
    }
    $c = $walker.GetFirstChild($e)
    $kids = @()
    while ($c -ne $null) { $kids += ,$c; $c = $walker.GetNextSibling($c) }
    for ($i = $kids.Count - 1; $i -ge 0; $i--) { $stack.Push(@($kids[$i], $d + 1)) }
  }
  ConvertTo-Json -Compress -Depth 4 @{ ok = $true; nodes = $n; max_depth = $maxDepth; types = $types; name_len = $buckets; patterns = $pat }
} catch { ConvertTo-Json -Compress @{ ok = $false; error = $_.Exception.GetType().Name; nodes = $n } }
"""


def child_windows(hwnd):
    """子視窗的類別與大小（不含標題文字）。"""
    import ctypes.wintypes as wt
    u32 = _u32(); out = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        cls = ctypes.create_unicode_buffer(256); u32.GetClassNameW(h, cls, 256)
        r = wt.RECT(); u32.GetWindowRect(h, ctypes.byref(r))
        out.append({"hwnd": h, "class": cls.value, "w": r.right - r.left, "h": r.bottom - r.top, "visible": bool(u32.IsWindowVisible(h)),
                    "rect": [r.left, r.top, r.right, r.bottom]})
        return True
    u32.EnumChildWindows(hwnd, PROC(cb), 0)
    return out


def deep_scan(hwnd, timeout=60):
    """RawView 全樹的結構統計（類型個數、名稱長度分布、Text/Value 模式與文字總長度）。不回傳任何文字。"""
    with tempfile.TemporaryDirectory() as td:
        ps1 = os.path.join(td, "deep.ps1")
        open(ps1, "w", encoding="ascii").write(DEEP_PS)
        try:
            p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, str(hwnd)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout"}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": "no json"}


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


def _deepest_child(hwnd, sx, sy):
    """螢幕座標 (sx, sy) 底下最深的可見子視窗（Qt 這類自繪選單沒有子視窗 → 回傳 hwnd 本身）。"""
    import ctypes.wintypes as wt
    u32 = _u32()
    cur = hwnd
    for _ in range(8):
        pt = wt.POINT(sx, sy)
        u32.ScreenToClient(cur, ctypes.byref(pt))
        nxt = u32.ChildWindowFromPointEx(cur, pt, 0x0001 | 0x0004)   # CWP_SKIPINVISIBLE | CWP_SKIPTRANSPARENT
        if not nxt or nxt == cur:
            break
        cur = nxt
    return cur


class CoordMismatch(Exception):
    pass


def click_item(hwnd, item, root=None):
    """點彈出選單的項目，不移動實體游標：
    項目是按鈕子視窗（Win32 / WinForms）→ 對它投遞 BM_CLICK；否則（Qt 等自繪）→ 對最深的視窗投遞滑鼠訊息。
    點之前確認座標系一致（UIA 的視窗外框 = Win32 的視窗外框）、項目中心在視窗裡、換算後的點在目標的客戶區裡；
    任何一項不成立就丟 CoordMismatch，不點。"""
    import ctypes.wintypes as wt
    u32 = _u32()
    l, t, r, b = item["rect"]
    sx, sy = (l + r) // 2, (t + b) // 2
    wr = wt.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(wr))
    win = [wr.left, wr.top, wr.right, wr.bottom]
    if root and any(abs(x - y) > 3 for x, y in zip(root, win)):
        raise CoordMismatch("UIA 與 Win32 的視窗外框不一致（%s vs %s）：座標系不同（DPI 縮放？），不點" % (root, win))
    if not (win[0] <= sx < win[2] and win[1] <= sy < win[3]):
        raise CoordMismatch("項目中心不在選單視窗裡（%s 不在 %s）：不點" % ([sx, sy], win))
    target = _deepest_child(hwnd, sx, sy)
    cls = ctypes.create_unicode_buffer(128); u32.GetClassNameW(target, cls, 128)
    if target != hwnd and "button" in cls.value.lower():
        u32.PostMessageW(target, 0x00F5, 0, 0)     # BM_CLICK
        return {"via": "BM_CLICK", "class": cls.value}
    pt = wt.POINT(sx, sy)
    u32.ScreenToClient(target, ctypes.byref(pt))
    cr = wt.RECT(); u32.GetClientRect(target, ctypes.byref(cr))
    if not (0 <= pt.x < cr.right and 0 <= pt.y < cr.bottom):
        raise CoordMismatch("換算後的點不在目標的客戶區裡（%s，客戶區 %dx%d）：不點" % ([pt.x, pt.y], cr.right, cr.bottom))
    lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
    u32.PostMessageW(target, 0x0200, 0, lp)          # WM_MOUSEMOVE（給該視窗，不是游標）
    time.sleep(0.05)
    u32.PostMessageW(target, 0x0201, 0x0001, lp)     # WM_LBUTTONDOWN
    time.sleep(0.05)
    u32.PostMessageW(target, 0x0202, 0, lp)          # WM_LBUTTONUP
    return {"via": "posted-mouse", "client_xy": [pt.x, pt.y], "class": cls.value}


def close_popup(hwnd):
    u32 = _u32()
    u32.PostMessageW(hwnd, 0x0100, 0x1B, 0)
    u32.PostMessageW(hwnd, 0x0101, 0x1B, 0xC0000000)


LISTS_PS = r"""
param([long]$Hwnd)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
try {
  $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
  $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::List)
  $out = @()
  foreach ($l in $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)) {
    $r = $l.Current.BoundingRectangle
    $kids = $l.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    $btn = 0; foreach ($k in $kids) { if ($k.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button) { $btn++ } }
    $out += @{ rect = @([int]$r.Left, [int]$r.Top, [int]$r.Right, [int]$r.Bottom); children = $kids.Count; buttons = $btn }
  }
  ConvertTo-Json -Compress -Depth 4 @{ ok = $true; lists = $out }
} catch { ConvertTo-Json -Compress @{ ok = $false; error = $_.Exception.Message } }
"""


LIST_ERR = {"last": None}


def list_rects(hwnd, timeout=30):
    """視窗裡每個 UIA List 的位置、子項數、其中按鈕數（不回任何名稱）。失敗原因（不含內容）記在 LIST_ERR。"""
    LIST_ERR["last"] = None
    with tempfile.TemporaryDirectory() as td:
        ps1 = os.path.join(td, "lists.ps1")
        open(ps1, "w", encoding="ascii").write(LISTS_PS)
        try:
            p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, str(hwnd)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            LIST_ERR["last"] = "timeout"
            return []
    try:
        d = json.loads(p.stdout.strip().splitlines()[-1])
        if not d.get("ok"):
            LIST_ERR["last"] = "uia: " + str(d.get("error"))[:120]
        ls = d.get("lists") or []
        return [ls] if isinstance(ls, dict) else ls
    except Exception as e:
        LIST_ERR["last"] = "parse: %s (stdout %d bytes)" % (type(e).__name__, len(p.stdout or ""))
        return []


def wheel_up(hwnd, sx, sy, notches=10):
    """對視窗投遞 WM_MOUSEWHEEL（往上）。座標是螢幕座標，Qt 依它分派給底下的元件；實體游標不動。"""
    return wheel(hwnd, sx, sy, notches)


def row_keys(raw, w, h, x0, x1, y0, y1):
    """視窗擷取裡某個矩形（相對視窗左上角）每一列像素的雜湊；整列同色的回 None（空白列不拿來比對位移）。"""
    x0, x1, y0, y1 = max(0, x0), min(w, x1), max(0, y0), min(h, y1)
    out = []
    for y in range(y0, y1):
        row = raw[(y * w + x0) * 4:(y * w + x1) * 4]
        out.append(None if row[:4] * (len(row) // 4) == row else hash(row))
    return out


def vertical_shift(a, b, min_rows=20):
    """往下捲之後內容往上移：b 的第 y 列 = a 的第 y+s 列。回 (s, 相符比例)；找不到可信的位移回 (None, 0)。
    只拿非空白列比；重疊少於 min_rows 列的位移不考慮。完全相同回 (0, 1.0)。"""
    n = min(len(a), len(b))
    if n == 0:
        return None, 0.0
    if a[:n] == b[:n]:
        return 0, 1.0
    best = (None, 0.0)
    for s in range(1, n - min_rows):
        tot = hit = 0
        for y in range(n - s):
            ka, kb = a[y + s], b[y]
            if ka is None and kb is None:
                continue
            tot += 1
            hit += ka == kb
        if tot >= min_rows:
            r = hit / tot
            if r > best[1] + 1e-9:
                best = (s, r)
    return best if best[1] >= 0.9 else (None, best[1])


def wheel(hwnd, sx, sy, notches):
    """對視窗投遞 WM_MOUSEWHEEL；notches 正＝往上、負＝往下。座標是螢幕座標；實體游標不動。"""
    import ctypes.wintypes as wt
    u32 = _u32()
    wr = wt.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(wr))
    if not (wr.left <= sx < wr.right and wr.top <= sy < wr.bottom):
        raise CoordMismatch("滾輪座標不在視窗裡（%s 不在 %s）" % ([sx, sy], [wr.left, wr.top, wr.right, wr.bottom]))
    wparam = ((120 * notches) & 0xFFFF) << 16
    lparam = ((sy & 0xFFFF) << 16) | (sx & 0xFFFF)
    # 明確包成 WPARAM／LPARAM：往下捲時 wparam 高位是負數的補數（> 2^31），交給 ctypes 預設的 int 轉換會溢位
    return bool(u32.PostMessageW(wt.HWND(hwnd), 0x020A, wt.WPARAM(wparam), wt.LPARAM(lparam)))
