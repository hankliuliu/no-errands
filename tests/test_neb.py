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
    fk = os.path.join(home, "fake_ab.py"); open(fk, "w", encoding="utf-8").write(FAKE)
    json.dump({"mattermost": {"url": "https://mm.example.org"}}, open(os.path.join(home, "policy.json"), "w"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, NE_FAKE_AB_PY=fk, NE_FAKE_DIR=home, PYTHONIOENCODING="utf-8")

    def neb(*a):
        p = subprocess.run([sys.executable, NEB, "--session", "t"] + list(a), capture_output=True, text=True,
                           encoding="utf-8", env=env)
        return p.returncode, p.stdout

    def grant(app):
        subprocess.run([sys.executable, POL, "grant", app, "--reason", "test"], env=env, capture_output=True)

    try:
        # 升級守門：放行清單是對某個版本的 --help 全文逐項對過的；versions.json 換版本而沒重對 → 失敗
        vj = json.load(open(os.path.join(REPO, "versions.json"), encoding="utf-8"))
        doc = open(os.path.join(REPO, "docs", "agent-browser-command-audit.md"), encoding="utf-8").read()
        check("agent-browser 的指令對照表是對 versions.json 釘的版本做的（%s）" % (vj["agent-browser"]["version"]),
              ("audited version: %s" % vj["agent-browser"]["version"]) in doc, "versions.json=%s" % vj["agent-browser"]["version"])
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

        # ---- 審查抓到、原本測試沒抓到的洞（2026-11-22）：每個洞一個反例 ----
        neb("open", "https://mail.google.com/mail/u/0")
        rc, o = neb("find", "label", "To", "fill", "someone@example.com")
        check("敏感網站 find … fill → 拒", rc == 3, o)
        rc, o = neb("find", "role", "button", "click", "--name", "Send")
        check("敏感網站 find … click → 拒", rc == 3, o)
        rc, o = neb("find", "role", "button")
        check("敏感網站 find 不寫動作（預設 click）→ 拒", rc == 3, o)
        rc, o = neb("find", "text", "Inbox", "text")
        check("敏感網站 find … text（只讀）→ 放行", rc == 0, o)
        rc, o = neb("wait", "--fn", "fetch('/send',{method:'POST'})")
        check("敏感網站 wait --fn（等同 eval）→ 拒", rc == 3, o)
        rc, o = neb("wait", "500")
        check("敏感網站 wait <ms> → 放行", rc == 0, o)
        for cmd in (["eval", "1"], ["mouse", "move", "1", "1"], ["keyboard", "type", "x"], ["clipboard", "read"],
                    ["vitals"], ["a11y"], ["pushstate", "/x"], ["network", "route", "*"], ["set", "headers", "{}"],
                    ["cookies", "set", "a", "b"], ["storage", "local"], ["download", "@e3", "x"], ["highlight", "@e3"],
                    ["diff", "url", "https://a.example", "https://b.example"]):
            rc, o = neb(*cmd)
            check("敏感網站 %s → 拒" % " ".join(cmd[:2]), rc == 3, o)
        rc, o = neb("open", "javascript:alert(document.cookie)")
        check("open javascript: → 拒", rc == 3 and "javascript" in o, o)
        rc, o = neb("open", "data:text/html,<script>x</script>")
        check("open data: → 拒", rc == 3, o)
        rc, o = neb("open", "file:///C:/Windows/win.ini")
        check("open file: → 拒", rc == 3, o)
        rc, o = neb("open", "about:blank")
        check("open about:blank → 放行（引擎在用）", rc == 0, o)
        rc, o = neb("open", "localhost:8065")
        check("open 主機:埠（localhost:8065）不被誤判成 scheme → 放行", rc == 0, o)
        neb("open", "https://example.com")
        for cmd in (["chat", "reply hi to my boss"], ["connect", "9222"], ["flow", "run", "x"], ["auth", "login", "x"],
                    ["webmcp", "invoke", "send"], ["plugin", "run", "x", "y"], ["confirm", "1"], ["mcp"], ["get", "cdp-url"]):
            rc, o = neb(*cmd)
            check("任何網站 %s → 拒（繞過逐步檢查）" % " ".join(cmd[:2]), rc == 3, o)
        rc, o = neb("open", "https://example.com", "--init-script", "evil.js")
        check("子指令後面的 --init-script → 拒", rc == 3, o)
        rc, o = neb("snapshot", "--user-agent=Mozilla/5.0")
        check("子指令後面的 --user-agent=… → 拒（會騙過發件匣的自動化檢查）", rc == 3, o)
        rc, o = neb("--headers", "{\"Authorization\":\"x\"}", "open", "https://example.com")
        check("子指令前面的 --headers → 拒", rc == 3, o)
        rc, o = neb("batch", "open https://mail.google.com/mail/u/0", "fill @e1 hi")
        check("batch 夾帶「到敏感網站再 fill」→ 拒", rc == 3 and "第 2 個" in o, o)
        rc, o = neb("batch", "open https://example.com", "screenshot x.png")
        check("batch 全是允許的指令 → 放行", rc == 0, o)
        rc, o = neb("batch", "--bail")
        check("batch 從 stdin 讀（neb 看不到內容）→ 拒", rc == 3, o)
        rc, o = neb("tab", "new", "https://web.whatsapp.com/")
        check("tab new 到未授權的敏感網站 → 拒", rc == 3, o)
        rc, o = neb("read", "https://web.whatsapp.com/")
        check("read 未授權的敏感網站 → 拒", rc == 3, o)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
