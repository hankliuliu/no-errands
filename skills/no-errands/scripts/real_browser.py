#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用使用者平常的瀏覽器（Windows 的 Chrome／Edge／Brave）已經登入的狀態，唯讀地讀一個網頁（wishes 第 2 項，Sprint I）。

只經由 `neb.py real read <網址> [--out 檔]` 呼叫：網址先過 neb 的同一套網域守門（敏感網域沒授權就拒絕），再到這裡。
做法（不用擴充功能、不拿 cookie、不接除錯埠）：
  0. 依序試 Chrome → Edge → Brave（裝了的才試）；某一個回報沒登入就換下一個，都沒登入才回報沒登入；
  1. 記下那個瀏覽器現有的頂層視窗；
  2. `<瀏覽器> --new-window <網址>` 開一個新視窗，只認「同一個程式、剛剛新出現的那一個」；不只一個就停手；
  3. 用 UI Automation 讀那個視窗裡的網頁文件（Document）：網址（ValuePattern）與全文（TextPattern），讀到兩次一樣才算載好；
     不讀分頁列、不看其他分頁與其他視窗；
  4. 網址的主機和要的不一樣、或路徑像登入頁 → 回報「這個瀏覽器沒登入」（呼叫端改走專用瀏覽器的登入流程）；
  5. 只對那個視窗送 WM_CLOSE；跳出任何確認框 → 對確認框送關閉（＝取消）、照實回報，不硬關；
  6. 確認那個瀏覽器原本的視窗一個都沒少。
不打字、不點頁面裡的東西。內容只回給呼叫的任務；紀錄（neb 的 ledger）只記主機與字數。"""
import ctypes, json, os, re, subprocess, sys, tempfile, time, urllib.parse

WM_CLOSE = 0x0010
LOGIN_PATH_RE = re.compile(r"(^|/)(login|signin|sign-in|sign_in|auth|sso|oauth2?|accounts?/login|session/new)(/|$|\?)", re.I)
LOGIN_HOST_RE = re.compile(r"^(login|signin|accounts|auth|sso|id)\.", re.I)

PS_READ = r"""
param([int64]$h, [int]$maxChars)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$ErrorActionPreference = 'Stop'
$out = @{ ok = $false }
try {
  $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$h)
  $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Document)
  $docs = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
  foreach ($d in $docs) {
    $url = ''
    try { $url = $d.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch {}
    if ($url -notmatch '^https?://') { continue }
    $txt = ''
    try { $txt = $d.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText($maxChars) } catch {}
    $out = @{ ok = $true; url = $url; text = $txt; title = $root.Current.Name }
    break
  }
} catch { $out = @{ ok = $false; error = $_.Exception.Message } }
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$out | ConvertTo-Json -Compress
"""


class Fail(Exception):
    pass


def _u32():
    return ctypes.windll.user32


# 依序試（使用者平常的登入狀態多半在 Chrome；Edge 常常只是系統預設開啟程式）：名稱、執行檔、安裝資料夾
BROWSERS = [("Chrome", "chrome.exe", ("Google", "Chrome")),
            ("Edge", "msedge.exe", ("Microsoft", "Edge")),
            ("Brave", "brave.exe", ("BraveSoftware", "Brave-Browser"))]


def browser_path(exe, sub):
    """瀏覽器執行檔的位置：App Paths 登錄 → 常見安裝位置。沒裝回 None。"""
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe) as k:
                    p = winreg.QueryValue(k, None)
                    if p and os.path.exists(p.strip('"')):
                        return p.strip('"')
            except OSError:
                pass
    except ImportError:
        pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        p = os.path.join(base or "", sub[0], sub[1], "Application", exe)
        if base and os.path.exists(p):
            return p
    return None


def installed_browsers():
    return [(name, exe, path) for name, exe, sub in BROWSERS for path in [browser_path(exe, sub)] if path]


def edge_path():
    return browser_path("msedge.exe", ("Microsoft", "Edge"))


def _exe_of(pid):
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_ulong(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        k32.CloseHandle(h)


def edge_windows():
    return browser_windows("msedge.exe")


def browser_windows(exe):
    """看得到的某個瀏覽器的頂層視窗（Chrome_WidgetWin_1、屬於該執行檔、有標題）。只看視窗本身，不讀分頁。"""
    from ctypes import wintypes
    u = _u32()
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        if not u.IsWindowVisible(h) or u.GetWindow(h, 4):    # GW_OWNER：有擁有者的是彈出框，不算
            return True
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, cls, 64)
        if cls.value != "Chrome_WidgetWin_1" or u.GetWindowTextLengthW(h) == 0:
            return True
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if _exe_of(pid.value) == exe:
            found.append(int(h))
        return True
    u.EnumWindows(proto(cb), 0)
    return found


def read_doc(hwnd, max_chars=400000, timeout=30):
    fd, ps1 = tempfile.mkstemp(suffix=".ps1")
    os.write(fd, PS_READ.encode("utf-8-sig"))
    os.close(fd)
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, str(hwnd), str(max_chars)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        try:
            return json.loads((p.stdout or "").strip().splitlines()[-1])
        except Exception:
            return {"ok": False, "error": "讀不到介面資訊（rc=%s）" % p.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "讀介面資訊逾時"}
    finally:
        try:
            os.remove(ps1)
        except OSError:
            pass


def looks_like_login(want_host, url):
    pu = urllib.parse.urlparse(url or "")
    host = (pu.hostname or "").lower()
    if host != want_host:
        return True, host
    return bool(LOGIN_PATH_RE.search(pu.path or "") or LOGIN_HOST_RE.match(host)), host


def close_window(hwnd, timeout=8):
    """只對自己開的那個視窗送 WM_CLOSE；跳出確認框就把確認框關掉（＝取消），不硬關。回 (關掉了嗎, 說明)。"""
    u = _u32()
    before_pop = u.GetWindow(hwnd, 6)                         # GW_ENABLEDPOPUP：關閉前就有的附屬視窗（翻譯泡泡等）不算確認框
    u.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    end = time.time() + timeout
    while time.time() < end:                                  # 先等它自己關（Edge 收視窗時會短暫出現附屬視窗，不能太早判斷）
        if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd):
            return True, None
        time.sleep(0.2)
    pop = u.GetWindow(hwnd, 6)
    if pop and pop != hwnd and pop != before_pop and u.IsWindowVisible(pop):
        u.PostMessageW(pop, WM_CLOSE, 0, 0)                   # 確認框的關閉＝取消
        return False, "關閉時跳出確認框，已按取消、沒有硬關；那個視窗還開著"
    return False, "送了關閉，%d 秒內視窗沒有關掉" % timeout


def read(url, wait_s=45, browsers=None):
    """依序用平常的瀏覽器試：第一個有登入（沒被導去登入頁）的就回它的結果；都沒登入回最後一個的「沒登入」。
    回 dict：ok、browser、tried、host、final_host、logged_in、chars、text（只給呼叫端）、title。全都失敗丟 Fail。"""
    if os.name != "nt":
        raise Fail("目前只支援 Windows 的 Chrome／Edge／Brave（其他平台還沒做）")
    cands = browsers if browsers is not None else installed_browsers()
    if not cands:
        raise Fail("找不到 Chrome、Edge 或 Brave")
    tried, last, errs = [], None, []
    for name, exe_name, path in cands:
        try:
            r = read_one(url, name, exe_name, path, wait_s)
        except Fail as e:
            tried.append({"browser": name, "error": str(e)})
            errs.append("%s：%s" % (name, e))
            continue
        tried.append({"browser": name, "logged_in": r.get("logged_in"), "closed": r.get("closed")})
        r["tried"] = tried
        if r.get("ok"):
            return r
        last = r
    if last is None:
        raise Fail("；".join(errs))
    last["tried"] = tried
    last["error"] = "平常的瀏覽器（%s）都沒登入這個網站：改用專用瀏覽器的登入流程" % "、".join(t["browser"] for t in tried)
    return last


def read_one(url, name, exe_name, exe, wait_s):
    want = (urllib.parse.urlparse(url).hostname or "").lower()
    before = set(browser_windows(exe_name))
    import nowin  # noqa: F401  子程序不開主控台視窗
    # --no-first-run --no-default-browser-check：沒跑過的設定檔（CI 的全新 runner）會卡在首次啟動畫面，
    # 文件讀不到、視窗也關不掉；平常已在跑的瀏覽器會把參數轉給既有實例、不受影響
    subprocess.Popen([exe, "--new-window", "--no-first-run", "--no-default-browser-check", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd, end = None, time.time() + 30
    while time.time() < end and hwnd is None:
        time.sleep(0.4)
        new = [h for h in browser_windows(exe_name) if h not in before]
        if len(new) > 1:
            raise Fail("同時出現 %d 個新的 %s 視窗，分不出哪個是自己開的：沒有讀也沒有關任何視窗" % (len(new), name))
        hwnd = new[0] if new else None
    if hwnd is None:
        raise Fail("30 秒內沒有出現新的 %s 視窗" % name)
    res = {"ok": True, "browser": name, "host": want}
    try:
        last, doc, end = None, None, time.time() + wait_s
        while time.time() < end:
            d = read_doc(hwnd)
            if d.get("ok") and (d.get("text") or "").strip():
                key = (d.get("url"), len(d.get("text") or ""))
                if key == last:                               # 讀兩次一樣＝載好了（第一次常常只有瀏覽器的框）
                    doc = d
                    break
                last = key
            time.sleep(1.0)
        if doc is None:
            raise Fail("%d 秒內讀不到穩定的頁面內容" % wait_s)
        login, final_host = looks_like_login(want, doc.get("url"))
        res.update(final_host=final_host, logged_in=not login, title=doc.get("title") or "",
                   chars=len(doc.get("text") or ""), text=doc.get("text") or "")
        if login:
            res.update(ok=False, text="", chars=0, error="這個瀏覽器沒登入：被導到 %s（像登入頁）；改用專用瀏覽器的登入流程" % final_host)
    finally:
        closed, why = close_window(hwnd)
        res["closed"] = closed
        if why:
            res["close_note"] = why
        missing = [h for h in before if not _u32().IsWindow(h)]
        res["other_windows_untouched"] = not missing
    return res
