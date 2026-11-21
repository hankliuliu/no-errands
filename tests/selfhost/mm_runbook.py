#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自架真實 Mattermost（CI）上的迷你示範劇本：虛構資料、真實瀏覽器、真實守門。

  a 登記示範主機（demo 帳號、agent 登入同意都記在政策檔）
  b agent 代為登入示範帳號（neb login：密碼走 stdin）
  c preflight：登入有效且身分正確
  d 沒核准 → 送不出去（neb 拒）
  e 整份劇本核准（測試腳本代替使用者在發件匣頁面核准）
  f rehearse：照劇本在頻道發一則訊息 → 伺服器上真的有這則貼文（API GET 驗證）
  g 守門反例（真實瀏覽器）：改一個字、換頻道 → 拒；伺服器上沒有多出貼文
  h learn 產生提案
環境同 mm_flow.py（MM_URL、MM_ADMIN/MM_ADMIN_PASS、MM_BOB/MM_BOB_PASS、MM_TEAM）。
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
NEB, POL, RB = (os.path.join(SC, n) for n in ("neb.py", "policy.py", "demo_runbook.py"))
UA = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"
MARK = "no-errands runbook CI marker"
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:400]), flush=True)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def api(base, method, path, token=None, body=None, raw=False):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + "/api/v4" + path, method=method, headers=h,
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return r if raw else json.loads(r.read().decode() or "null")


def http(url, data=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def main():
    ab_bin = sys.argv[sys.argv.index("--ab") + 1]
    base = os.environ["MM_URL"].rstrip("/")
    adm, admpw, bob, bobpw, team = (os.environ[k] for k in ("MM_ADMIN", "MM_ADMIN_PASS", "MM_BOB", "MM_BOB_PASS", "MM_TEAM"))
    hostport = base.split("//", 1)[1]
    td = tempfile.mkdtemp(prefix="ne-rbmm-")
    home = os.path.join(td, "home"); os.makedirs(os.path.join(home, "bin"))
    shutil.copy(ab_bin, os.path.join(home, "bin", os.path.basename(ab_bin)))
    port = free_port()
    json.dump({"outbox": {"port": port}}, open(os.path.join(home, "policy.json"), "w"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    rbdir = os.path.join(td, "rb"); prof = os.path.join(rbdir, "profiles", bob); os.makedirs(prof)
    rbp = os.path.join(rbdir, "runbook.json")
    chan = "/%s/channels/town-square" % team
    rb = {
        "title": "CI 迷你劇本", "runs_dir": os.path.join(td, "runs"),
        "target": {"base_url": base, "host": hostport, "home_path": chan, "whoami_path": "/api/v4/users/me"},
        "accounts": {bob: {"profile": "profiles/" + bob}},
        "steps": [{"id": "s1", "account": bob, "where": "Town Square", "kind": "訊息", "say": "照劇本發一則訊息",
                   "actions": [{"do": "open", "url": chan},
                               {"do": "fill", "target": {"role": "textbox",
                                                         "names": ["Write to Town Square", "寫給 Town Square"]},
                                "text": MARK},
                               {"do": "press", "key": "Enter"},
                               {"do": "wait_text", "text": MARK, "budget_s": 30}]}],
    }
    json.dump(rb, open(rbp, "w", encoding="utf-8"), ensure_ascii=False)

    def run(script, *a, stdin=None):
        p = subprocess.run([sys.executable, script] + list(a), capture_output=True, text=True, encoding="utf-8",
                           env=env, timeout=300, input=stdin)
        txt = p.stdout
        last = txt[txt.rfind("\n{") + 1:] if "\n{" in txt else txt[txt.find("{"):] if "{" in txt else "{}"
        try:
            return p.returncode, json.loads(last), txt
        except Exception:
            return p.returncode, {}, txt + p.stderr[-500:]

    def ab(*a):
        return subprocess.run([ab_bin, "--session", "rb-ci", "--profile", prof] + list(a), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=90)

    def neb(*a):
        return run(NEB, "--session", "rb-" + bob, "--profile", prof, *a)

    def marker_posts():
        tok = api(base, "POST", "/users/login", body={"login_id": adm, "password": admpw}, raw=True).headers["Token"]
        t = api(base, "GET", "/teams/name/" + team, tok)
        c = api(base, "GET", "/teams/%s/channels/name/town-square" % t["id"], tok)
        posts = api(base, "GET", "/channels/%s/posts?per_page=200" % c["id"], tok)["posts"].values()
        u = api(base, "GET", "/users/username/" + bob, tok)
        return [p for p in posts if p["user_id"] == u["id"] and MARK in p["message"]]

    try:
        # 一次性：人第一次用這個 profile 時會按掉「要在哪裡開？」中繼頁（登記主機之前，neb 還沒守門）
        ab("open", base + "/login"); ab("wait", "--load", "networkidle")
        snap = ab("snapshot", "-i").stdout
        m = re.search(r'(?:link|button) "View in Browser"[^\n]*?ref=(e\d+)', snap, re.I)
        if m:
            ab("click", "@" + m.group(1))
        ab("close")

        # a 登記示範主機
        rc, o, _ = run(POL, "domain", "add", hostport, "ci-demo", "--demo-accounts", bob, "--agent-login", bob,
                       "--reason", "CI：自架 Mattermost 迷你劇本")
        run(POL, "grant", "ci-demo", "--reason", "CI")
        check("a 登記示範主機（帳號與 agent 登入同意在政策檔）", rc == 0, o)

        # b agent 代為登入
        rc, o, txt = run(RB, "login", rbp, "--account", bob, "--secret-cmd",
                         '"%s" -c "import os;print(os.environ[\'MM_BOB_PASS\'])"' % sys.executable)
        check("b neb login 登入示範帳號", rc == 0 and o.get("ok"), txt[-400:])
        led = open(os.path.join(home, "neb-ledger.jsonl"), encoding="utf-8").read()
        check("b ledger 不含密碼", bobpw not in led)

        # c preflight
        rc, o, txt = run(RB, "preflight", rbp)
        ids = [c for c in o.get("checks", []) if c["check"].startswith("登入有效且身分正確")]
        check("c preflight：登入的就是 %s" % bob, ids and all(c["ok"] for c in ids), txt[-400:])

        # d 沒核准 → 送不出去
        before = len(marker_posts())
        neb("open", base + chan); time.sleep(3); neb("snapshot", "-i")
        snap = neb("snapshot", "-i")[2]
        ref = re.search(r'textbox "(?:Write to Town Square|寫給 Town Square)"[^\n]*?ref=(e\d+)', snap)
        rc, o, txt = neb("fill", "@" + ref.group(1) if ref else "@e1", MARK)
        check("d 沒核准時 fill → 拒", rc == 3, txt[-300:])

        # e 核准
        rc, o, txt = run(RB, "request-approval", rbp)
        aid = o.get("id")
        b = "http://127.0.0.1:%d/" % port
        st = json.loads(http(b + "api/state")[1])
        p = [d for d in st["pending"] if d["id"] == aid][0]
        key = json.loads(re.search(r"const KEY=(\"[^\"]+\")", http(b)[1]).group(1))
        code, t = http(b + "api/decide", {"id": aid, "action": "approve", "key": key, "rev": p["rev"], "max_runs": 3})
        check("e 整份劇本核准（代替使用者）", code == 200, t)

        # f rehearse
        rc, o, txt = run(RB, "rehearse", rbp, "--approval", aid)
        check("f rehearse 成功", rc == 0 and o.get("steps_ok") == 1, txt[-600:])
        time.sleep(2)
        after = len(marker_posts())
        check("f 伺服器上真的多了一則劇本訊息", after == before + 1, (before, after))

        # g 反例（真實瀏覽器）
        neb("open", base + chan); time.sleep(3)
        snap = neb("snapshot", "-i")[2]
        ref = re.search(r'textbox "(?:Write to Town Square|寫給 Town Square)"[^\n]*?ref=(e\d+)', snap)
        r_ = "@" + ref.group(1) if ref else "@e1"
        rc, o, txt = neb("fill", r_, MARK + "!", "--runbook-approval", aid, "--step", "s1")
        check("g 改一個字 → 拒", rc == 3 and "需重新核准" in txt, txt[-300:])
        neb("open", base + "/%s/channels/off-topic" % team); time.sleep(3)
        snap = neb("snapshot", "-i")[2]
        ref = re.search(r'textbox "([^"]*)"[^\n]*?ref=(e\d+)', snap)
        rc, o, txt = neb("fill", "@" + (ref.group(2) if ref else "e1"), MARK, "--runbook-approval", aid, "--step", "s1")
        check("g 換頻道 → 拒", rc == 3, txt[-300:])
        neb("close")
        check("g 伺服器上沒有多出貼文", len(marker_posts()) == after)

        # h learn
        rc, o, txt = run(RB, "learn", rbp)
        check("h learn 產生提案", rc == 0 and o.get("proposal"), txt[-300:])
    except Exception as e:
        check("例外", False, repr(e))
    finally:
        try:
            for line in open(os.path.join(home, "outbox", "server.log"), encoding="utf-8"):
                if '"pid"' in line:
                    os.kill(json.loads(line)["pid"], 15)
        except Exception:
            pass
        subprocess.run([ab_bin, "--session", "rb-" + bob, "close"], capture_output=True)
        shutil.rmtree(td, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (len(R) - sum(R), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
