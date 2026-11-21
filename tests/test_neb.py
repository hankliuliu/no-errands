#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neb 守門測試（純 stdlib、無瀏覽器；三平台 CI）。用假的 agent-browser（NE_FAKE_AB_PY）。"""
import json, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEB = os.path.join(REPO, "skills", "no-errands", "scripts", "neb.py")
POL = os.path.join(REPO, "skills", "no-errands", "scripts", "policy.py")
R = []

FAKE = r'''
import json, os, sys
st = os.path.join(os.environ["NE_FAKE_DIR"], "url.txt")
a = [x for x in sys.argv[1:]]
while a and a[0].startswith("--"):
    a = a[2:] if a[0] in ("--session", "--profile") else a[1:]
sub = a[0]
if sub == "open":
    open(st, "w").write(a[1] if "://" in a[1] else "https://" + a[1]); print("ok")
elif sub == "get" and a[1:2] == ["url"]:
    print(open(st).read() if os.path.exists(st) else "about:blank")
elif sub == "snapshot":
    print('- textbox "Message" [ref=e1]\n- button "Send" [ref=e2]\n- link "Inbox" [level=2, ref=e3]\n- button "回覆" [ref=e4]')
elif sub == "navigate-away":
    open(st, "w").write(a[1]); print("ok")
else:
    print("did " + sub)
'''


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


def main():
    home = tempfile.mkdtemp(prefix="ne-neb-")
    fk = os.path.join(home, "fake_ab.py"); open(fk, "w").write(FAKE)
    json.dump({"mattermost": {"url": "https://mm.example.org"}}, open(os.path.join(home, "policy.json"), "w"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, NE_FAKE_AB_PY=fk, NE_FAKE_DIR=home, PYTHONIOENCODING="utf-8")

    def neb(*a):
        p = subprocess.run([sys.executable, NEB, "--session", "t"] + list(a), capture_output=True, text=True,
                           encoding="utf-8", env=env)
        return p.returncode, p.stdout

    def grant(app):
        subprocess.run([sys.executable, POL, "grant", app, "--reason", "test"], env=env, capture_output=True)

    try:
        rc, o = neb("open", "https://example.com")
        check("一般網站 open 放行", rc == 0, o)
        rc, o = neb("fill", "#q", "hello")
        check("一般網站 fill 放行", rc == 0, o)
        rc, o = neb("open", "https://mail.google.com/mail/u/0")
        check("未授權的 Gmail → 拒", rc == 3 and "同意閘門" in o, o)
        rc, o = neb("open", "https://mm.example.org/team/channels/x")
        check("政策檔裡的 Mattermost 主機也算敏感 → 拒", rc == 3, o)
        grant("gmail")
        rc, o = neb("open", "https://mail.google.com/mail/u/0")
        check("授權後可 open", rc == 0, o)
        rc, o = neb("fill", "@e1", "hi")
        check("授權的敏感網站 fill → 拒", rc == 3 and "只能讀" in o, o)
        rc, o = neb("press", "Enter")
        check("授權的敏感網站 press → 拒", rc == 3, o)
        rc, o = neb("click", "#send")
        check("CSS selector 點擊 → 拒（不知道點的是什麼）", rc == 3 and "ref" in o, o)
        rc, o = neb("click", "@e2")
        check("沒先 snapshot 的 ref → 拒", rc == 3, o)
        neb("snapshot", "-i")
        rc, o = neb("click", "@e2")
        check("Send 按鈕 → 拒", rc == 3 and "送出" in o, o)
        rc, o = neb("click", "@e4")
        check("「回覆」按鈕 → 拒", rc == 3, o)
        rc, o = neb("click", "@e3")
        check("一般連結（Inbox）→ 放行", rc == 0, o)
        rc, o = neb("screenshot", "x.png")
        check("screenshot 放行", rc == 0, o)
        # 從一般網站點連結被導到敏感網站：neb 每次都問目前網址
        neb("open", "https://example.com")
        subprocess.run([sys.executable, fk, "navigate-away", "https://web.whatsapp.com/"], env=env)
        rc, o = neb("fill", "#x", "hi")
        check("被導到未授權的敏感網站後 fill → 拒", rc == 3, o)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
