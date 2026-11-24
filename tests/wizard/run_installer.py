#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B3(i) GUI 安裝精靈從頭點到完成（CI Windows runner only；會安裝軟體、開視窗）。

  python tests/wizard/run_installer.py --ad <agent-desktop.exe> --installer npp|winmerge --out wizard-npp.json --i-am-ci

每一步先用 agent-desktop 語意點擊；同一頁按 3 次不換頁 → (b) 直接對按鈕 HWND 送 BM_CLICK →
仍不換頁 → (d) 實體點擊（--headed，只在 CI；在使用者機器上要走閒置閘門或事前告知）。
每種手段有沒有用都記下來，作為 playbook 的依據。完成後驗證程式檔存在，再靜默移除。
"""
import ctypes, ctypes.wintypes as wt, hashlib, json, os, re, subprocess, sys, tempfile, time, urllib.request

INSTALLERS = {
    "npp": {"framework": "NSIS",
            "url": "https://github.com/notepad-plus-plus/notepad-plus-plus/releases/download/v8.6.9/npp.8.6.9.Installer.x64.exe",
            "target": r"C:\Program Files\Notepad++\notepad++.exe",
            "uninstall": [r"C:\Program Files\Notepad++\uninstall.exe", "/S"]},
    "winmerge": {"framework": "Inno Setup",
                 "url": "https://github.com/WinMerge/winmerge/releases/download/v2.16.44/WinMerge-2.16.44-x64-Setup.exe",
                 "target": r"C:\Program Files\WinMerge\WinMergeU.exe",
                 "uninstall": [r"C:\Program Files\WinMerge\unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES"]},
    # 只裝給目前使用者（Inno PrivilegesRequired=lowest）：同一套 GUI 精靈、不需要管理員權限、不跳 UAC。
    # 要管理員的安裝會跳 UAC，而 UAC 畫在安全桌面上、任何程式注入的滑鼠鍵盤都到不了（Windows 的設計）——
    # 在使用者自己的電腦上要「不用他動手」把精靈從頭點到完成，走這種不升權的安裝檔。
    "winmerge-user": {"framework": "Inno Setup (per-user, no UAC)",
                      "url": "https://github.com/WinMerge/winmerge/releases/download/v2.16.44/WinMerge-2.16.44-x64-PerUser-Setup.exe",
                      "target": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "WinMerge", "WinMergeU.exe"),
                      "uninstall": [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "WinMerge", "unins000.exe"),
                                    "/VERYSILENT", "/SUPPRESSMSGBOXES"]},
}
NEXT_NAMES = {"OK", "Next >", "Next", "I Agree", "Install", "下一步(N) >", "我接受(I)", "安裝(I)",
              "Install for me only (recommended)", "Install for me only", "Install for all users"}
FINISH_NAMES = {"Finish", "完成(F)"}
ACCEPT_HINTS = ("I accept", "我接受", "我同意")
RUN_HINTS = ("Run", "Launch", "View", "執行")
u32 = ctypes.windll.user32


def arg(k, d=None):
    return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d


AD = arg("--ad")
LOG = {"steps": [], "methods_used": []}


def ad(*a):
    try:
        p = subprocess.run([AD] + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return json.loads(p.stdout)
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


def walk(n):
    yield n
    for c in n.get("children") or []:
        yield from walk(c)


def clean(s):
    # 連續空白壓成一個：繁中 Inno 的按鈕是「下一步(N)  >」（兩個空格），英文是「Next >」——不壓的話繁中系統上永遠對不到
    return re.sub(r"\s+", " ", (s or "").replace("&", "")).strip()


def _text(h):
    t = ctypes.create_unicode_buffer(256); u32.GetWindowTextW(h, t, 256); return t.value


def _cls(h):
    c = ctypes.create_unicode_buffer(64); u32.GetClassNameW(h, c, 64); return c.value


ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def top_windows(pids):
    """該程序群的可見頂層視窗（含 list-windows 濾掉的 owned 視窗，例如 Inno 的精靈）。"""
    out = []

    def top(h, _):
        p = wt.DWORD(); u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value in pids and u32.IsWindowVisible(h):
            out.append({"hwnd": h, "title": _text(h), "class": _cls(h), "pid": p.value,
                        "owner": bool(u32.GetWindow(h, 4))})
        return True
    cb = ENUM(top)
    u32.EnumWindows(cb, 0)
    return out


def children(hwnd):
    found = []

    def child(h, _):
        if u32.IsWindowVisible(h):
            found.append({"hwnd": h, "name": clean(_text(h)), "class": _cls(h), "enabled": bool(u32.IsWindowEnabled(h))})
        return True
    cb = ENUM(child)
    u32.EnumChildWindows(hwnd, cb, 0)
    return found


def buttons_of(hwnd):
    return [c for c in children(hwnd) if "button" in c["class"].lower()]


def bm_click(pid, name):
    """在該程序所有頂層視窗的子視窗裡找文字相符的按鈕（Button / TNewButton…），直接投遞 BM_CLICK。"""
    hs = [b["hwnd"] for w in top_windows({pid}) for b in buttons_of(w["hwnd"]) if b["name"] == clean(name)]
    for h in hs:
        u32.PostMessageW(h, 0x00F5, 0, 0)   # BM_CLICK
    return len(hs)


def wm_command(h):
    """真的點一下按鈕，最後就是父視窗收到 WM_COMMAND(BN_CLICKED)。直接投遞它：不需要前景、不注入輸入。
    BM_CLICK 在對話框不是作用中視窗時可能失效（MSDN 註明），WM_COMMAND 不受影響。"""
    parent = u32.GetParent(h)
    cid = u32.GetDlgCtrlID(h)
    return bool(u32.PostMessageW(parent, 0x0111, cid & 0xFFFF, h))


def wm_command_by_name(pid, name):
    hs = [b["hwnd"] for w in top_windows({pid}) for b in buttons_of(w["hwnd"]) if b["name"] == clean(name)]
    return sum(wm_command(h) for h in hs)


def checked(h):
    return u32.SendMessageW(h, 0x00F0, 0, 0) == 1   # BM_GETCHECK


def child_pids(pid):
    """安裝程式常會再起一個程序（Inno 的 setup.tmp、NSIS 自我提權）→ 一併納入。"""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
                             capture_output=True, text=True, timeout=30).stdout.split()
        pairs = list(zip(map(int, out[0::2]), map(int, out[1::2])))
    except Exception:
        return {pid}
    res, grew = {pid}, True
    while grew:
        grew = False
        for c, par in pairs:
            if par in res and c not in res:
                res.add(c); grew = True
    return res


def page_key(nodes):
    return tuple(sorted(clean(n.get("name")) for n in nodes if n.get("role") in ("statictext", "text") and n.get("name"))[:6])


def main():
    if "--i-am-ci" not in sys.argv:
        print("refusing: installs software and opens windows; CI only (--i-am-ci)"); sys.exit(2)
    which = arg("--installer", "npp")
    spec = INSTALLERS[which]
    LOG.update(installer=which, framework=spec["framework"])
    td = tempfile.mkdtemp(prefix="wiz-")
    exe = os.path.join(td, "setup.exe")
    try:
        urllib.request.urlretrieve(spec["url"], exe)
    except Exception as e:
        LOG.update(ok=False, error="download failed: %s" % e)
        print(json.dumps(LOG, ensure_ascii=False)); print("FAILED 1/1"); sys.exit(1)
    LOG["sha256"] = hashlib.sha256(open(exe, "rb").read()).hexdigest()
    proc = subprocess.Popen([exe])
    t0, finished, stall, last_key, escalate = time.time(), False, 0, None, 0
    while time.time() - t0 < 360 and not finished:
        time.sleep(1.2)
        wins = [w for w in (ad("list-windows").get("data") or [])
                if w.get("pid") == proc.pid or "Setup" in (w.get("title") or "") or "安裝" in (w.get("title") or "")]
        if not wins:
            # list-windows 看不到（例如 Inno 精靈是隱藏應用程式視窗擁有的 owned 視窗）→ Win32 列舉＋WM_COMMAND
            pids = child_pids(proc.pid)
            tops = [t for t in top_windows(pids) if t["title"]]
            if tops and not LOG.get("win32_seen"):
                LOG["win32_seen"] = [{k: t[k] for k in ("title", "class", "owner")} for t in tops][:6]
            if not tops and time.time() - t0 > 30 and "diag_listwindows" not in LOG:
                LOG["diag_listwindows"] = [(w.get("title"), w.get("pid")) for w in (ad("list-windows").get("data") or [])][:10]
                LOG["diag_pids"] = sorted(pids)
            for t in sorted(tops, key=lambda x: len(buttons_of(x["hwnd"])), reverse=True):
                kids = children(t["hwnd"])
                bs = [c for c in kids if "button" in c["class"].lower()]
                if not bs:
                    continue
                key = (t["title"], tuple(sorted(c["name"] for c in kids))[:16])
                stall = stall + 1 if key == last_key else 0
                last_key = key
                for b in bs:   # 授權頁「我接受」
                    if any(hh in b["name"] for hh in ACCEPT_HINTS) and not checked(b["hwnd"]):
                        wm_command(b["hwnd"]); LOG["steps"].append({"win32": "accept", "name": b["name"]})
                fin = [b for b in bs if b["name"] in FINISH_NAMES and b["enabled"]]
                if fin:
                    for b in bs:   # 「執行 / 檢視」勾選框取消
                        if any(hh in b["name"] for hh in RUN_HINTS) and checked(b["hwnd"]):
                            wm_command(b["hwnd"])
                    wm_command(fin[0]["hwnd"]); finished = True
                    LOG["steps"].append({"win32": "Finish"}); LOG["methods_used"].append("win32_wm_command:Finish")
                    break
                nxt = [b for b in bs if b["name"] in NEXT_NAMES and b["enabled"]]
                if nxt:
                    wm_command(nxt[0]["hwnd"])
                    LOG["steps"].append({"win32": nxt[0]["name"], "page": t["title"], "class": nxt[0]["class"], "stall": stall})
                    LOG["methods_used"].append("win32_wm_command:%s" % nxt[0]["name"])
                else:
                    LOG["steps"].append({"win32_page": t["title"], "buttons": [(b["name"], b["enabled"]) for b in bs][:8]})
                break
            if finished:
                break
            if not tops and proc.poll() is not None and LOG["steps"]:
                break
            continue
        for w in sorted(wins, key=lambda x: (x.get("bounds") or {}).get("width", 9999)):
            nodes = list(walk(ad("snapshot", "--window-id", w["id"], "--compact").get("data", {}).get("tree", {})))
            btns = [n for n in nodes if n.get("role") == "button" and n.get("ref_id")]
            key = (w.get("title"), page_key(nodes))
            stall = stall + 1 if key == last_key else 0
            last_key = key
            for n in nodes:   # 授權頁的「我接受」單選鈕
                st = str(n.get("states") or []).lower()
                if n.get("role") in ("radiobutton", "radio") and any(h in (n.get("name") or "") for h in ACCEPT_HINTS) \
                        and "checked" not in st and "selected" not in st and n.get("ref_id"):
                    r = ad("click", n["ref_id"]); LOG["steps"].append({"accept": n.get("name"), "ok": r.get("ok")})
            fin = [n for n in btns if clean(n.get("name")) in FINISH_NAMES]
            if fin:
                for n in nodes:
                    if n.get("role") == "checkbox" and any(h in (n.get("name") or "") for h in RUN_HINTS) \
                            and "checked" in str(n.get("states") or []).lower() and n.get("ref_id"):
                        ad("uncheck", n["ref_id"])
                r = ad("click", fin[0]["ref_id"]); LOG["steps"].append({"click": "Finish", "ok": r.get("ok")})
                finished = True
                break
            nxt = [n for n in btns if clean(n.get("name")) in NEXT_NAMES and "disabled" not in str(n.get("states") or []).lower()]
            if not nxt:
                LOG["steps"].append({"page": w.get("title"), "no_next": [clean(n.get("name")) for n in btns][:8]})
                continue
            name = clean(nxt[0].get("name"))
            if stall == 0:
                escalate = 0
            if stall >= 3 and escalate == 0:
                k = wm_command_by_name(w["pid"], name); escalate = 1
                LOG["steps"].append({"page": w.get("title"), "method": "WM_COMMAND", "button": name, "posted": k})
                LOG["methods_used"].append("wm_command:%s" % name)
            elif stall >= 5 and escalate == 1:
                k = bm_click(w["pid"], name); escalate = 2
                LOG["steps"].append({"page": w.get("title"), "method": "BM_CLICK", "button": name, "hwnds": k})
                LOG["methods_used"].append("bm_click:%s" % name)
            elif stall >= 7 and escalate == 2:
                # 實體點擊會落在最上層的視窗 → 先把安裝程式拉到前景（只在 CI）
                fw = ad("focus-window", "--window-id", w["id"]).get("ok")
                time.sleep(0.5)
                r = ad("click", "--headed", nxt[0]["ref_id"]); escalate = 3
                LOG["steps"].append({"page": w.get("title"), "method": "physical(--headed)", "button": name,
                                     "ok": r.get("ok"), "focus_ok": fw})
                LOG["methods_used"].append("physical:%s" % name)
            else:
                r = ad("click", nxt[0]["ref_id"])
                LOG["steps"].append({"page": w.get("title"), "method": "semantic", "button": name, "ok": r.get("ok"), "stall": stall})
            break
    try:
        proc.wait(timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill()
    LOG["finished_clicked"] = finished
    LOG["installed"] = os.path.exists(spec["target"])
    # 完成頁的「啟動程式」勾選框在 Inno 是 TNewCheckListBox（不是按鈕，取消不了）→ 程式被啟動會鎖住檔案、移除失敗。
    # 只關「從剛裝好的路徑啟動」的程序（依路徑找 PID，不按程序名關）。
    inst_dir = os.path.dirname(spec["target"])
    ps = ("Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith('%s', 'OrdinalIgnoreCase') } | "
          "ForEach-Object { $_.Id; Stop-Process -Id $_.Id -Force }" % inst_dir.replace("'", "''"))
    killed = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout.split()
    LOG["launched_after_finish_pids_closed"] = len(killed)
    if os.path.exists(spec["uninstall"][0]):
        subprocess.run(spec["uninstall"], timeout=240)
        for _ in range(120):   # Inno 的移除程式會把自己複製到暫存再跑、立即返回 → 等久一點
            time.sleep(1)
            if not os.path.exists(spec["target"]):
                break
    LOG["removed"] = not os.path.exists(spec["target"])
    LOG["ok"] = LOG["finished_clicked"] and LOG["installed"] and LOG["removed"]
    LOG["methods_used"] = sorted(set(LOG["methods_used"]))
    LOG["steps"] = LOG["steps"][-40:]
    print(json.dumps(LOG, ensure_ascii=False, indent=1))
    if arg("--out"):
        json.dump(LOG, open(arg("--out"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("ALL PASS (1)" if LOG["ok"] else "FAILED 1/1")
    sys.exit(0 if LOG["ok"] else 1)


if __name__ == "__main__":
    main()
