#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""no-errands doctor — 每一階做「真的」smoke test（不是只看 PATH），純標準函式庫。

用法: python doctor.py [--home <dir>] [--skip-desktop] [--skip-browser]
產出: <home>/capabilities.md、刷新所有已安裝目標的 CLAUDE.md 受管區塊（只對通過的項目說可用）。
守則:
- 桌面測試只操作 doctor 自己 spawn 的專屬測試視窗（唯一標題 + PID），絕不碰使用者的 app。
- 測完清掉自己開的視窗/程序/暫存 profile；殺程序用 PID/參數指紋，絕不用程序名。
- 每個 check 無論成敗都必須在結果裡留下紀錄（失敗靜默 = 誤報全綠）。
"""
import argparse, json, os, platform, shutil, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import setup as ne  # 共用 exe_name / managed_block / upsert_block

IS_WIN = os.name == "nt"

def run(cmd, timeout=30):
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
    r = {"ok": False, "found": []}
    res["pkg_manager"] = r
    cands = [("winget", ["winget", "--version"])] if IS_WIN else \
            [("brew", ["brew", "--version"]), ("apt-get", ["apt-get", "--version"])]
    for name, cmd in cands:
        rc, out, _ = run(cmd, 20)
        if rc == 0 and out:
            r["found"].append({"name": name, "version": out.splitlines()[0]})
    r["ok"] = bool(r["found"])
    if not r["ok"]:
        r["recovery"] = "Windows: 從 Microsoft Store 裝 App Installer；macOS: 裝 Homebrew"

def detect_browsers(res):
    r = {"ok": False, "found": []}
    res["browsers"] = r
    la = os.environ.get("LOCALAPPDATA", "")
    cands = [
        ("chrome", os.path.join(la, "Google", "Chrome", "Application", "chrome.exe")),
        ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        ("edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ("edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ("brave", os.path.join(la, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")),
        ("brave", r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ] if IS_WIN else [
        ("chrome", "/usr/bin/google-chrome"), ("chrome", "/usr/bin/google-chrome-stable"),
        ("chromium", "/usr/bin/chromium"), ("chromium", "/usr/bin/chromium-browser"),
        ("chromium", "/snap/bin/chromium"), ("edge", "/usr/bin/microsoft-edge"),
        ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ("brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
    ]
    seen = set()
    for name, p in cands:
        if os.path.exists(p) and name not in seen:
            seen.add(name)
            r["found"].append({"name": name, "path": p})
    r["ok"] = bool(r["found"])
    if not r["ok"]:
        r["note"] = "沒有系統瀏覽器；agent-browser 可用 `install` 自帶一份（doctor 的瀏覽器測試會自動用它）"
        r["ok"] = True  # 不是硬失敗：agent-browser install 是合法備援

# ── 桌面：spawn 專屬測試視窗（WinForms，唯一標題），只操作它、只殺自己的 PID ──
PS_FORM = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = '{TITLE}'
$f.Width = 420; $f.Height = 180; $f.StartPosition = 'Manual'
$f.Left = 40; $f.Top = 40
$t = New-Object System.Windows.Forms.TextBox
$t.Name = 'neInput'; $t.AccessibleName = 'neInput'; $t.Width = 300; $t.Top = 20; $t.Left = 20
$b = New-Object System.Windows.Forms.Button
$b.Text = 'NE-OK'; $b.AccessibleName = 'NE-OK'; $b.Top = 60; $b.Left = 20
$b.Add_Click({ $t.Text = 'CLICKED:' + $t.Text })
$f.Controls.Add($t); $f.Controls.Add($b)
[System.Windows.Forms.Application]::Run($f)
"""

def check_desktop_posix(r, ad):
    """macOS / Linux：自有對話框把輸入印到 stdout，doctor 用 agent-desktop 填值、按 OK，
    再比對**對話框程序自己印出的值**（驗證獨立於 agent-desktop）。"""
    rc, out, _ = run([ad, "status"], 20)
    r["status_rc"] = rc
    rc2, perm, _ = run([ad, "permissions"], 20)
    r["permissions_raw"] = (perm or "")[:600]          # 存原文：status ✅ 到底代表有權限、還是只代表 binary 能跑
    title = "NE-DOCTOR-%d" % os.getpid()
    token = "ne%d" % int(time.time())
    if sys.platform == "darwin":
        script = ('text returned of (display dialog "no-errands doctor" default answer "" buttons {"OK"} '
                  'default button "OK" with title "%s")' % title)
        cmd, app = ["osascript", "-e", script], "osascript"
    elif shutil.which("zenity"):
        cmd, app = ["zenity", "--entry", "--title", title, "--text", "no-errands doctor"], "zenity"
    else:
        r.update(ok=(rc == 0), roundtrip="skipped", note="Linux 無測試靶（未安裝 zenity）：只驗了 status，沒有驗寫入→讀回")
        return
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        win_id = None
        for _ in range(30):
            time.sleep(0.5)
            rc3, j = jrun([ad, "list-windows"], 20)
            for w in (j.get("data") or []):
                if isinstance(w, dict) and (w.get("title") == title or (w.get("pid") == proc.pid and w.get("title"))):
                    win_id = w.get("id"); break
            if win_id:
                break
            if not j.get("ok") and "ermission" in json.dumps(j):
                break
        if not win_id:
            msg = json.dumps(j)[:300] if isinstance(j, dict) else ""
            if "ermission" in msg or "PERMISSION" in msg or "not trusted" in msg.lower():
                r.update(ok=False, needs_permission=True, error="需授權：輔助使用（Accessibility）權限未開",
                         recovery="agent-desktop permissions --guide，把啟動 agent 的 app 加進輔助使用清單")
            else:
                r.update(ok=False, error="找不到自有測試對話框（%s）" % app, list_windows=msg,
                         recovery="Linux：確認 AT-SPI 匯流排在跑（dbus session + at-spi-bus-launcher）")
            return
        rc4, j = jrun([ad, "snapshot", "--window-id", str(win_id), "-i", "--compact"], 30)
        refs = {"edit": None, "btn": None}

        def walk(n):
            if isinstance(n, dict):
                acts = n.get("available_actions") or []
                if n.get("role") in ("textfield", "text") and "SetValue" in acts and not refs["edit"]:
                    refs["edit"] = n.get("ref_id")
                if n.get("role") == "button" and (n.get("name") or "").replace("_", "") in ("OK", "確定", "好") and not refs["btn"]:
                    refs["btn"] = n.get("ref_id")
                for c in n.get("children", []) or []:
                    walk(c)
        walk(j.get("data", {}).get("tree", {}))
        if not refs["edit"] or not refs["btn"]:
            r.update(ok=False, error="測試對話框的無障礙樹缺文字框或 OK 鈕（refs=%s）" % refs)
            return
        jrun([ad, "set-value", refs["edit"], token], 30)
        jrun([ad, "click", refs["btn"]], 30)
        try:
            printed, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            printed = ""
        r["roundtrip"] = {"wrote": token, "dialog_printed": (printed or "").strip()}
        r["ok"] = (printed or "").strip() == token
        r["tool_note"] = "驗證值來自對話框程序（%s）自己的輸出，不是 agent-desktop 讀回" % app
        if not r["ok"]:
            r["error"] = "寫入→按 OK→對話框印出的值不一致"
    finally:
        if proc.poll() is None:
            proc.kill()   # 只殺 doctor 自己 spawn 的 PID


def check_desktop(res, home):
    ad = os.path.join(home, "bin", ne.exe_name("agent-desktop"))
    r = {"ok": False, "tool": ad}
    res["desktop"] = r
    if not os.path.exists(ad):
        r["error"], r["recovery"] = "binary missing", "先跑 setup.py"
        return
    if not IS_WIN:
        return check_desktop_posix(r, ad)
    title = "NE-DOCTOR-%d" % os.getpid()
    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Normal", "-Command",
         PS_FORM.replace("{TITLE}", title)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        win_id = None
        for _ in range(20):
            time.sleep(0.5)
            rc, j = jrun([ad, "list-windows"], 20)
            data = j.get("data")
            wins = data if isinstance(data, list) else (data or {}).get("windows", [])
            for w in wins:
                if isinstance(w, dict) and w.get("title") == title and w.get("pid") == proc.pid:
                    win_id = w.get("id") or w.get("window_id")
                    break
            if win_id:
                break
        if not win_id:
            r["error"] = "spawned test window not found via list-windows"
            r["recovery"] = "確認 powershell/WinForms 可用；或看 agent-desktop list-windows 輸出格式是否變了"
            return
        rc, j = jrun([ad, "snapshot", "--window-id", str(win_id), "-i", "--compact"], 30)
        refs = {"edit": None, "btn": None}
        def walk(n):
            if isinstance(n, dict):
                acts = n.get("available_actions") or []
                if n.get("role") == "textfield" and "SetValue" in acts and not refs["edit"]:
                    refs["edit"] = n.get("ref_id")
                if n.get("role") == "button" and n.get("name") == "NE-OK" and not refs["btn"]:
                    refs["btn"] = n.get("ref_id")
                for c in n.get("children", []) or []:
                    walk(c)
        walk(j.get("data", {}).get("tree", {}))
        if not refs["edit"] or not refs["btn"]:
            r["error"] = "test window a11y tree missing textbox/button (refs=%s)" % refs
            return
        token = "ne-%d" % int(time.time())
        jrun([ad, "set-value", refs["edit"], token], 30)
        jrun([ad, "click", refs["btn"]], 30)
        rc, j = jrun([ad, "get", refs["edit"], "--property", "value"], 30)
        got = j.get("data", {}).get("value")
        r["roundtrip"] = {"wrote": token, "after_click": got}
        r["ok"] = (got == "CLICKED:" + token)
        if not r["ok"]:
            r["error"] = "write→click→read mismatch"
    finally:
        proc.kill()   # 只殺 doctor 自己 spawn 的 PID
        proc.wait(timeout=10)

def ab_cmd(home, session, extra):
    ab = os.path.join(home, "bin", ne.exe_name("agent-browser"))
    return [ab, "--session", session] + extra

def check_browser(res, home):
    ab = os.path.join(home, "bin", ne.exe_name("agent-browser"))
    r = {"ok": False, "tool": ab}
    res["browser"] = r
    if not os.path.exists(ab):
        r["error"], r["recovery"] = "binary missing", "先跑 setup.py"
        return
    page = "file:///" + os.path.join(REPO, "doctor-assets", "testpage.html").replace("\\", "/")
    S = "ne-doctor"
    try:
        rc, out, err = run(ab_cmd(home, S, ["open", page]), 90)
        if rc != 0:
            r["error"] = "open failed: %s %s" % (out[-200:], err[-200:])
            if "loading shared librari" in (out + err):
                r["recovery"] = ("缺系統庫。無 sudo 的免 root 解法：apt-get download libnspr4 libnss3 "
                                 "libasound2t64 → dpkg -x 到 ~/ne-libs → 以 LD_LIBRARY_PATH 啟動"
                                 "（詳見 playbook/tasks/linux-no-sudo-browser-libs.md）")
            else:
                r["recovery"] = "沒有系統瀏覽器時先跑 `agent-browser install`；或在 config.json 指定 executable"
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

def check_profile(res, home):
    prof = os.path.join(home, ".doctor-profile")  # 不碰正式 browser-profile（可能正被使用/避免殘渣）
    page = "file:///" + os.path.join(REPO, "doctor-assets", "testpage.html").replace("\\", "/")
    val = "ne-%d" % int(time.time())
    r = {"ok": False, "profile_mechanism": "persistent --profile（以暫存 .doctor-profile 驗證機制）"}
    res["profile"] = r
    try:
        run(ab_cmd(home, "ne-key-a", ["--profile", prof, "open", page]), 90)
        run(ab_cmd(home, "ne-key-a", ["eval", "localStorage.setItem('ne-doctor','%s'); 'set'" % val]), 30)
        run(ab_cmd(home, "ne-key-a", ["close"]), 20)
        time.sleep(1)
        run(ab_cmd(home, "ne-key-b", ["--profile", prof, "open", page]), 90)
        rc, out, _ = run(ab_cmd(home, "ne-key-b", ["eval", "localStorage.getItem('ne-doctor')"]), 30)
        r["read_back"] = out
        r["ok"] = val in out
        if not r["ok"]:
            r["error"] = "state did not survive a browser restart"
            r["recovery"] = "檢查 profile 目錄可寫；或 file:// localStorage 被鎖 → 改 http 測試頁"
    finally:
        run(ab_cmd(home, "ne-key-b", ["close"]), 20)
        shutil.rmtree(prof, ignore_errors=True)  # 測試殘渣不留

def write_outputs(res, home, dur):
    lines = ["# no-errands 本機能力圖（doctor 實測，%s，%.1fs）" % (time.strftime("%Y-%m-%d %H:%M"), dur), ""]
    for k, v in res.items():
        mark = "✅" if v.get("ok") else "❌"
        extra = ""
        if k == "python": extra = "%s @ %s" % (v["version"], v["path"])
        if k == "pkg_manager": extra = ", ".join(f["name"] + " " + f["version"] for f in v["found"]) or "無"
        if k == "browsers": extra = ", ".join(f["name"] + " = " + f["path"] for f in v["found"]) or v.get("note", "無")
        if k == "desktop": extra = "專屬測試視窗 寫入→點擊→讀回 %s（%s）" % ("一致" if v.get("ok") else "失敗", v.get("tool"))
        if k == "browser": extra = "click→DOM 變化→截圖 %s（%s）" % ("通過" if v.get("ok") else "失敗", v.get("tool"))
        if k == "profile": extra = "跨程序持久 %s" % ("通過" if v.get("ok") else "失敗")
        lines.append("- %s %s：%s" % (mark, k, extra))
        if v.get("error"):
            lines.append("  - error: %s" % v["error"])
        if v.get("recovery"):
            lines.append("  - recovery: %s" % v["recovery"])
    cap = os.path.join(home, "capabilities.md")
    open(cap, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    cfg_p = os.path.join(home, "config.json")
    refreshed = []
    if os.path.exists(cfg_p):
        cfg = json.load(open(cfg_p, encoding="utf-8"))
        paths = {"agent-desktop": os.path.join(home, "bin", ne.exe_name("agent-desktop")),
                 "agent-browser": os.path.join(home, "bin", ne.exe_name("agent-browser"))}
        repo_hash = None
        repo = cfg.get("repo")
        if repo and os.path.isdir(os.path.join(repo, "skills", "no-errands")):
            repo_hash = ne.skill_tree_hash(os.path.join(repo, "skills", "no-errands"))
        for inst in cfg.get("installs", []):
            ne.upsert_block(inst["target"], ne.managed_block(home, paths, checks=res, repo=repo))
            refreshed.append(inst["target"])
            if repo_hash and os.path.isdir(inst["skill"]):
                if ne.skill_tree_hash(inst["skill"]) != repo_hash:
                    res.setdefault("skill_sync", {"ok": False,
                        "error": "已安裝 skill 與 repo 版本不一致",
                        "recovery": "跑 setup.py --sync --home %s" % home,
                        "stale": []})["stale"].append(inst["skill"])
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
