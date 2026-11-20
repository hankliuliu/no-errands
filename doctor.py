#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""no-errands doctor — 每一階做「真的」smoke test（不是只看 PATH），純標準函式庫。

用法: python doctor.py [--home <dir>] [--skip-desktop] [--skip-browser]
產出: <home>/capabilities.md、刷新所有已安裝目標的 CLAUDE.md 受管區塊；stdout 一份 JSON。
守則: 測完把自己開的視窗/程序清乾淨；殺程序一律用「參數指紋」，絕不用程序名。
"""
import argparse, json, os, platform, subprocess, sys, tempfile, time, urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import setup as ne  # 共用 exe_name / managed_block / upsert_block

IS_WIN = os.name == "nt"

def run(cmd, timeout=30):
    """執行指令，回 (rc, stdout, stderr)。不經 shell。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"

def jrun(cmd, timeout=30):
    rc, out, err = run(cmd, timeout)
    try:
        return rc, json.loads(out)
    except Exception:
        return rc, {"_raw": out, "_err": err}

def check_python(res):
    res["python"] = {"ok": True, "version": platform.python_version(), "path": sys.executable}

def check_pkg_manager(res):
    cands = [("winget", ["winget", "--version"])] if IS_WIN else \
            [("brew", ["brew", "--version"]), ("apt-get", ["apt-get", "--version"])]
    found = []
    for name, cmd in cands:
        rc, out, _ = run(cmd, 20)
        if rc == 0 and out:
            found.append({"name": name, "version": out.splitlines()[0]})
    res["pkg_manager"] = {"ok": bool(found), "found": found,
                          "recovery": None if found else "Windows: 從 Microsoft Store 裝 App Installer"}

def detect_browsers(res):
    paths = []
    if IS_WIN:
        la = os.environ.get("LOCALAPPDATA", "")
        cands = [
            ("chrome", os.path.join(la, "Google", "Chrome", "Application", "chrome.exe")),
            ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            ("edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            ("brave", os.path.join(la, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")),
            ("brave", r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
    else:
        cands = [("chrome", "/usr/bin/google-chrome"), ("chromium", "/usr/bin/chromium"),
                 ("chromium", "/snap/bin/chromium"),
                 ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    seen = set()
    for name, p in cands:
        if os.path.exists(p) and name not in seen:
            seen.add(name)
            paths.append({"name": name, "path": p})
    res["browsers"] = {"ok": bool(paths), "found": paths,
                       "recovery": None if paths else "需要任一 Chromium 系瀏覽器；Windows 11 內建 Edge，請確認路徑"}

def check_desktop(res, home):
    ad = os.path.join(home, "bin", ne.exe_name("agent-desktop"))
    r = {"ok": False, "tool": ad}
    if not os.path.exists(ad):
        r["error"], r["recovery"] = "binary missing", "先跑 setup.py"
        res["desktop"] = r; return
    if not IS_WIN:
        rc, out, _ = run([ad, "status"], 20)
        r.update(ok=(rc == 0), note="非 Windows：僅驗 status（M1 範圍）", status_rc=rc)
        res["desktop"] = r; return
    token = "ne-doctor-%d" % int(time.time())
    launched = False
    ref = None
    try:
        # notepad.exe 是跳板程序（啟動新版記事本後即退出），launch 的「等程序」會等好等滿
        # 預設 30 秒——用 --timeout 壓短；真正的就緒確認交給後面的 snapshot。
        rc, j = jrun([ad, "launch", "notepad", "--timeout", "5000"], 20)
        if rc != 0:
            r["error"] = "launch failed: %s" % j
            res["desktop"] = r; return
        launched = True
        time.sleep(2.5)
        rc, j = jrun([ad, "snapshot", "--app", "Notepad.exe", "-i", "--compact"], 30)
        ref = None
        def walk(n):
            nonlocal ref
            if isinstance(n, dict):
                if n.get("role") == "textfield" and "SetValue" in (n.get("available_actions") or []):
                    ref = ref or n.get("ref_id")
                for c in n.get("children", []) or []:
                    walk(c)
        walk(j.get("data", {}).get("tree", {}))
        if not ref:
            r["error"] = "no editable textfield in a11y tree"
            r["recovery"] = "確認 Windows 記事本可啟動；或換一個有文字框的 app 重試"
            res["desktop"] = r; return
        rc, j = jrun([ad, "set-value", ref, token], 30)
        rc, j = jrun([ad, "get", ref, "--property", "value"], 30)
        got = j.get("data", {}).get("value")
        r["roundtrip"] = {"wrote": token, "read": got}
        if got == token:
            r["ok"] = True
        else:
            r["error"] = "write/read mismatch"
    finally:
        if launched:
            if ref:
                run([ad, "set-value", ref, ""], 20)
            run([ad, "close-app", "Notepad.exe"], 20)
    res["desktop"] = r

def ab_cmd(home, session, extra):
    ab = os.path.join(home, "bin", ne.exe_name("agent-browser"))
    return [ab, "--session", session] + extra

def check_browser(res, home):
    ab = os.path.join(home, "bin", ne.exe_name("agent-browser"))
    r = {"ok": False, "tool": ab}
    if not os.path.exists(ab):
        r["error"], r["recovery"] = "binary missing", "先跑 setup.py"
        res["browser"] = r; return
    page = "file:///" + os.path.join(REPO, "doctor-assets", "testpage.html").replace("\\", "/")
    S = "ne-doctor"
    try:
        rc, out, err = run(ab_cmd(home, S, ["open", page]), 60)
        if rc != 0:
            r["error"] = "open failed: %s %s" % (out[-200:], err[-200:])
            r["recovery"] = "確認系統有 Chromium 系瀏覽器；或 config.json 指定 executable"
            return
        run(ab_cmd(home, S, ["click", "#doctorBtn"]), 30)
        rc, out, _ = run(ab_cmd(home, S, ["get", "text", "#result"]), 30)
        r["clicked_text"] = out
        png = os.path.join(tempfile.gettempdir(), "ne-doctor-shot.png")
        if os.path.exists(png):
            os.remove(png)
        run(ab_cmd(home, S, ["screenshot", png]), 30)
        shot_ok = os.path.exists(png) and os.path.getsize(png) > 1000
        r["screenshot_bytes"] = os.path.getsize(png) if os.path.exists(png) else 0
        if os.path.exists(png):
            os.remove(png)  # 守則：截圖可能含私資，用完即刪
        r["ok"] = ("DOCTOR-OK-" in out) and shot_ok
        if not r["ok"]:
            r["error"] = "click/read or screenshot failed"
    finally:
        run(ab_cmd(home, S, ["close"]), 20)
    res["browser"] = r

def check_profile(res, home):
    prof = os.path.join(home, "browser-profile")
    page = "file:///" + os.path.join(REPO, "doctor-assets", "testpage.html").replace("\\", "/")
    val = "ne-%d" % int(time.time())
    r = {"ok": False, "profile": prof}
    try:
        run(ab_cmd(home, "ne-key-a", ["--profile", prof, "open", page]), 60)
        run(ab_cmd(home, "ne-key-a", ["eval", "localStorage.setItem('ne-doctor','%s'); 'set'" % val]), 30)
        run(ab_cmd(home, "ne-key-a", ["close"]), 20)
        time.sleep(1)
        run(ab_cmd(home, "ne-key-b", ["--profile", prof, "open", page]), 60)
        rc, out, _ = run(ab_cmd(home, "ne-key-b", ["eval", "localStorage.getItem('ne-doctor')"]), 30)
        r["read_back"] = out
        r["ok"] = val in out
        if not r["ok"]:
            r["recovery"] = "file:// 的 localStorage 可能被鎖；改用 https 測試頁或檢查 profile 目錄權限"
    finally:
        run(ab_cmd(home, "ne-key-b", ["close"]), 20)
    res["profile"] = r

def write_outputs(res, home, dur):
    lines = ["# no-errands 本機能力圖（doctor 實測，%s，%.1fs）" % (time.strftime("%Y-%m-%d %H:%M"), dur), ""]
    for k, v in res.items():
        mark = "✅" if v.get("ok") else "❌"
        extra = ""
        if k == "python": extra = "%s @ %s" % (v["version"], v["path"])
        if k == "pkg_manager": extra = ", ".join(f["name"] + " " + f["version"] for f in v["found"]) or "無"
        if k == "browsers": extra = ", ".join(f["name"] + " = " + f["path"] for f in v["found"]) or "無"
        if k == "desktop": extra = "a11y 寫入→讀回 %s（%s）" % ("一致" if v.get("ok") else "失敗", v.get("tool"))
        if k == "browser": extra = "click→DOM 變化→截圖 %s（%s）" % ("通過" if v.get("ok") else "失敗", v.get("tool"))
        if k == "profile": extra = "跨程序登入狀態 %s（%s）" % ("持久" if v.get("ok") else "失敗", v.get("profile"))
        lines.append("- %s %s：%s" % (mark, k, extra))
        if v.get("recovery"):
            lines.append("  - recovery: %s" % v["recovery"])
    cap = os.path.join(home, "capabilities.md")
    open(cap, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    # 刷新所有安裝目標的受管區塊
    cfg_p = os.path.join(home, "config.json")
    refreshed = []
    if os.path.exists(cfg_p):
        cfg = json.load(open(cfg_p, encoding="utf-8"))
        paths = {"agent-desktop": os.path.join(home, "bin", ne.exe_name("agent-desktop")),
                 "agent-browser": os.path.join(home, "bin", ne.exe_name("agent-browser"))}
        for inst in cfg.get("installs", []):
            ne.upsert_block(inst["target"], ne.managed_block(home, paths))
            refreshed.append(inst["target"])
    return cap, refreshed

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.path.join(os.path.expanduser("~"), ".no-errands"))
    ap.add_argument("--skip-desktop", action="store_true")
    ap.add_argument("--skip-browser", action="store_true")
    a = ap.parse_args()
    home = os.path.abspath(a.home)
    t0 = time.time()
    res = {}
    check_python(res)
    check_pkg_manager(res)
    detect_browsers(res)
    if not a.skip_desktop:
        check_desktop(res, home)
    if not a.skip_browser:
        check_browser(res, home)
        check_profile(res, home)
        run([os.path.join(home, "bin", ne.exe_name("agent-browser")), "close", "--all"], 20)
    dur = time.time() - t0
    cap, refreshed = write_outputs(res, home, dur)
    ok = all(v.get("ok") for v in res.values())
    print(json.dumps({"ok": ok, "duration_s": round(dur, 1), "capabilities": cap,
                      "refreshed_blocks": refreshed, "checks": res}, ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
