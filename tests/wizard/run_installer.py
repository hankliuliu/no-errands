#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B3(i) GUI 安裝精靈從頭點到完成（CI Windows runner only；會安裝軟體、開視窗）。

  python tests/wizard/run_installer.py --ad <agent-desktop.exe> --installer npp|winmerge --out wizard-npp.json --i-am-ci

每一步先用 agent-desktop 語意點擊；同一頁按 3 次不換頁 → (b) 直接對按鈕 HWND 送 BM_CLICK →
仍不換頁 → (d) 實體點擊（--headed，只在 CI；在使用者機器上要走閒置閘門或事前告知）。
每種手段有沒有用都記下來，作為 playbook 的依據。完成後驗證程式檔存在，再靜默移除。
"""
import ctypes, ctypes.wintypes as wt, hashlib, json, os, subprocess, sys, tempfile, time, urllib.request

INSTALLERS = {
    "npp": {"framework": "NSIS",
            "url": "https://github.com/notepad-plus-plus/notepad-plus-plus/releases/download/v8.6.9/npp.8.6.9.Installer.x64.exe",
            "target": r"C:\Program Files\Notepad++\notepad++.exe",
            "uninstall": [r"C:\Program Files\Notepad++\uninstall.exe", "/S"]},
    "winmerge": {"framework": "Inno Setup",
                 "url": "https://github.com/WinMerge/winmerge/releases/download/v2.16.44/WinMerge-2.16.44-x64-Setup.exe",
                 "target": r"C:\Program Files\WinMerge\WinMergeU.exe",
                 "uninstall": [r"C:\Program Files\WinMerge\unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES"]},
}
NEXT_NAMES = {"OK", "Next >", "Next", "I Agree", "Install", "下一步(N) >", "我接受(I)", "安裝(I)",
              "Install for me only (recommended)", "Install for me only", "Install for all users"}
FINISH_NAMES = {"Finish", "完成(F)"}
ACCEPT_HINTS = ("I accept", "我接受")
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
    return (s or "").replace("&", "").strip()


def bm_click(pid, name):
    """在該程序所有頂層視窗的子視窗裡找文字相符的 Button，直接投遞 BM_CLICK。"""
    found = []
    ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def child(h, _):
        cls = ctypes.create_unicode_buffer(64); u32.GetClassNameW(h, cls, 64)
        t = ctypes.create_unicode_buffer(128); u32.GetWindowTextW(h, t, 128)
        if cls.value == "Button" and clean(t.value) == clean(name) and u32.IsWindowVisible(h):
            found.append(h)
        return True
    child_cb = ENUM(child)

    def top(h, _):
        p = wt.DWORD(); u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid:
            u32.EnumChildWindows(h, child_cb, 0)
        return True
    u32.EnumWindows(ENUM(top), 0)
    for h in found:
        u32.PostMessageW(h, 0x00F5, 0, 0)   # BM_CLICK
    return len(found)


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
            if proc.poll() is not None and LOG["steps"]:
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
                k = bm_click(w["pid"], name); escalate = 1
                LOG["steps"].append({"page": w.get("title"), "method": "BM_CLICK", "button": name, "hwnds": k})
                LOG["methods_used"].append("bm_click:%s" % name)
            elif stall >= 6 and escalate == 1:
                ad("focus-window", "--window-id", w["id"])   # 實體點擊會落在最上層的視窗 → 先把安裝程式拉到前景（只在 CI）
                time.sleep(0.5)
                r = ad("click", "--headed", nxt[0]["ref_id"]); escalate = 2
                LOG["steps"].append({"page": w.get("title"), "method": "physical(--headed)", "button": name, "ok": r.get("ok")})
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
    if os.path.exists(spec["uninstall"][0]):
        subprocess.run(spec["uninstall"], timeout=240)
        for _ in range(40):
            time.sleep(1)
            if not os.path.exists(spec["target"]):
                break
    LOG["removed"] = not os.path.exists(spec["target"])
    LOG["ok"] = LOG["finished_clicked"] and LOG["installed"] and LOG["removed"]
    LOG["steps"] = LOG["steps"][-40:]
    print(json.dumps(LOG, ensure_ascii=False, indent=1))
    if arg("--out"):
        json.dump(LOG, open(arg("--out"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("ALL PASS (1)" if LOG["ok"] else "FAILED 1/1")
    sys.exit(0 if LOG["ok"] else 1)


if __name__ == "__main__":
    main()
