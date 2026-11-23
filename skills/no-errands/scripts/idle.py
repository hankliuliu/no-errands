#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""使用者閒置偵測（純 stdlib）——讓「不打擾使用者」與「要把事做完」並存。

需要畫面或可能搶前景的步驟，只在使用者閒置時做；做的途中一偵測到使用者輸入就中止並交還。

  idle.py seconds                 # 使用者多久沒碰鍵盤滑鼠（秒）；不支援的平台回 null
  idle.py wait --min 180 --timeout 3600

平台：Windows GetLastInputInfo；macOS `ioreg -c IOHIDSystem` 的 HIDIdleTime；
Linux 有 `xprintidle` 才支援（X11），否則回 None → 呼叫端改為詢問使用者。
"""
import json, os, re, shutil, subprocess, sys, time
import nowin  # noqa: E402,F401  Windows：子程序不開主控台視窗（使用者回報視窗一直彈出）


def idle_seconds():
    """回傳使用者閒置秒數（float）；無法判斷時回 None。"""
    if os.name == "nt":
        import ctypes
        import ctypes.wintypes as wt

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
        lii = LASTINPUTINFO(); lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - lii.dwTime) & 0xFFFFFFFF) / 1000.0
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
            return int(m.group(1)) / 1e9 if m else None
        except Exception:
            return None
    if shutil.which("xprintidle") and os.environ.get("DISPLAY"):
        try:
            return int(subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=5).stdout.strip()) / 1000.0
        except Exception:
            return None
    return None


def wait_idle(min_s=180, timeout=3600, poll=5):
    """等到使用者閒置滿 min_s 秒。回 (ok, info)。不支援 → (False, 'unsupported')。"""
    t0 = time.time()
    while True:
        s = idle_seconds()
        if s is None:
            return False, "unsupported"
        if s >= min_s:
            return True, {"idle_s": round(s, 1), "waited_s": round(time.time() - t0, 1)}
        if time.time() - t0 >= timeout:
            return False, {"timeout_s": timeout, "last_idle_s": round(s, 1)}
        time.sleep(min(poll, max(1, min_s - s)))


def session_lock_state():
    """這個工作階段是不是鎖定：True＝鎖定、False＝沒鎖、None＝判斷不了。
    WTSQuerySessionInformation(WTSSessionInfoEx) 的 SessionFlags（Windows 8 起：0＝LOCK、1＝UNLOCK）。
    WTSINFOEXW 的 union 在 64 位元對齊到第 8 個位元組：SessionId@8、SessionState@12、SessionFlags@16。"""
    import ctypes, ctypes.wintypes as wt, struct
    k, w = ctypes.windll.kernel32, ctypes.windll.wtsapi32
    sid = wt.DWORD()
    if not k.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
        return None
    buf, n = ctypes.c_void_p(), wt.DWORD()
    if not w.WTSQuerySessionInformationW(None, sid.value, 25, ctypes.byref(buf), ctypes.byref(n)):
        return None
    try:
        if n.value < 20:
            return None
        lvl, _pad, s_id, _state, flags = struct.unpack("<IIIIi", ctypes.string_at(buf, 20))
        if lvl != 1 or s_id != sid.value:
            return None
        return True if flags == 0 else False if flags == 1 else None
    finally:
        w.WTSFreeMemory(buf)


def _foreground_is_lockapp():
    import ctypes, ctypes.wintypes as wt
    u32, k = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = u32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wt.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k.OpenProcess.restype = wt.HANDLE
    h = k.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wt.DWORD(520)
        if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return False
        return os.path.basename(buf.value).lower() == "lockapp.exe"
    finally:
        k.CloseHandle(h)


def screen_unavailable():
    """畫面能不能用：電腦鎖定、或螢幕保護程式正在跑 → 回原因字串；可以用 → None。
    鎖定時「閒置很久」沒有意義，擷取到的也不是真的畫面（2026-11-23：螢幕閒置 20 分鐘後關閉並鎖定，之後的結果都不算數）。
    Windows 10 起的鎖定畫面（LockApp）跑在使用者原本的桌面上，所以「輸入桌面」看不出鎖定；以工作階段的鎖定旗標為準，
    讀不到時看前景視窗是不是 LockApp。判斷不了（非 Windows、呼叫失敗）回 None，交給其他檢查。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        u32 = ctypes.windll.user32
        locked = session_lock_state()
        if locked:
            return "電腦目前鎖定中"
        if locked is None and _foreground_is_lockapp():
            return "電腦目前鎖定中（前景是鎖定畫面）"
        u32.OpenInputDesktop.restype = ctypes.c_void_p
        h = u32.OpenInputDesktop(0, False, 0x0100)   # DESKTOP_SWITCHDESKTOP：輸入安全桌面（輸入密碼、UAC）時打不開
        if not h:
            return "電腦目前在安全桌面（鎖定或輸入密碼中）"
        u32.CloseDesktop(ctypes.c_void_p(h))
        running = ctypes.c_int(0)
        if u32.SystemParametersInfoW(0x0072, 0, ctypes.byref(running), 0) and running.value:   # SPI_GETSCREENSAVERRUNNING
            return "螢幕保護程式正在執行"
    except Exception:
        return None
    return None


OWN_PAD_S = 0.3   # 我們自己的動作結束後，再多算這麼久（系統記錄輸入時間的誤差）


class IdleWatch:
    """操作期間監看：使用者一有輸入（閒置秒數比經過時間短）就算被打斷。
    例外只有一種：最新那筆輸入的時間點，落在「我們自己的動作正在執行」的時段裡。實測（2026-11-23，真的 LINE）：
    agent-desktop 的語意點擊在點 Qt 的清單項目時，會送出一筆系統層級的合成輸入（游標沒動、Alt 沒按，但最後輸入
    時間被更新），監看就把自己的點擊當成使用者回來了。這段時間以外的輸入照常算使用者；監看沒有放寬，也沒拉長容忍。
    代價照實說：使用者剛好在我們那一個動作執行的那幾秒內動了一下、之後就沒再動，這一下會被忽略。"""

    def __init__(self):
        self.t0 = time.time()
        self.idle0 = idle_seconds()
        self.own = []   # [[開始, 結束或 None（還在跑）], ...]

    def own_begin(self):
        self.own.append([time.time(), None])
        return len(self.own) - 1

    def own_end(self, i):
        if i is not None and 0 <= i < len(self.own):
            self.own[i][1] = time.time() + OWN_PAD_S

    def _is_own(self, ts):
        now = time.time()
        return any(a - 0.05 <= ts <= (b if b is not None else now + OWN_PAD_S) for a, b in self.own)

    def interrupted(self, slack=1.5):
        """沒人碰的話，閒置秒數應該 = 開始時的閒置秒數 + 經過時間；明顯比較小就是有人動過——
        除非最新那筆輸入就發生在我們自己的動作執行期間。"""
        s = idle_seconds()
        if s is None or self.idle0 is None:
            return False
        if not (s + slack < self.idle0 + (time.time() - self.t0)):
            return False
        return not self._is_own(time.time() - s)


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    if a[0] == "seconds":
        print(json.dumps({"idle_s": idle_seconds()})); sys.exit(0)
    if a[0] == "screen":
        why = screen_unavailable()
        print(json.dumps({"available": why is None, "why": why}, ensure_ascii=False)); sys.exit(0 if why is None else 1)
    if a[0] == "wait":
        mn = float(a[a.index("--min") + 1]) if "--min" in a else 180
        to = float(a[a.index("--timeout") + 1]) if "--timeout" in a else 3600
        ok, info = wait_idle(mn, to)
        print(json.dumps({"ok": ok, "info": info})); sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
