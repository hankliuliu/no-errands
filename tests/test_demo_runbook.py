#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範劇本測試（純 stdlib、無瀏覽器；三平台 CI）：policy 的 demo/sensitive_domains、outbox 的 runbook 核准、
neb 的窄例外與 login 規則、demo_runbook 的 sends/rehearse/live/learn/apply/freeze/reset 與 persist=off。
用假的 agent-browser；每個審查指出的洞各有反例。"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(REPO, "skills", "no-errands", "scripts")
NEB, POL, OUTBOX, RB = (os.path.join(S, n) for n in ("neb.py", "policy.py", "outbox.py", "demo_runbook.py"))
UA = "Mozilla/5.0 (Windows NT 10.0) Firefox/140.0"
SECRET = "pw-Zq81-secret"
R = []

FAKE = r'''
import json, os, sys
d = os.environ["NE_FAKE_DIR"]
a = sys.argv[1:]
sess, prof = "default", ""
while a and a[0].startswith("--"):
    if a[0] == "--session": sess = a[1]
    if a[0] == "--profile": prof = a[1]
    a = a[2:] if a[0] in ("--session", "--profile") else a[1:]
st = os.path.join(d, "url-%s.txt" % sess)
sub = a[0] if a else ""
with open(os.path.join(d, "ab-calls.jsonl"), "a", encoding="utf-8") as log:
    log.write(json.dumps(a, ensure_ascii=False) + "\n")
def cur():
    return open(st).read() if os.path.exists(st) else "about:blank"
def rd(n, dflt):
    p = os.path.join(d, n)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else dflt
if sub == "open":
    open(st, "w").write(a[1]); print("ok")
elif sub == "get" and a[1:2] == ["url"]:
    print(cur())
elif sub == "get" and a[1:2] == ["text"]:
    if cur().endswith("/api/v4/users/me"):
        print(json.dumps({"username": os.path.basename(os.path.normpath(prof))}))
    else:
        print(open(os.path.join(d, "page.txt"), encoding="utf-8").read())
elif sub == "snapshot":
    print('- textbox "訊息" [ref=e1]\n- button "核准（Gate 1）" [ref=e2]\n- button "Send" [ref=e3]\n'
          '- button "核准（Gate 1）" [ref=e4]')
elif sub == "eval":
    js = a[1]
    obj = rd("login.json", None) if "password" in js and "querySelectorAll" in js else rd("focus.json", None)
    print(json.dumps(json.dumps(obj)))
elif sub == "batch":
    open(os.path.join(d, "batch-stdin.txt"), "a", encoding="utf-8").write(sys.stdin.read() + "\n"); print("ok")
elif sub == "screenshot":
    open(a[1], "wb").write(b"PNG"); print("ok")
else:
    print("did " + sub)
'''


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:400]))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def http(url, data=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


RUNBOOK = {
    "title": "迷你劇本", "target": {"base_url": "http://localhost:8065", "host": "localhost:8065", "home_path": "/t",
                                  "whoami_path": "/api/v4/users/me"},
    "accounts": {"demo_a": {"profile": "profiles/demo_a"}, "demo_b": {"profile": "profiles/demo_b"}},
    "vars": {"case_id": {"pattern": "C-[0-9a-f]{8}"}, "free": {"pattern": ".+"}},
    "reset": {"commands": ["echo reset-ok"]},
    "steps": [
        {"id": "s1", "account": "demo_a", "where": "私訊", "kind": "指令", "say": "打指令",
         "actions": [{"do": "open", "url": "/t/messages/@bot"},
                     {"do": "fill", "target": {"role": "textbox", "names": ["訊息"]}, "text": "/case help"},
                     {"do": "press", "key": "Enter"},
                     {"do": "wait_text", "text": "說明", "budget_s": 5},
                     {"do": "capture", "var": "case_id", "regex": "C-[0-9a-f]{8}"}]},
        {"id": "s2", "account": "demo_b", "where": "私訊", "kind": "按鈕", "advance": "cue",
         "actions": [{"do": "open", "url": "/t/messages/@bot"},
                     {"do": "click", "target": {"role": "button", "names": ["核准（Gate 1）"]}},
                     {"do": "fill", "target": {"role": "textbox", "names": ["訊息"]}, "text": "/case show {case_id}"}]},
        {"id": "s3", "account": "demo_b", "where": "私訊", "kind": "測試用自由變數",
         "actions": [{"do": "fill", "target": {"role": "textbox", "names": ["訊息"]}, "text": "/x {free}"}]},
    ],
}


def main():
    home = tempfile.mkdtemp(prefix="ne-rb-")
    port = free_port()
    fk = os.path.join(home, "fake_ab.py"); open(fk, "w", encoding="utf-8").write(FAKE)
    open(os.path.join(home, "page.txt"), "w", encoding="utf-8").write("使用說明 C-1234abcd 其他")
    json.dump({"outbox": {"port": port}}, open(os.path.join(home, "policy.json"), "w"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, NE_FAKE_AB_PY=fk, NE_FAKE_DIR=home, PYTHONIOENCODING="utf-8")
    rbdir = os.path.join(home, "demo"); os.makedirs(rbdir)
    rbp = os.path.join(rbdir, "runbook.json")
    json.dump(RUNBOOK, open(rbp, "w", encoding="utf-8"), ensure_ascii=False)

    def focus(names, value, typ="textarea", id_="x"):
        json.dump({"names": names, "value": value, "type": typ, "id": id_},
                  open(os.path.join(home, "focus.json"), "w", encoding="utf-8"), ensure_ascii=False)

    def run(script, *a, stdin=None, extra_env=None):
        p = subprocess.run([sys.executable, script] + list(a), capture_output=True, text=True, encoding="utf-8",
                           env=dict(env, **(extra_env or {})), timeout=180, input=stdin)
        txt = p.stdout
        last = txt[txt.rfind("\n{") + 1:] if "\n{" in txt else txt[txt.find("{"):] if "{" in txt else "{}"
        try:
            return p.returncode, json.loads(last), txt
        except Exception:
            return p.returncode, {}, txt + p.stderr

    def neb(profile, *a, stdin=None, sess="t", extra_env=None):
        rc, o, txt = run(NEB, "--session", sess, "--profile", os.path.join(rbdir, "profiles", profile), *a,
                         stdin=stdin, extra_env=extra_env)
        return rc, txt

    def rbj(*a):
        return run(RB, *a)

    base = "http://127.0.0.1:%d/" % port
    key = None

    def approve(aid, **extra):
        nonlocal key
        st = json.loads(http(base + "api/state")[1])
        p = [d for d in st["pending"] if d["id"] == aid][0]
        if key is None:
            key = json.loads(re.search(r"const KEY=(\"[^\"]+\")", http(base)[1]).group(1))
        body = {"id": aid, "action": "approve", "key": key, "rev": p["rev"]}
        body.update(extra)
        return http(base + "api/decide", body), p

    try:
        # ---------- policy
        rc, o, _ = run(POL, "show")
        check("policy 預設 demo.persist=on、learn=propose、sensitive_domains=[]",
              o["policy"]["demo"] == {"persist": "on", "learn": "propose"} and o["policy"]["sensitive_domains"] == [], o)
        rc, o, _ = run(POL, "set", "demo.persist", "off")
        check("修改 demo.persist 沒附 --reason → 拒", rc == 3, o)

        # ---------- localhost 未登記：neb 不設防（鎖住現況，提醒必須登記）
        neb("demo_a", "open", "http://localhost:8065/t")
        rc, o = neb("demo_a", "fill", "@e1", "anything")
        check("未登記的 localhost:8065 → 當一般網站（所以示範主機必須登記）", rc == 0, o)

        run(POL, "domain", "add", "localhost:8065", "demo-mm", "--demo-accounts", "demo_a,demo_b",
            "--agent-login", "demo_a", "--reason", "test")
        run(POL, "domain", "add", "localhost:8066", "demo-mm", "--demo-accounts", "demo_a,demo_b", "--reason", "test")
        rc, o, _ = run(POL, "show")
        ent = [e for e in o["policy"]["sensitive_domains"] if e["host"] == "localhost:8065"][0]
        check("登記示範主機：帳號與 agent 登入的同意記在政策檔", ent.get("demo") and ent["demo_accounts"] == ["demo_a", "demo_b"]
              and ent["agent_login"] == ["demo_a"], ent)
        rc, o = neb("demo_a", "snapshot", "-i")
        check("登記後未授權 → 拒（同意閘門）", rc == 3 and "同意閘門" in o, o)
        run(POL, "grant", "demo-mm", "--reason", "test")
        # loopback 的各種寫法、userinfo 都視為同一台
        for u in ("http://127.0.0.1:8065/t", "http://x@localhost:8065/t", "http://[::1]:8065/t", "http://0.0.0.0:8065/t",
                  "http://LOCALHOST.:8065/t", "http://127.1.2.3:8065/t", "http://2130706433:8065/t",
                  "http://0x7f000001:8065/t", "http://127.1:8065/t"):
            neb("demo_a", "open", u); neb("demo_a", "snapshot", "-i")
            rc, o = neb("demo_a", "fill", "@e1", "x")
            check("同一台換寫法（%s）也受保護 → fill 拒" % u.split("//")[1].split("/")[0], rc == 3, o)
        neb("demo_a", "open", "http://localhost:8065/t"); neb("demo_a", "snapshot", "-i")
        rc, o = neb("demo_a", "fill", "@e1", "/case help")
        check("登記＋授權、沒帶核准 → fill 拒", rc == 3, o)
        rc, o = neb("demo_a", "click", "@e2")
        check("登記的網域上沒帶核准的點擊 → 拒（「核准」不是送出字眼也要擋）", rc == 3 and "劇本" in o, o)
        rc, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", "nope", "--step", "s1")
        check("帶不存在的核准 → 拒並要求重新核准", rc == 3 and "需重新核准" in o, o)

        # ---------- 劇本 → 送出清單
        bad = json.loads(json.dumps(RUNBOOK)); bad["vars"]["free"] = {"shown": "隨便"}
        badp = os.path.join(rbdir, "bad.json"); json.dump(bad, open(badp, "w", encoding="utf-8"), ensure_ascii=False)
        rc, o, _ = rbj("validate", badp)
        check("變數沒宣告 pattern → validate 拒（沒有預設 .+）", rc == 1 and any("pattern" in e for e in o["errors"]), o)
        rc, o, _ = rbj("validate", rbp)
        check("validate 通過、列出會執行的 shell 指令", rc == 0 and o["sends"] == 3
              and o["shell_commands"] == [{"where": "reset", "cmd": "echo reset-ok"}], o)
        os.makedirs(os.path.join(home, ".git"))
        rc, o, _ = rbj("validate", rbp)
        check("劇本／紀錄資料夾在 git 工作目錄裡 → 警告", len(o.get("warnings", [])) == 2, o)
        os.rmdir(os.path.join(home, ".git"))
        rc, o, _ = rbj("sends", rbp)
        doc = json.load(open(o["file"], encoding="utf-8"))
        s2 = [s for s in doc["sends"] if s["step"] == "s2"][0]
        check("送出清單每個動作都帶網址路徑；變數是具名群組", all(i["at"] == "/t/messages/@bot" for i in s2["inputs"])
              and s2["clicks"][0]["at"] == "/t/messages/@bot" and "(?P<v_case_id_0>" in s2["inputs"][0]["pattern"], s2)
        s3 = [s for s in doc["sends"] if s["step"] == "s3"][0]
        check("沒有 open 的步驟沿用該帳號上一步的路徑", s3["inputs"][0]["at"] == "/t/messages/@bot", s3)

        # ---------- outbox 格式檢查與天數上限
        bd = json.loads(json.dumps(doc)); bd["sends"][0]["inputs"][0] = {"at": "/t", "fields": ["訊息"], "pattern": ".+"}
        bdp = os.path.join(home, "bd.json"); json.dump(bd, open(bdp, "w", encoding="utf-8"), ensure_ascii=False)
        rc, o, _ = run(OUTBOX, "draft", "--service", "runbook", "--to", "localhost:8065", "--body-file", bdp)
        check("清單裡的變動輸入沒有具名群組 → 發件匣拒收", rc == 2, o)
        bd = json.loads(json.dumps(doc))
        s2b = [s for s in bd["sends"] if s["step"] == "s2"][0]
        s2b["inputs"][0]["pattern"] = s2b["inputs"][0]["pattern"].replace("C-[0-9a-f]{8}", ".+")
        json.dump(bd, open(bdp, "w", encoding="utf-8"), ensure_ascii=False)
        rc, o, _ = run(OUTBOX, "draft", "--service", "runbook", "--to", "localhost:8065", "--body-file", bdp)
        check("pattern 與卡片顯示的文字＋變數格式不一致 → 發件匣拒收", rc == 2 and "不一致" in o.get("error", ""), o)
        bd = json.loads(json.dumps(doc)); bd["sends"][0]["keys"] = ["Enter"]
        json.dump(bd, open(bdp, "w", encoding="utf-8"), ensure_ascii=False)
        rc, o, _ = run(OUTBOX, "draft", "--service", "runbook", "--to", "localhost:8065", "--body-file", bdp)
        check("按鍵沒有網址路徑 → 發件匣拒收", rc == 2, o)
        bd = json.loads(json.dumps(doc)); bd["days"] = 30
        json.dump(bd, open(bdp, "w", encoding="utf-8"), ensure_ascii=False)
        rc, o, _ = run(OUTBOX, "draft", "--service", "runbook", "--to", "localhost:8065", "--body-file", bdp)
        st = json.loads(http(base + "api/state")[1])
        p30 = [d for d in st["pending"] if d["id"] == o.get("id")][0]
        check("清單要求 30 天 → 卡片顯示伺服器採用的 7 天", p30.get("days_effective") == 7, p30.get("days_effective"))
        (code, t), _ = approve(o["id"])
        rc, o30, _ = run(OUTBOX, "status", o["id"])
        ap30 = json.load(open(os.path.join(home, "outbox", "approved", o["id"] + ".json"), encoding="utf-8"))["approval"]
        check("核准記錄的天數被伺服器限制在 7 天、預設可執行 3 次", ap30["days"] == 7 and ap30["max_runs"] == 3, ap30)
        http(base + "api/decide", {"id": o["id"], "action": "revoke", "key": key})

        # ---------- 核准
        rc, o, _ = rbj("rehearse", rbp, "--approval", "nope")
        check("沒核准不能 rehearse", rc == 3, o)
        rc, o, txt = rbj("request-approval", rbp)
        check("request-approval 起草到發件匣", rc == 0 and o.get("ok"), txt)
        aid = o.get("id")
        code, page = http(base)
        check("頁面：強制的網址路徑與內容、agent 說明分開、真正的格式、可執行次數",
              "網址路徑（強制）" in page and "agent 的說明（不是強制內容）" in page and "不含空白、最長 64 字»" in page
              and "select class=\"runs\"" in page)
        (code, t), p1 = approve(aid, action="edit", text="x")
        check("劇本不能在頁面上修改後核准", code == 400, t)
        (code, t), _ = approve(aid, max_runs=99)
        check("可執行次數超出 1–10 → 拒", code == 400, t)
        (code, t), _ = approve(aid, max_runs=10)
        check("使用者核准劇本（選 10 次）→ approved", code == 200 and json.loads(t)["state"] == "approved", t)
        rc, o, _ = run(OUTBOX, "status", aid)
        check("status 回 approved 與雜湊、到期時間", o.get("state") == "approved" and o.get("sends_sha256"), o)

        # ---------- neb 窄例外
        neb("demo_a", "open", "http://localhost:8065/t/messages/@bot")
        neb("demo_a", "snapshot", "-i")
        rc, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid, "--step", "s1")
        check("核准後照劇本、在核准的頁面輸入 → 放行", rc == 0, o)
        rc, o = neb("demo_a", "type", "@e1", "/case help", "--runbook-approval", aid, "--step", "s1")
        check("type（附加輸入）不在例外裡 → 拒", rc == 3, o)
        rc, o = neb("demo_a", "fill", "@e1", "/case helps", "--runbook-approval", aid, "--step", "s1")
        check("改一個字 → 拒", rc == 3 and "需重新核准" in o, o)
        focus(["訊息"], "/case help")
        rc, o = neb("demo_a", "press", "Enter", "--runbook-approval", aid, "--step", "s1")
        check("焦點欄位與內容都等於核准的輸入 → Enter 放行", rc == 0, o)
        focus(["訊息"], "/case help 殘留的草稿")
        rc, o = neb("demo_a", "press", "Enter", "--runbook-approval", aid, "--step", "s1")
        check("輸入框裡的內容和核准的不同 → Enter 拒", rc == 3 and "焦點" in o, o)
        focus(["搜尋"], "/case help")
        rc, o = neb("demo_a", "press", "Enter", "--runbook-approval", aid, "--step", "s1")
        check("焦點在別的欄位 → Enter 拒", rc == 3, o)
        os.remove(os.path.join(home, "focus.json"))
        rc, o = neb("demo_a", "press", "Enter", "--runbook-approval", aid, "--step", "s1")
        check("讀不到焦點 → Enter 拒", rc == 3, o)
        focus(["訊息"], "/case help")
        rc, o = neb("demo_a", "press", "Tab", "--runbook-approval", aid, "--step", "s1")
        check("沒核准的按鍵 → 拒", rc == 3, o)
        neb("demo_a", "open", "http://localhost:8065/t/channels/town-square"); neb("demo_a", "snapshot", "-i")
        rc, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid, "--step", "s1")
        check("同樣的欄位名與文字、但在別的頻道 → 拒（位置有強制）", rc == 3 and "頁面" in o, o)
        neb("demo_a", "open", "http://localhost:8065/t/messages/@bot"); neb("demo_a", "snapshot", "-i")
        rc, o = neb("demo_a", "click", "@e2", "--runbook-approval", aid, "--step", "s1")
        check("別步的點擊（s1 沒有核准點「核准」）→ 拒", rc == 3, o)
        neb("demo_b", "open", "http://localhost:8065/t/messages/@bot"); neb("demo_b", "snapshot", "-i")
        rc, o = neb("demo_b", "click", "@e2", "--runbook-approval", aid, "--step", "s2")
        check("s2 由 demo_b 點「核准（Gate 1）」→ 放行", rc == 0, o)
        rc, o = neb("demo_b", "fill", "@e1", "/case show C-1234abcd", "--runbook-approval", aid, "--step", "s2")
        check("變動部分符合規則（C-編號）→ 放行", rc == 0, o)
        rc, o = neb("demo_b", "fill", "@e1", "/case show C-1234abcd; rm", "--runbook-approval", aid, "--step", "s2")
        check("變動部分夾帶其他文字 → 拒", rc == 3, o)
        rc, o = neb("demo_b", "fill", "@e1", "/x ok-value", "--runbook-approval", aid, "--step", "s3")
        check("寬鬆變數（.+）的單一 token → 放行", rc == 0, o)
        rc, o = neb("demo_b", "fill", "@e1", "/x two words", "--runbook-approval", aid, "--step", "s3")
        check("寬鬆變數夾帶空白 → 硬上限拒", rc == 3, o)
        rc, o = neb("demo_b", "fill", "@e1", "/x " + "a" * 65, "--runbook-approval", aid, "--step", "s3")
        check("寬鬆變數超過 64 字 → 硬上限拒", rc == 3, o)
        rc, o = neb("demo_b", "fill", "@e1", "/x a\nb", "--runbook-approval", aid, "--step", "s3")
        check("寬鬆變數夾帶換行 → 硬上限拒", rc == 3, o)
        rc, o = neb("demo_a", "click", "@e2", "--runbook-approval", aid, "--step", "s2")
        check("換帳號（demo_a 執行 demo_b 的步驟）→ 拒", rc == 3 and "示範帳號" in o, o)
        neb("demo_c", "open", "http://localhost:8065/t/messages/@bot"); neb("demo_c", "snapshot", "-i")
        rc, o = neb("demo_c", "click", "@e2", "--runbook-approval", aid, "--step", "s2")
        check("沒登記的帳號（demo_c）→ 拒", rc == 3 and "登記的示範帳號" in o, o)
        rc, o = neb("demo_b", "click", "@e3", "--runbook-approval", aid, "--step", "s2")
        check("核准清單外的按鈕（Send）→ 拒", rc == 3, o)
        neb("demo_b", "open", "http://localhost:8066/t/messages/@bot"); neb("demo_b", "snapshot", "-i")
        rc, o = neb("demo_b", "click", "@e2", "--runbook-approval", aid, "--step", "s2")
        check("換主機（另一個登記的主機）→ 拒", rc == 3 and "目標主機" in o, o)
        neb("demo_b", "open", "http://127.0.0.1:8065/t/messages/@bot"); neb("demo_b", "snapshot", "-i")
        rc, o = neb("demo_b", "click", "@e2", "--runbook-approval", aid, "--step", "s2")
        check("127.0.0.1:8065 視為核准的 localhost:8065（同一台）→ 放行", rc == 0, o)
        rc, o = neb("demo_b", "eval", "1", "--runbook-approval", aid, "--step", "s2")
        check("劇本執行時 eval → 拒", rc == 3, o)
        run(POL, "grant", "gmail", "--reason", "test")
        neb("demo_a", "open", "https://mail.google.com/mail/u/0"); neb("demo_a", "snapshot", "-i")
        rc, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid, "--step", "s1")
        check("內建敏感 app（Gmail，已授權）帶了核准也一律拒", rc == 3 and "示範用途" in o, o)
        led = open(os.path.join(home, "neb-ledger.jsonl"), encoding="utf-8").read()
        check("ledger 有劇本使用紀錄、不含輸入內容", '"runbook"' in led and "/case help" not in led)

        # ---------- 可執行次數
        rc, o, _ = rbj("request-approval", rbp)
        aid1 = o["id"]
        approve(aid1, max_runs=1)
        gap0 = {"NE_RB_REUSE_GAP_S": "0"}
        neb("demo_a", "open", "http://localhost:8065/t/messages/@bot"); neb("demo_a", "snapshot", "-i")
        rc1, _ = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid1, "--step", "s1", extra_env=gap0)
        rc2, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid1, "--step", "s1", extra_env=gap0)
        check("選 1 次：第一次放行、第二次 → 拒（要重新核准）", rc1 == 0 and rc2 == 3 and "上限" in o, o)
        rc3, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid1, "--step", "s1",
                     extra_env={"NE_RB_REUSE_GAP_S": "9999"})
        check("環境變數不能把重試視窗調寬", rc3 == 3, o)
        rc, o, _ = rbj("preflight", rbp, "--approval", aid1)
        rem = [c for c in o.get("checks", []) if c["check"].startswith("每個送出步驟還剩")]
        check("preflight：有步驟次數用完 → 失敗並指出哪一步", rem and not rem[0]["ok"] and "s1" in rem[0]["detail"], rem)
        rc, o, _ = rbj("preflight", rbp, "--approval", aid1, "--demo-at", "2099-01-01 10:00")
        exp = [c for c in o.get("checks", []) if c["check"].startswith("核准到期")]
        check("preflight：核准會在預定示範前到期 → 失敗", exp and not exp[0]["ok"], exp)

        # ---------- agent 撤回自己的草稿
        rc, o, _ = rbj("request-approval", rbp)
        rc, w, _ = run(OUTBOX, "withdraw", o["id"], "--why", "改用新版")
        rc2, st_, _ = run(OUTBOX, "status", o["id"])
        check("agent 撤回待核准的草稿 → 不再能核准", rc == 0 and st_.get("state") == "rejected", (w, st_))
        rc, w, _ = run(OUTBOX, "withdraw", aid)
        check("已核准的劇本 agent 不能撤回（要使用者撤銷）", rc == 3, w)

        # ---------- login 規則
        rc, o, _ = rbj("login", rbp, "--account", "demo_b", "--secret-cmd", "echo " + SECRET)
        check("沒在政策檔同意 agent 登入的帳號 → 拒", rc == 3, o)
        json.dump({"user_id": "input_loginId", "pw_id": "input_password"}, open(os.path.join(home, "login.json"), "w"))
        neb("demo_a", "open", "http://localhost:8065/t/channels/town-square", sess="L")
        rc, o = neb("demo_a", "login", stdin="任意文字\n", sess="L")
        check("惡意劇本：登入規則被指到頻道頁 → 拒（不在登入頁不填）", rc == 3 and "登入頁" in o, o)
        neb("demo_a", "open", "http://localhost:8066/login", sess="L")
        rc, o = neb("demo_a", "login", stdin=SECRET + "\n", sess="L")
        check("另一台主機上該帳號沒有 agent 登入同意 → 拒", rc == 3, o)
        neb("demo_a", "open", "http://localhost:8065/login", sess="L")
        json.dump({"error": "password_fields=0"}, open(os.path.join(home, "login.json"), "w"))
        rc, o = neb("demo_a", "login", stdin=SECRET + "\n", sess="L")
        check("登入頁上沒有 password 型別欄位（例如被換成訊息框）→ 拒", rc == 3, o)
        json.dump({"user_id": "input_loginId", "pw_id": "input_password"}, open(os.path.join(home, "login.json"), "w"))
        focus(["密碼"], "", typ="text", id_="post_textbox")
        rc, o = neb("demo_a", "login", stdin=SECRET + "\n", sess="L")
        check("焦點不在密碼欄 → 不按 Enter", rc == 3 and "Enter" in o, o)
        focus(["密碼"], "", typ="password", id_="input_password")
        open(os.path.join(home, "ab-calls.jsonl"), "w").close()
        rc, o = neb("demo_a", "login", stdin=SECRET + "\n", sess="L")
        argv_log = open(os.path.join(home, "ab-calls.jsonl"), encoding="utf-8").read()
        stdin_log = open(os.path.join(home, "batch-stdin.txt"), encoding="utf-8").read()
        check("正常登入：密碼只經 stdin，不在命令列參數", rc == 0 and SECRET not in argv_log and SECRET in stdin_log
              and '"Enter"' in argv_log, argv_log[-300:])
        led = open(os.path.join(home, "neb-ledger.jsonl"), encoding="utf-8").read()
        check("ledger 不含密碼", SECRET not in led)

        # ---------- preflight 身分
        rc, o, txt = rbj("preflight", rbp)
        ids = [c for c in o.get("checks", []) if c["check"].startswith("登入有效且身分正確")]
        check("preflight 確認每個 profile 登入的帳號名", len(ids) == 2 and all(c["ok"] for c in ids), o)

        # ---------- rehearse（迷你劇本在假瀏覽器跑通）
        focus(["訊息"], "/case help")
        rc, o, txt = rbj("rehearse", rbp, "--approval", aid)
        check("rehearse 三步都成功", rc == 0 and o.get("steps_ok") == 3, txt)
        run_dir = o.get("run_dir") or ""
        rj = json.load(open(os.path.join(run_dir, "run.json"), encoding="utf-8")) if run_dir else {}
        check("紀錄有每步耗時、選取方式、截圖", rj and all("dur_s" in s for s in rj["steps"])
              and any(a.get("used") for a in rj["steps"][1]["actions"]) and rj["steps"][0].get("shot"), rj)
        raw = open(os.path.join(run_dir, "run.json"), encoding="utf-8").read() if run_dir else ""
        check("run.json 不含輸入文字", raw and "/case help" not in raw and "/case show" not in raw)
        check("rehearse 成功的步驟更新 Plan B 截圖", os.path.exists(os.path.join(rbdir, "planb", "s2.png")))
        calls = open(os.path.join(home, "ab-calls.jsonl"), encoding="utf-8").read()
        check("變數代入：s2 輸入的是 capture 到的編號", "/case show C-1234abcd" in calls)

        # ---------- live 在 cue 步驟停下、resume 接著跑
        rc, o, txt = rbj("live", rbp, "--approval", aid)
        check("live 在 advance=cue 的 s2 前停下等講者", rc == 0 and (o.get("stopped") or {}).get("waiting_cue") == "s2", txt)
        rc, o, txt = rbj("live", rbp, "--approval", aid, "--resume", o.get("run_dir") or "")
        check("講者示意後 --resume 跑完", rc == 0 and o.get("steps_ok") == 3 and not o.get("stopped"), txt)

        # ---------- learn / freeze / apply
        rc, o, txt = rbj("learn", rbp)
        check("learn 產生提案（等待預算依觀測值調整）", rc == 0 and o.get("n_changes", 0) >= 1
              and any("budget_s" in x for x in o.get("diff", [])), txt)
        prop = o.get("proposal")
        rbj("freeze", rbp, "--reason", "重要示範前凍結")
        rc, o, _ = rbj("apply", rbp, prop, "--reason", "使用者同意")
        check("凍結時不能套用", rc == 3 and "凍結" in o.get("error", ""), o)
        rbj("unfreeze", rbp, "--reason", "示範結束")
        rc, o, _ = rbj("apply", rbp, prop)
        check("套用必須附 --reason", rc == 3, o)
        rc, o, _ = rbj("apply", rbp, prop, "--reason", "使用者同意")
        check("套用等待預算的修改 → 不需重新核准", rc == 0 and o.get("needs_reapproval") is False, o)
        rc, o, txt = rbj("rehearse", rbp, "--approval", aid)
        check("套用後同一份核准仍有效（送出內容沒變）", rc == 0, txt)

        # ---------- 劇本送出內容改了 → 要重新核准
        rb = json.load(open(rbp, encoding="utf-8"))
        rb["steps"][0]["actions"][1]["text"] = "/case whoami"
        json.dump(rb, open(rbp, "w", encoding="utf-8"), ensure_ascii=False)
        rc, o, _ = rbj("rehearse", rbp, "--approval", aid)
        check("改了送出文字 → 引擎拒絕執行、要求重新核准", rc == 3 and "重新核准" in o.get("error", ""), o)
        json.dump(RUNBOOK, open(rbp, "w", encoding="utf-8"), ensure_ascii=False)

        # ---------- reset：同意記在政策檔、綁指令雜湊
        rc, o, _ = rbj("reset", rbp)
        check("政策檔沒有重置同意 → 拒並列出指令與雜湊", rc == 3 and o.get("commands_sha256"), o)
        run(POL, "set", "demo.reset_consent", o["commands_sha256"], "--reason", "使用者：每次排練後還原")
        rc, o, _ = rbj("reset", rbp)
        check("同意了這組指令 → 執行", rc == 0 and o.get("ok"), o)
        rb = json.load(open(rbp, encoding="utf-8")); rb["reset"]["commands"] = ["echo something-else"]
        json.dump(rb, open(rbp, "w", encoding="utf-8"), ensure_ascii=False)
        rc, o, _ = rbj("reset", rbp)
        check("還原指令改了 → 要重新同意", rc == 3, o)
        json.dump(RUNBOOK, open(rbp, "w", encoding="utf-8"), ensure_ascii=False)

        # ---------- persist=off：一次性
        run(POL, "set", "demo.persist", "off", "--reason", "test")
        n_before = len(os.listdir(os.path.join(rbdir, "runs")))
        rc, o, txt = rbj("rehearse", rbp, "--approval", aid)
        check("persist=off：照樣執行、不留紀錄", rc == 0 and o.get("run_dir") is None
              and len(os.listdir(os.path.join(rbdir, "runs"))) == n_before, txt)
        rc, o, _ = rbj("learn", rbp)
        check("persist=off：learn 不提出修改", rc == 0 and o.get("proposal") is None, o)
        run(POL, "set", "demo.persist", "on", "--reason", "test")

        # ---------- 撤銷、到期
        code, t = http(base + "api/decide", {"id": aid, "action": "revoke", "key": key})
        check("歷史頁撤銷核准", code == 200, t)
        neb("demo_a", "open", "http://localhost:8065/t/messages/@bot"); neb("demo_a", "snapshot", "-i")
        rc, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid, "--step", "s1")
        check("撤銷後 → 拒", rc == 3 and "撤銷" in o, o)
        rc, o, txt = rbj("request-approval", rbp)
        aid2 = o.get("id")
        approve(aid2)
        ap = os.path.join(home, "outbox", "approved", aid2 + ".json")
        rec = json.load(open(ap, encoding="utf-8")); rec["approval"]["expires_ts"] = time.time() - 1
        json.dump(rec, open(ap, "w", encoding="utf-8"), ensure_ascii=False)
        rc, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid2, "--step", "s1")
        check("到期後 → 拒", rc == 3 and "到期" in o, o)
        rc, o, _ = run(OUTBOX, "status", aid2)
        check("status 回 expired", o.get("state") == "expired", o)
    finally:
        try:
            for line in open(os.path.join(home, "outbox", "server.log"), encoding="utf-8"):
                if '"pid"' in line:
                    pid = json.loads(line)["pid"]
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
                    else:
                        os.kill(pid, 15)
        except Exception:
            pass
        shutil.rmtree(home, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (len(R) - sum(R), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
