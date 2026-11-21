#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範劇本測試（純 stdlib、無瀏覽器；三平台 CI）：policy 的 demo/sensitive_domains、outbox 的 runbook 核准、
neb 的窄例外、demo_runbook 的 sends/rehearse/learn/apply/freeze 與 persist=off。用假的 agent-browser。"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(REPO, "skills", "no-errands", "scripts")
NEB, POL, OUTBOX, RB = (os.path.join(S, n) for n in ("neb.py", "policy.py", "outbox.py", "demo_runbook.py"))
UA = "Mozilla/5.0 (Windows NT 10.0) Firefox/140.0"
R = []

FAKE = r'''
import json, os, sys
d = os.environ["NE_FAKE_DIR"]
st = os.path.join(d, "url-" + "x" + ".txt")
a = sys.argv[1:]
while a and a[0].startswith("--"):
    a = a[2:] if a[0] in ("--session", "--profile") else a[1:]
sub = a[0] if a else ""
log = open(os.path.join(d, "ab-calls.jsonl"), "a", encoding="utf-8")
log.write(json.dumps(a, ensure_ascii=False) + "\n")
if sub == "open":
    open(st, "w").write(a[1]); print("ok")
elif sub == "get" and a[1:2] == ["url"]:
    print(open(st).read() if os.path.exists(st) else "about:blank")
elif sub == "get" and a[1:2] == ["text"]:
    print(open(os.path.join(d, "page.txt"), encoding="utf-8").read())
elif sub == "snapshot":
    print('- textbox "訊息" [ref=e1]\n- button "核准（Gate 1）" [ref=e2]\n- button "Send" [ref=e3]\n'
          '- button "核准（Gate 1）" [ref=e4]')
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
    "title": "迷你劇本", "target": {"base_url": "http://localhost:8065", "host": "localhost:8065", "home_path": "/t"},
    "accounts": {"demo_a": {"profile": "profiles/demo_a"}, "demo_b": {"profile": "profiles/demo_b"}},
    "vars": {"case_id": {"pattern": "C-[0-9a-f]{8}", "shown": "系統產生的案件編號"}},
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
                     {"do": "fill", "target": {"role": "textbox", "names": ["訊息"]}, "text": "/case show {case_id}"},
                     {"do": "press", "key": "Enter"}]},
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

    def run(script, *a):
        p = subprocess.run([sys.executable, script] + list(a), capture_output=True, text=True, encoding="utf-8",
                           env=env, timeout=120)
        try:
            return p.returncode, json.loads(p.stdout[p.stdout.find("{"):] if "{" in p.stdout else "{}"), p.stdout
        except Exception:
            return p.returncode, {}, p.stdout + p.stderr

    def neb(profile, *a):
        p = subprocess.run([sys.executable, NEB, "--session", "t", "--profile", os.path.join(rbdir, "profiles", profile)]
                           + list(a), capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        return p.returncode, p.stdout

    def rbj(*a):
        p = subprocess.run([sys.executable, RB] + list(a), capture_output=True, text=True, encoding="utf-8",
                           env=env, timeout=180)
        txt = p.stdout
        last = txt[txt.rfind("\n{") + 1:] if "\n{" in txt else txt
        try:
            return p.returncode, json.loads(last), txt
        except Exception:
            return p.returncode, {}, txt + p.stderr

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
        check("未登記的 localhost:8065 → 當一般網站（fill 放行；所以示範主機必須登記）", rc == 0, o)

        run(POL, "domain", "add", "localhost:8065", "demo-mm", "--reason", "test")
        run(POL, "domain", "add", "localhost:8066", "demo-mm", "--reason", "test")
        rc, o = neb("demo_a", "snapshot", "-i")
        check("登記後未授權 → 拒（同意閘門）", rc == 3 and "同意閘門" in o, o)
        run(POL, "grant", "demo-mm", "--reason", "test")
        neb("demo_a", "snapshot", "-i")
        rc, o = neb("demo_a", "fill", "@e1", "/case help")
        check("登記＋授權、沒帶核准 → fill 拒", rc == 3, o)
        rc, o = neb("demo_a", "click", "@e2")
        check("登記的網域上沒帶核准的點擊 → 拒（「核准」不是送出字眼也要擋）", rc == 3 and "劇本" in o, o)
        rc, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", "nope", "--step", "s1")
        check("帶不存在的核准 → 拒並要求重新核准", rc == 3 and "需重新核准" in o, o)

        # ---------- 劇本 → 送出清單 → 核准
        rc, o, _ = rbj("validate", rbp)
        check("validate 通過、2 個送出步驟", rc == 0 and o["sends"] == 2, o)
        rc, o, _ = rbj("rehearse", rbp, "--approval", "nope")
        check("沒核准不能 rehearse", rc == 3, o)
        rc, o, txt = rbj("request-approval", rbp)
        check("request-approval 起草到發件匣", rc == 0 and o.get("ok"), txt)
        aid = o.get("id")
        base = "http://127.0.0.1:%d/" % port
        st = json.loads(http(base + "api/state")[1])
        p1 = [d for d in st["pending"] if d["id"] == aid][0]
        check("發件匣辨識劇本並顯示標籤", p1["resolved"] and "迷你劇本" in p1["label"], p1)
        code, page = http(base)
        check("頁面有劇本表格的渲染程式", "核准整份劇本" in page and "rbTable" in page)
        key = json.loads(re.search(r"const KEY=(\"[^\"]+\")", page).group(1))
        code, t = http(base + "api/decide", {"id": aid, "action": "edit", "text": "x", "key": key, "rev": p1["rev"]})
        check("劇本不能在頁面上修改後核准", code == 400, t)
        code, t = http(base + "api/decide", {"id": aid, "action": "approve", "key": key, "rev": p1["rev"]})
        check("使用者核准劇本 → approved", code == 200 and json.loads(t)["state"] == "approved", t)
        rc, o, _ = run(OUTBOX, "status", aid)
        check("status 回 approved 與雜湊、到期時間", o.get("state") == "approved" and o.get("sends_sha256")
              and o.get("expires"), o)

        # ---------- neb 窄例外
        neb("demo_a", "open", "http://localhost:8065/t")
        neb("demo_a", "snapshot", "-i")
        rc, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid, "--step", "s1")
        check("核准後照劇本輸入 → 放行", rc == 0, o)
        rc, o = neb("demo_a", "fill", "@e1", "/case helps", "--runbook-approval", aid, "--step", "s1")
        check("改一個字 → 拒", rc == 3 and "需重新核准" in o, o)
        rc, o = neb("demo_a", "press", "Enter", "--runbook-approval", aid, "--step", "s1")
        check("核准過的按鍵 → 放行", rc == 0, o)
        rc, o = neb("demo_a", "press", "Tab", "--runbook-approval", aid, "--step", "s1")
        check("沒核准的按鍵 → 拒", rc == 3, o)
        rc, o = neb("demo_a", "click", "@e2", "--runbook-approval", aid, "--step", "s1")
        check("別步的點擊（s1 沒有核准點「核准」）→ 拒", rc == 3, o)
        neb("demo_b", "snapshot", "-i")
        rc, o = neb("demo_b", "click", "@e2", "--runbook-approval", aid, "--step", "s2")
        check("s2 由 demo_b 點「核准（Gate 1）」→ 放行", rc == 0, o)
        rc, o = neb("demo_b", "fill", "@e1", "/case show C-1234abcd", "--runbook-approval", aid, "--step", "s2")
        check("變動部分符合規則（C-編號）→ 放行", rc == 0, o)
        rc, o = neb("demo_b", "fill", "@e1", "/case show C-1234abcd; rm", "--runbook-approval", aid, "--step", "s2")
        check("變動部分夾帶其他文字 → 拒", rc == 3, o)
        rc, o = neb("demo_a", "click", "@e2", "--runbook-approval", aid, "--step", "s2")
        check("換帳號（demo_a 執行 demo_b 的步驟）→ 拒", rc == 3 and "示範帳號" in o, o)
        rc, o = neb("demo_b", "click", "@e3", "--runbook-approval", aid, "--step", "s2")
        check("核准清單外的按鈕（Send）→ 拒", rc == 3, o)
        neb("demo_b", "open", "http://localhost:8066/t"); neb("demo_b", "snapshot", "-i")
        rc, o = neb("demo_b", "click", "@e2", "--runbook-approval", aid, "--step", "s2")
        check("換主機（另一個登記的主機）→ 拒", rc == 3 and "目標主機" in o, o)
        rc, o = neb("demo_b", "eval", "1", "--runbook-approval", aid, "--step", "s2")
        check("劇本執行時 eval → 拒", rc == 3, o)
        led = open(os.path.join(home, "neb-ledger.jsonl"), encoding="utf-8").read()
        check("ledger 有劇本使用紀錄、不含輸入內容", '"runbook"' in led and "/case help" not in led)

        # ---------- rehearse（迷你劇本在假瀏覽器跑通）
        rc, o, txt = rbj("rehearse", rbp, "--approval", aid)
        check("rehearse 兩步都成功", rc == 0 and o.get("steps_ok") == 2, txt)
        run_dir = o.get("run_dir") or ""
        rj = json.load(open(os.path.join(run_dir, "run.json"), encoding="utf-8")) if run_dir else {}
        check("紀錄有每步耗時、選取方式、截圖", rj and all("dur_s" in s for s in rj["steps"])
              and any(a.get("used") for a in rj["steps"][1]["actions"]) and rj["steps"][0].get("shot"), rj)
        check("rehearse 成功的步驟更新 Plan B 截圖", os.path.exists(os.path.join(rbdir, "planb", "s2.png")))
        calls = open(os.path.join(home, "ab-calls.jsonl"), encoding="utf-8").read()
        check("變數代入：s2 輸入的是 capture 到的編號", "/case show C-1234abcd" in calls)

        # ---------- live 在 cue 步驟停下、resume 接著跑
        rc, o, txt = rbj("live", rbp, "--approval", aid)
        check("live 在 advance=cue 的 s2 前停下等講者", rc == 0 and (o.get("stopped") or {}).get("waiting_cue") == "s2", txt)
        rc, o, txt = rbj("live", rbp, "--approval", aid, "--resume", o.get("run_dir") or "")
        check("講者示意後 --resume 跑完 s2", rc == 0 and o.get("steps_ok") == 2 and not o.get("stopped"), txt)

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
        neb("demo_a", "open", "http://localhost:8065/t"); neb("demo_a", "snapshot", "-i")
        rc, o = neb("demo_a", "fill", "@e1", "/case help", "--runbook-approval", aid, "--step", "s1")
        check("撤銷後 → 拒", rc == 3 and "撤銷" in o, o)
        rc, o, txt = rbj("request-approval", rbp)
        aid2 = o.get("id")
        st = json.loads(http(base + "api/state")[1])
        p2 = [d for d in st["pending"] if d["id"] == aid2][0]
        http(base + "api/decide", {"id": aid2, "action": "approve", "key": key, "rev": p2["rev"]})
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
