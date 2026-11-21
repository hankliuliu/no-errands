#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B5 示範模式（CI Windows only；有頭瀏覽器、會開視窗）：
瀏覽器（--headed）開測試頁、按按鈕；桌面開假聊天、找對話——每一步放慢、截單一視窗，最後產出 guide.md。
  python tests/demo_lab.py --home <no-errands home> --out demo-out --i-am-ci"""
import json, os, subprocess, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")


def arg(k, d=None):
    return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d


def main():
    if "--i-am-ci" not in sys.argv:
        print("refusing: opens visible windows; CI only (--i-am-ci)"); sys.exit(2)
    home, out = os.path.abspath(arg("--home")), os.path.abspath(arg("--out", "demo-out"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    D = [sys.executable, os.path.join(SC, "demo.py")]
    ab = os.path.join(home, "bin", "agent-browser.exe")
    ad = os.path.join(home, "bin", "agent-desktop.exe")
    page = "file:///" + os.path.join(REPO, "doctor-assets", "testpage.html").replace("\\", "/")
    run = lambda *a: subprocess.run(D + list(a), env=env, capture_output=True, text=True, encoding="utf-8")
    run("begin", "--out", out, "--title", "示範：瀏覽器與桌面各走一段")
    S = ["--session", "ne-demo"]
    run("step", "--out", out, "--say", "用有頭瀏覽器打開測試頁（觀看模式：看得到視窗）", "--browser", "ne-demo",
        "--", ab, "--headed", *S, "open", page)
    run("step", "--out", out, "--say", "按下「執行檢查」按鈕", "--browser", "ne-demo", "--", ab, *S, "click", "button")
    subprocess.run([ab, *S, "close"], capture_output=True)
    state = os.path.join(out, "fakechat-state")
    fake = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File",
                             os.path.join(REPO, "scenarios", "fake-chat", "fakechat.ps1"), "-StateDir", state])
    win = None
    for _ in range(40):
        time.sleep(0.5)
        for w in json.loads(subprocess.run([ad, "list-windows"], capture_output=True, text=True, encoding="utf-8").stdout).get("data", []):
            if w.get("title") == "NE-FAKECHAT":
                win = w["id"]
        if win:
            break
    run("step", "--out", out, "--say", "桌面：這是一個聊天程式（測試用的假視窗）", "--window", win or "")
    pol = os.path.join(home, "policy.json")
    p = json.load(open(pol, encoding="utf-8")) if os.path.exists(pol) else {}
    p.setdefault("granted_apps", []).append("fake-chat"); p["visibility"] = "watch"
    p.setdefault("chat", {}).setdefault("whitelist", []).append("Alpha Team")
    json.dump(p, open(pol, "w", encoding="utf-8"), ensure_ascii=False)
    run("step", "--out", out, "--say", "用守門工具開啟指名的對話「Alpha Team」", "--window", win or "",
        "--", sys.executable, os.path.join(SC, "chat_guard.py"), "fake-chat", "open", "--chat", "Alpha Team")
    fin = json.loads(run("finish", "--out", out).stdout)
    fake.kill()
    guide = open(fin["guide"], encoding="utf-8").read()
    ok = fin["steps"] == 4 and fin["shots"] >= 3 and "## 4." in guide
    print(json.dumps(fin, ensure_ascii=False))
    print("ALL PASS (1)" if ok else "FAILED 1/1")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
