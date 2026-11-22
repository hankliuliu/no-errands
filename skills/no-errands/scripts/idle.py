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
    if a[0] == "wait":
        mn = float(a[a.index("--min") + 1]) if "--min" in a else 180
        to = float(a[a.index("--timeout") + 1]) if "--timeout" in a else 3600
        ok, info = wait_idle(mn, to)
        print(json.dumps({"ok": ok, "info": info})); sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
