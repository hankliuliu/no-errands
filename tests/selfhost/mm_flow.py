#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自架真實 Mattermost（官方 preview 映像，CI Linux runner）端到端：

  a 管理員在 System Console 打開「個人存取權杖」（瀏覽器）
  b 使用者在 Profile → Security 建立 PAT（瀏覽器），token 寫進設定檔（值不輸出）
  c 唯讀工具 mm_ro：whoami / channels / posts
  d 發件匣真實送出：起草 → 測試腳本代替使用者核准 → 貼文真的出現在伺服器（GET 驗證）；退回的草稿不出現
  e GET 讀貼文不動已讀（另一個使用者發文造成未讀 → 讀 → 成員紀錄不變）
環境：MM_URL、MM_ADMIN/MM_ADMIN_PASS、MM_BOB/MM_BOB_PASS、MM_TEAM（CI 步驟用 mmctl 建好）。
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
sys.path.insert(0, HERE)
from abhelp import AB  # noqa: E402

R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


def api(base, method, path, token=None, body=None, raw=False):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + "/api/v4" + path, method=method, headers=h,
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return r if raw else json.loads(r.read().decode() or "null")


def login_token(base, user, pw):
    r = api(base, "POST", "/users/login", body={"login_id": user, "password": pw}, raw=True)
    return r.headers.get("Token")


def main():
    ab_bin = sys.argv[sys.argv.index("--ab") + 1]
    base = os.environ["MM_URL"].rstrip("/")
    adm, admpw, bob, bobpw, team = (os.environ[k] for k in ("MM_ADMIN", "MM_ADMIN_PASS", "MM_BOB", "MM_BOB_PASS", "MM_TEAM"))
    td = tempfile.mkdtemp(prefix="ne-mm-")
    home = os.path.join(td, "home"); os.makedirs(home)
    try:
        a = AB(ab_bin, "ne-mm", os.path.join(td, "profile"))
        a.run("open", "about:blank")
        a.run("open", base + "/login")
        a.run("wait", "--load", "networkidle")
        if a.find("button|link", r"view in browser", tries=3):   # 「要在哪裡開？」的中繼頁
            a.click("button|link", r"view in browser")
        a.fill("textbox", r"email|user", adm)
        a.fill("textbox", r"password", admpw)
        a.click("button", r"log in|sign in")
        # a. System Console → Integration Management → Enable Personal Access Tokens = true → Save
        a.run("open", base + "/admin_console/integrations/integration_management")
        a.run("wait", "--load", "networkidle")
        tgt = None
        for it in a.snapshot():
            if it["role"] == "radio" and re.search(r"true", it["name"], re.I):
                tgt = it   # 第一組 true 不一定是 PAT；改用文字定位
        # 管理頁面是 SPA：剛載入時 Save 列還沒畫出來（CI 偶發「找不到按鈕 ^save$」）→ 等、重試，必要時重開頁面重點一次
        saved = False
        for attempt in range(3):
            if attempt:
                a.run("open", base + "/admin_console/integrations/integration_management")
                a.run("wait", "--load", "networkidle")
                time.sleep(2)
            snap_txt = a.run("snapshot").stdout
            m = re.search(r'group "Enable Personal Access Tokens:?".*?radio "True"[^\n]*?\bref=(e\d+)', snap_txt, re.S | re.I)
            if m:
                a.run("click", "@" + m.group(1))
            else:   # 診斷：印出「Enable Personal Access Tokens」這個設定本身的結構（測試伺服器）
                i = snap_txt.lower().find("enable personal access tokens")
                print("admin-console-PAT-setting:", snap_txt[max(0, i - 200): i + 900] if i >= 0 else "(title not found) " + snap_txt[:600])
            if a.find("button", r"^save$", tries=15):
                a.click("button", r"^save$")
                saved = True
                break
            print("save button not found (attempt %d); retrying" % (attempt + 1))
        if not saved:
            print("admin console: Save button never appeared after 3 attempts")
        admin_tok = login_token(base, adm, admpw)
        cfg = api(base, "GET", "/config", admin_tok)
        check("a 管理介面打開 PAT（伺服器設定為 true）", cfg["ServiceSettings"].get("EnableUserAccessTokens") is True,
              cfg["ServiceSettings"].get("EnableUserAccessTokens"))

        # b. Profile → Security → Personal Access Tokens → Create
        a.run("open", base + "/%s/channels/town-square" % team)
        a.run("wait", "--load", "networkidle")
        steps_ok = True
        for role, name in (("button", r"user avatar|account menu|current status|set a status|@" + adm),
                           ("menuitem|button|link", r"^profile$"),
                           ("tab|button|link", r"^security$")):
            if not a.find(role, name, tries=4):
                steps_ok = False; break
            a.click(role, name)
        tok = None
        if steps_ok:
            try:
                # Security 頁的 PAT 是收合的設定列：先按那一列的 Edit，才出現 Create Token
                if not a.find("button", r"create token|create new token", tries=1):
                    s_txt = a.run("snapshot").stdout
                    me_ = re.search(r'Personal Access Tokens.*?button "(?:Edit|[^"]*Personal Access Tokens[^"]*)"[^\n]*?\bref=(e\d+)', s_txt, re.S | re.I)
                    if me_:
                        a.run("click", "@" + me_.group(1))
                    else:
                        i = s_txt.lower().find("personal access tokens")
                        print("security-tab:", s_txt[max(0, i - 200): i + 800] if i >= 0 else "(no PAT section) " + s_txt[:800])
                a.click("button", r"create token|create new token")
                a.fill("textbox", r"description|token description", "no-errands")
                a.click("button", r"^save$")
                time.sleep(1)
                if a.find("button", r"yes,? create", tries=2):   # 管理員帳號建 token 會再確認一次
                    a.click("button", r"yes,? create")
                time.sleep(1.5)
                page = a.text()
                mm = re.search(r"Access Token:\s*([a-z0-9]{26})", page)
                tok = mm.group(1) if mm else None
            except RuntimeError as e:
                print("ui:", e)
        check("b 網頁介面建立 PAT 並讀到一次性 token", bool(tok), "ui steps ok=%s" % steps_ok)
        a.close()
        if not tok:   # UI 路線失敗時，以 API 建 PAT 讓後面各步仍可驗（並如實記錄 b 失敗）
            me = api(base, "GET", "/users/me", admin_tok)
            tok = api(base, "POST", "/users/%s/tokens" % me["id"], admin_tok, {"description": "fallback"})["token"]
        tf = os.path.join(home, "mattermost.txt")
        open(tf, "w", encoding="utf-8").write("Token 敘述：no-errands\n存取 Token：%s\n" % tok)   # 實際 token 頁面的格式
        json.dump({"mattermost": {"token_file": tf, "url": base}, "granted_apps": ["mattermost"],
                   "outbox": {"port": free_port()}}, open(os.path.join(home, "policy.json"), "w", encoding="utf-8"), ensure_ascii=False)
        env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")

        def tool(script, *args):
            p = subprocess.run([sys.executable, os.path.join(SC, script)] + list(args), capture_output=True, text=True,
                               encoding="utf-8", env=env, timeout=120)
            check("（%s 輸出不含 token）" % script, tok not in p.stdout + p.stderr)
            try:
                return json.loads(p.stdout)
            except Exception:
                return {"raw": p.stdout[-200:]}

        # c. 唯讀工具
        o = tool("mm_ro.py", "whoami")
        check("c mm_ro whoami", o.get("username") == adm, o)
        o = tool("mm_ro.py", "channels")
        check("c mm_ro channels 回數量", o.get("ok") and o.get("channels", 0) >= 1, o)
        chans = api(base, "GET", "/users/me/teams/%s/channels" % api(base, "GET", "/teams/name/" + team, admin_tok)["id"], admin_tok)
        ts = [c for c in chans if c["name"] == "town-square"][0]
        o = tool("mm_ro.py", "posts", ts["id"], "--n", "5")
        check("c mm_ro posts 回則數", o.get("ok") and "count" in o, o)
        o = tool("mm_ro.py", "request", "POST", "/api/v4/posts")
        check("c mm_ro 拒絕 POST", o.get("refused") is True, o)

        # d. 發件匣真實送出
        body = os.path.join(td, "b.txt")
        open(body, "w", encoding="utf-8").write("no-errands outbox e2e %d" % int(time.time()))
        d1 = tool("outbox.py", "draft", "--service", "mattermost", "--to", ts["id"], "--body-file", body, "--why", "e2e")
        open(body + "2", "w", encoding="utf-8").write("this draft must NOT be sent")
        d2 = tool("outbox.py", "draft", "--service", "mattermost", "--to", ts["id"], "--body-file", body + "2", "--why", "e2e reject")
        url = d1.get("url")
        page = urllib.request.urlopen(url, timeout=10).read().decode()
        key = json.loads(re.search(r'const KEY=("[^"]+")', page).group(1))
        st = json.loads(urllib.request.urlopen(url + "api/state", timeout=10).read().decode())
        p1 = [x for x in st["pending"] if x["id"] == d1["id"]][0]
        p2 = [x for x in st["pending"] if x["id"] == d2["id"]][0]
        check("d 頁面上的對象標籤由伺服器查出（頻道名）", p1.get("resolved") and "Town Square" in (p1.get("label") or ""), p1.get("label"))

        def decide(pid, action, rev, text=None):
            req = urllib.request.Request(url + "api/decide", method="POST", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"id": pid, "action": action, "rev": rev, "key": key,
                                                          "text": text or "", "reason": "e2e"}).encode())
            return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        r1 = decide(d1["id"], "approve", p1["rev"], open(body, encoding="utf-8").read())
        r2 = decide(d2["id"], "reject", p2["rev"])
        posts = api(base, "GET", "/channels/%s/posts?per_page=20" % ts["id"], admin_tok)
        msgs = [posts["posts"][i]["message"] for i in posts["order"]]
        check("d 核准後貼文真的出現在伺服器", r1.get("ok") and open(body, encoding="utf-8").read() in msgs, (r1, msgs[:3]))
        check("d 退回的草稿沒有出現", r2.get("ok") and "this draft must NOT be sent" not in msgs)

        # e. GET 不動已讀：bob 發文造成 admin 的未讀 → mm_ro 讀 → admin 的成員紀錄不變
        bob_tok = login_token(base, bob, bobpw)
        api(base, "POST", "/posts", bob_tok, {"channel_id": ts["id"], "message": "unread maker %d" % time.time()})
        time.sleep(1)
        m0 = api(base, "GET", "/channels/%s/members/me" % ts["id"], admin_tok)
        tool("mm_ro.py", "posts", ts["id"], "--n", "20")
        time.sleep(1)
        m1 = api(base, "GET", "/channels/%s/members/me" % ts["id"], admin_tok)
        chan = api(base, "GET", "/channels/" + ts["id"], admin_tok)
        check("e 有未讀（讀之前）", (chan.get("total_msg_count") or 0) > (m0.get("msg_count") or 0), (chan.get("total_msg_count"), m0.get("msg_count")))
        check("e 讀貼文後 last_viewed_at 與 msg_count 不變", m0.get("last_viewed_at") == m1.get("last_viewed_at")
              and m0.get("msg_count") == m1.get("msg_count"), (m0.get("last_viewed_at"), m1.get("last_viewed_at")))
        # f. gather（聊天收集）：貼文 → 同步 → 編輯與刪除 → 再同步（只發 GET；編輯與刪除由測試腳本用管理員 API 代為製造）
        p_a = api(base, "POST", "/posts", bob_tok, {"channel_id": ts["id"], "message": "gather probe A %d" % time.time()})
        p_b = api(base, "POST", "/posts", bob_tok, {"channel_id": ts["id"], "message": "gather probe B %d" % time.time()})
        o = tool("gather.py", "add", "--service", "mattermost", "--chat", ts["id"], "--since", time.strftime("%Y-%m-%d"))
        gid = o.get("target")
        o = tool("gather.py", "sync", "--target", gid or "")
        r0 = (o.get("results") or [{}])[0]
        check("f gather 第一次同步收到今天的貼文", r0.get("ok") and r0.get("total", 0) >= 2, o)
        api(base, "PUT", "/posts/%s/patch" % p_a["id"], bob_tok, {"message": "gather probe A edited"})
        api(base, "DELETE", "/posts/%s" % p_b["id"], bob_tok)
        time.sleep(1.5)
        o = tool("gather.py", "sync", "--target", gid or "")
        r1 = (o.get("results") or [{}])[0]
        rows = {m["id"]: m for m in (tool("gather.py", "read", "--target", gid or "").get("messages") or [])}
        check("f 再同步：編輯過的貼文更新成新內容", rows.get(p_a["id"], {}).get("text") == "gather probe A edited", r1)
        check("f 再同步：刪除的貼文標成已刪除（不默默消失）", rows.get(p_b["id"], {}).get("deleted") is True, r1)

        # 收掉發件匣伺服器
        try:
            for line in open(os.path.join(home, "outbox", "server.log"), encoding="utf-8"):
                if '"pid"' in line:
                    os.kill(json.loads(line)["pid"], 15)
        except Exception:
            pass
        print("steps:", " ".join(a.log))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    sys.exit(0 if all(R) else 1)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


if __name__ == "__main__":
    main()
