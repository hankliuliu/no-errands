#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B3(i) GUI 安裝精靈從頭點到完成（CI Windows runner only；會開視窗）。

下載 Notepad++ 官方 GitHub release 的 NSIS 安裝檔 → 啟動 → 只用 agent-desktop 語意點擊一路按到完成
（語言 OK → 下一步 → 我同意 → 下一步… → 安裝 → 取消勾選「執行」→ 完成）→ 驗證程式檔存在 → 靜默移除。

  python tests/wizard/run_installer.py --ad <agent-desktop.exe> --out wizard.json --i-am-ci
"""
import hashlib, json, os, subprocess, sys, tempfile, time, urllib.request

URL = "https://github.com/notepad-plus-plus/notepad-plus-plus/releases/download/v8.6.9/npp.8.6.9.Installer.x64.exe"
TARGET = r"C:\Program Files\Notepad++\notepad++.exe"
NEXT_NAMES = ["OK", "Next >", "&Next >", "I Agree", "&I Agree", "Install", "&Install", "Next", "下一步(N) >", "我接受(I)", "安裝(I)"]
FINISH_NAMES = ["Finish", "&Finish", "完成(F)"]
RUN_CHECKBOX_HINTS = ("Run", "執行", "Launch")


def arg(k, d=None):
    return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d


AD = arg("--ad")
LOG = {"steps": []}


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


def main():
    if "--i-am-ci" not in sys.argv:
        print("refusing: installs software and opens windows; CI only (--i-am-ci)"); sys.exit(2)
    td = tempfile.mkdtemp(prefix="wiz-")
    exe = os.path.join(td, "npp-installer.exe")
    urllib.request.urlretrieve(URL, exe)
    LOG["sha256"] = hashlib.sha256(open(exe, "rb").read()).hexdigest()
    proc = subprocess.Popen([exe])
    t0 = time.time()
    finished = False
    while time.time() - t0 < 300 and proc.poll() is None:
        time.sleep(1.0)
        wins = [w for w in (ad("list-windows").get("data") or []) if w.get("pid") == proc.pid or
                "Notepad++" in (w.get("title") or "") and "Setup" in (w.get("title") or "")]
        if not wins:
            continue
        w = wins[0]
        snap = ad("snapshot", "--window-id", w["id"], "-i", "--compact")
        nodes = list(walk(snap.get("data", {}).get("tree", {})))
        names = [n.get("name") for n in nodes if n.get("role") == "button"]
        fin = [n for n in nodes if n.get("role") == "button" and n.get("name") in FINISH_NAMES]
        if fin:
            for n in nodes:   # uncheck "Run Notepad++" so nothing extra launches
                if n.get("role") == "checkbox" and any(hh in (n.get("name") or "") for hh in RUN_CHECKBOX_HINTS):
                    r = ad("uncheck", n["ref_id"]); LOG["steps"].append({"uncheck": n.get("name"), "ok": r.get("ok")})
            r = ad("click", fin[0]["ref_id"]); LOG["steps"].append({"click": fin[0].get("name"), "ok": r.get("ok")})
            finished = True
            break
        nxt = [n for n in nodes if n.get("role") == "button" and n.get("name") in NEXT_NAMES
               and "disabled" not in str(n.get("states") or []).lower()]
        if nxt:
            r = ad("click", nxt[0]["ref_id"])
            LOG["steps"].append({"title": w.get("title"), "click": nxt[0].get("name"), "ok": r.get("ok"), "buttons": names[:6]})
        else:
            LOG["steps"].append({"title": w.get("title"), "waiting": True, "buttons": names[:6]})
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
    LOG["finished_clicked"] = finished
    LOG["installed"] = os.path.exists(TARGET)
    unins = os.path.join(os.path.dirname(TARGET), "uninstall.exe")
    if os.path.exists(unins):
        subprocess.run([unins, "/S"], timeout=180)
        for _ in range(30):
            time.sleep(1)
            if not os.path.exists(TARGET):
                break
    LOG["removed"] = not os.path.exists(TARGET)
    LOG["ok"] = LOG["finished_clicked"] and LOG["installed"] and LOG["removed"]
    print(json.dumps(LOG, ensure_ascii=False, indent=1))
    if arg("--out"):
        json.dump(LOG, open(arg("--out"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("ALL PASS (1)" if LOG["ok"] else "FAILED 1/1")
    sys.exit(0 if LOG["ok"] else 1)


if __name__ == "__main__":
    main()
