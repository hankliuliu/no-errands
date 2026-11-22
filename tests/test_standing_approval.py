#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範劇本的長期核准（純 stdlib、無瀏覽器；三平台 CI）。

由發件匣伺服器依政策決定，不是 agent 去按：
  1. policy：domain set … runbook_approval standing 只給示範用途、非內建敏感 app 的主機；demo.learn auto；outbox.port
  2. page 模式（預設）：劇本清單照舊進待核准
  3. standing＋沒有先前的頁面核准 → 進頁面（卡片寫明原因）
  4. standing＋使用者曾在頁面核准過同主機、帳號涵蓋 → 伺服器自動核准（via=standing、連到那筆、記 decide-log）
  5. 帳號超出先前頁面核准的範圍 → 進頁面；帳號不在登記範圍 → 進頁面
  6. 內建敏感 app（就算政策檔被手改成 standing）→ 永遠進頁面
  7. 使用者在頁面撤銷過 → 暫停；再在頁面核准一次 → 恢復
  8. 頁面：待核准徽章只算訊息；劇本在歷史的獨立一區
  9. demo_runbook：次數用完 → 長期核准下自動續一筆（同一份清單）；page 模式不自動續
用法：python tests/test_standing_approval.py   （成功印 ALL PASS，失敗 exit 1）
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(REPO, "skills", "no-errands", "scripts")
POL, OUTBOX = os.path.join(S, "policy.py"), os.path.join(S, "outbox.py")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/157.0 Safari/537.36"
R = []


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


def sends_doc(target, accounts, tag):
    return {"kind": "no-errands-runbook-sends", "target": target, "runbook_sha256": "sha-" + tag, "title": "測試劇本 " + tag,
            "sends": [{"step": "s%d" % i, "account": a, "kind": "指令", "where": "私訊",
                       "inputs": [{"at": "/t/messages/@bot", "fields": ["訊息"], "text": "/case help %s" % tag}]}
                      for i, a in enumerate(accounts, 1)]}


RUNBOOK = {
    "title": "迷你劇本", "target": {"base_url": "http://localhost:8065", "host": "localhost:8065", "home_path": "/t"},
    "accounts": {"demo_a": {"profile": "profiles/demo_a"}, "demo_b": {"profile": "profiles/demo_b"}},
    "vars": {},
    "steps": [
        {"id": "s1", "account": "demo_a", "where": "私訊", "kind": "指令", "say": "打指令",
         "actions": [{"do": "open", "url": "/t/messages/@bot"},
                     {"do": "fill", "target": {"role": "textbox", "names": ["訊息"]}, "text": "/case help"},
                     {"do": "press", "key": "Enter"}]},
        {"id": "s2", "account": "demo_b", "where": "私訊", "kind": "按鈕",
         "actions": [{"do": "open", "url": "/t/messages/@bot"},
                     {"do": "click", "target": {"role": "button", "names": ["核准"]}}]},
    ],
}

USABLE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import demo_runbook
rb = demo_runbook.load_rb(sys.argv[2])
aid, rec, note = demo_runbook.usable_approval(rb, sys.argv[3])
print(json.dumps({"aid": aid, "note": note}, ensure_ascii=False))
"""


def main():
    home = tempfile.mkdtemp(prefix="ne-standing-")
    port = free_port()
    json.dump({"outbox": {"port": port}}, open(os.path.join(home, "policy.json"), "w"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    base = "http://127.0.0.1:%d/" % port
    key = [None]

    def run(script, *a):
        p = subprocess.run([sys.executable, script] + list(a), capture_output=True, text=True, encoding="utf-8",
                           env=env, timeout=120)
        try:
            return p.returncode, json.loads(p.stdout)
        except Exception:
            return p.returncode, {"raw": p.stdout, "err": p.stderr[-400:]}

    def draft(doc, tag):
        p = os.path.join(home, "sends-%s.json" % tag)
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        return run(OUTBOX, "draft", "--service", "runbook", "--to", doc["target"], "--body-file", p, "--why", "test")

    def state():
        return json.loads(http(base + "api/state")[1])

    def pending(aid):
        return [d for d in state()["pending"] if d["id"] == aid]

    def page_approve(aid, **extra):
        p = pending(aid)[0]
        if key[0] is None:
            key[0] = json.loads(re.search(r"const KEY=(\"[^\"]+\")", http(base)[1]).group(1))
        body = {"id": aid, "action": "approve", "key": key[0], "rev": p["rev"]}
        body.update(extra)
        return http(base + "api/decide", body)

    def approval(aid):
        return json.load(open(os.path.join(home, "outbox", "approved", aid + ".json"), encoding="utf-8"))["approval"]

    try:
        # ---------- 1. policy
        run(POL, "domain", "add", "localhost:8065", "demo-mm", "--demo-accounts", "demo_a,demo_b,demo_c", "--reason", "t")
        run(POL, "domain", "add", "localhost:8067", "plain-site", "--reason", "t")                  # 不是示範用途
        run(POL, "domain", "add", "localhost:8068", "mattermost", "--demo-accounts", "demo_a", "--reason", "t")
        rc, o = run(POL, "domain", "set", "localhost:8065", "runbook_approval", "standing")
        check("policy：設長期核准沒附 --reason → 拒", rc == 3, o)
        rc, o = run(POL, "domain", "set", "localhost:8067", "runbook_approval", "standing", "--reason", "t")
        check("policy：不是示範用途的主機不能設長期核准", rc == 3, o)
        rc, o = run(POL, "domain", "set", "localhost:8068", "runbook_approval", "standing", "--reason", "t")
        check("policy：內建敏感 app（mattermost）不能設長期核准", rc == 3 and "敏感" in o.get("error", ""), o)
        rc, o = run(POL, "domain", "set", "localhost:9999", "runbook_approval", "standing", "--reason", "t")
        check("policy：沒登記的主機 → 拒", rc != 0, o)
        rc, o = run(POL, "set", "outbox.port", "80", "--reason", "t")
        check("policy：outbox.port 只接受 1024–65535", rc == 2, o)
        rc, o = run(POL, "set", "demo.learn", "auto", "--reason", "t")
        check("policy：demo.learn=auto", rc == 0, o)
        led = [json.loads(l) for l in open(os.path.join(home, "policy-ledger.jsonl"), encoding="utf-8")]
        check("policy：每次修改寫 ledger", led[-1]["detail"] == {"demo.learn": "auto"}, led[-1])

        # ---------- 2. page 模式（預設）
        rc, o = draft(sends_doc("localhost:8065", ["demo_a", "demo_b"], "p1"), "p1")
        check("page 模式：劇本清單進待核准", rc == 0 and o.get("state") is None and pending(o["id"]), o)
        p1 = o["id"]
        time.sleep(1.5)
        check("page 模式：卡片寫明為什麼要使用者看（沒有設定長期核准）",
              "沒有設定長期核准" in (pending(p1)[0].get("standing_skip") or ""), pending(p1))

        # ---------- 3. standing、沒有先前的頁面核准
        rc, o = run(POL, "domain", "set", "localhost:8065", "runbook_approval", "standing", "--reason", "使用者：劇本我一律核准")
        check("policy：示範主機設成長期核准", rc == 0, o)
        rc, o = run(POL, "domain", "add", "localhost:8065", "demo-mm", "--demo-accounts", "demo_a,demo_b,demo_c",
                    "--reason", "重新登記")
        ent = [e for e in json.load(open(os.path.join(home, "policy.json"), encoding="utf-8"))["sensitive_domains"]
               if e["host"] == "localhost:8065"][0]
        check("policy：重新登記同一主機不會默默改掉核准方式", ent.get("runbook_approval") == "standing", ent)
        time.sleep(1.5)
        check("standing＋沒有先前的頁面核准 → 仍在待核准、卡片寫明第一次要看",
              pending(p1) and "還沒在這一頁親自核准" in (pending(p1)[0].get("standing_skip") or ""), pending(p1))

        # ---------- 4. 使用者在頁面核准一次 → 之後自動
        code, t = page_approve(p1, max_runs=5)
        check("使用者在頁面核准第一份（5 次）", code == 200, t)
        check("頁面核准記 via=page", approval(p1).get("via") == "page", approval(p1))
        rc, o = draft(sends_doc("localhost:8065", ["demo_a", "demo_b"], "a2"), "a2")
        check("standing＋有頁面核准＋同主機同帳號 → draft 直接回 approved（伺服器自動）",
              rc == 0 and o.get("state") == "approved" and o.get("via") == "standing" and o.get("basis") == p1, o)
        a2 = o.get("id")
        ap = approval(a2)
        check("自動核准的紀錄連到那筆頁面核准、次數沿用上次（5）",
              ap.get("via") == "standing" and ap.get("basis") == p1 and ap.get("max_runs") == 5, ap)
        rc, o = run(OUTBOX, "status", a2)
        check("status 回 via=standing 與 basis", o.get("via") == "standing" and o.get("basis") == p1, o)
        log = [json.loads(l) for l in open(os.path.join(home, "outbox", "decide-log.jsonl"), encoding="utf-8")]
        check("decide-log 記一筆 standing（附依據）", any(x.get("id") == a2 and x.get("code") == "standing"
                                                       and x.get("basis") == p1 for x in log), log[-3:])
        rc, o = draft(sends_doc("127.0.0.1:8065", ["demo_a"], "a3"), "a3")
        check("同一台換寫法（127.0.0.1:8065）、帳號是子集 → 自動", o.get("state") == "approved", o)
        check("自動核准的不會成為下一次的依據（依據永遠是頁面核准）", approval(o["id"]).get("basis") == p1, approval(o["id"]))

        # ---------- 5. 帳號超出範圍
        rc, o = draft(sends_doc("localhost:8065", ["demo_a", "demo_c"], "c1"), "c1")
        check("帳號超出先前頁面核准的範圍（demo_c）→ 進頁面", o.get("state") is None and pending(o["id"])
              and "還沒在這一頁親自核准" in (o.get("standing_skip") or ""), o)
        c1 = o["id"]
        rc, o = draft(sends_doc("localhost:8065", ["demo_a", "demo_z"], "z1"), "z1")
        check("帳號不在登記範圍（demo_z）→ 進頁面", o.get("state") is None and "不在登記範圍" in (o.get("standing_skip") or ""), o)

        # ---------- 6. 內建敏感 app：就算政策檔被手改成 standing 也不適用
        pol = json.load(open(os.path.join(home, "policy.json"), encoding="utf-8"))
        for e in pol["sensitive_domains"]:
            if e["host"] == "localhost:8068":
                e["runbook_approval"] = "standing"
        json.dump(pol, open(os.path.join(home, "policy.json"), "w", encoding="utf-8"), ensure_ascii=False)
        rc, o = draft(sends_doc("localhost:8068", ["demo_a"], "m1"), "m1")
        page_approve(o["id"])
        rc, o = draft(sends_doc("localhost:8068", ["demo_a"], "m2"), "m2")
        check("內建敏感 app（mattermost）手改成 standing、也有頁面核准 → 仍進頁面", o.get("state") is None and pending(o["id"]), o)

        # ---------- 7. 撤銷 → 暫停；再核准一次 → 恢復
        code, t = http(base + "api/decide", {"id": a2, "action": "revoke", "key": key[0]})
        check("使用者在歷史撤銷一份自動核准", code == 200, t)
        rc, o = draft(sends_doc("localhost:8065", ["demo_a", "demo_b"], "r1"), "r1")
        check("撤銷之後 → 長期核准暫停、進頁面（寫明原因）", o.get("state") is None and "撤銷或退回" in (o.get("standing_skip") or ""), o)
        page_approve(o["id"])
        rc, o = draft(sends_doc("localhost:8065", ["demo_a", "demo_b"], "r2"), "r2")
        check("在頁面再核准一次 → 恢復自動", o.get("state") == "approved" and o.get("via") == "standing", o)

        # ---------- 8. 頁面
        page = http(base)[1]
        st = state()
        check("api/state：劇本卡片仍在 pending（給歷史頁的劇本區），訊息另計",
              any(d["service"] == "runbook" for d in st["pending"]), [d["id"] for d in st["pending"]])
        check("頁面：徽章只算訊息、劇本在歷史的獨立一區",
              'filter(d=>d.service!=="runbook")' in page and "msgs().length" in page and 'id="history-rb"' in page
              and "示範劇本" in page, "")
        node = shutil.which("node")
        if node:
            js = page.split("<script>")[1].split("</script>")[0]
            jp = os.path.join(home, "page.js"); open(jp, "w", encoding="utf-8").write(js)
            p = subprocess.run([node, "--check", jp], capture_output=True, text=True)
            check("頁面腳本語法正確（node --check）", p.returncode == 0, p.stderr)
        else:
            print("SKIP 頁面腳本語法（沒有 node）")

        # ---------- 9. demo_runbook 續約
        rbdir = os.path.join(home, "demo"); os.makedirs(rbdir)
        rbp = os.path.join(rbdir, "runbook.json")
        json.dump(RUNBOOK, open(rbp, "w", encoding="utf-8"), ensure_ascii=False)
        p = subprocess.run([sys.executable, os.path.join(S, "demo_runbook.py"), "request-approval", rbp],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        o = json.loads(p.stdout)
        check("demo_runbook request-approval：長期核准下直接 approved", o.get("state") == "approved", o)
        rid = o["id"]
        with open(os.path.join(home, "neb-ledger.jsonl"), "a", encoding="utf-8") as f:
            for k in range(approval(rid)["max_runs"]):
                for stp, kk in (("s1", "fill:0"), ("s2", "click:0")):
                    f.write(json.dumps({"runbook": rid, "step": stp, "key": kk, "runbook_ok": True,
                                        "t": time.time() - 10000 + k * 1000}) + "\n")

        def usable(aid):
            q = subprocess.run([sys.executable, "-c", USABLE, S, rbp, aid], capture_output=True, text=True,
                               encoding="utf-8", env=env, timeout=60)
            try:
                return json.loads(q.stdout)
            except Exception:
                return {"raw": q.stdout, "err": q.stderr[-400:]}
        u = usable(rid)
        check("次數用完 → 長期核准下自動續一筆（同一份清單）", u.get("aid") and u["aid"] != rid and "續一筆" in (u.get("note") or ""), u)
        if u.get("aid"):
            na, oa = approval(u["aid"]), approval(rid)
            check("續的那筆送出內容與原核准相同、via=standing", na["sends_sha256"] == oa["sends_sha256"]
                  and na.get("via") == "standing", na)
        run(POL, "domain", "set", "localhost:8065", "runbook_approval", "page", "--reason", "t")
        u = usable(rid)
        check("page 模式：次數用完不自動續（照舊要使用者核准）", u.get("aid") is None and "用完" in (u.get("note") or ""), u)
        before = len(st["pending"])
        rc, o = draft(sends_doc("localhost:8065", ["demo_a"], "pg"), "pg")
        check("改回 page 模式 → 劇本清單照舊進待核准", o.get("state") is None and pending(o["id"]), o)
    finally:
        try:
            for line in open(os.path.join(home, "outbox", "server.log"), encoding="utf-8"):
                if '"pid"' in line:
                    pid = json.loads(line)["pid"]
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
                    else:
                        os.kill(pid, 9)
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(home, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
