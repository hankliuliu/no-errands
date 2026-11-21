#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""發件匣無 GUI 驗收（純 stdlib；三平台 CI 都跑）。

用暫存 home 與隨機埠：
  1. draft → 伺服器自動起來 → status=pending；頁面顯示伺服器解析的對象標籤
  2. 不帶/錯誤密鑰、錯誤 Host、跨站 Origin → 403；自動化瀏覽器 UA → 403
  3. 模擬使用者（從頁面取密鑰、帶回看到的修訂雜湊）核准 → fake feed 出現原文、status=sent
  4. 修改後送出 → feed 是修改版、edited=true
  5. 退回 → feed 沒有新增、status=rejected 附原因
  6. 頁面載入後草稿檔被竄改 to → 核准被拒（stale），feed 不變
  7. 對象無法解析（不在假聊天聯絡人裡）→ 頁面標示無法確認、核准被拒
  8. 埠被別的程式占用 → draft 回 ok=false 與 recovery，草稿保留
  9. CLI 沒有 send 子指令
用法：python tests/test_outbox.py   （成功印 ALL PASS，失敗 exit 1）
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTBOX = os.path.join(REPO, "skills", "no-errands", "scripts", "outbox.py")
RESULTS = []
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/157.0 Safari/537.36"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (("  -- " + detail) if detail and not cond else ""))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def make_home(port, contacts=None):
    home = tempfile.mkdtemp(prefix="ne-outbox-test-")
    with open(os.path.join(home, "policy.json"), "w", encoding="utf-8") as f:
        json.dump({"outbox": {"port": port}}, f)
    if contacts is not None:
        os.makedirs(os.path.join(home, "outbox"), exist_ok=True)
        with open(os.path.join(home, "outbox", "fake-chat-contacts.json"), "w", encoding="utf-8") as f:
            json.dump(contacts, f, ensure_ascii=False)
    return home


def cli(env, *args):
    p = subprocess.run([sys.executable, OUTBOX] + list(args), capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=30)
    try:
        return json.loads(p.stdout), p.returncode
    except Exception:
        return {"raw": p.stdout, "err": p.stderr}, p.returncode


def http(url, data=None, headers=None):
    h = {"Content-Type": "application/json", "User-Agent": BROWSER_UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers=h, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def feed_lines(home):
    p = os.path.join(home, "outbox", "fake-chat-feed.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def stop_server(home):
    try:
        pid = None
        for line in open(os.path.join(home, "outbox", "server.log"), encoding="utf-8"):
            if '"pid"' in line:
                pid = json.loads(line)["pid"]
        if pid:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            else:
                os.kill(pid, 15)
    except FileNotFoundError:
        pass
    except Exception as e:
        print("cleanup warning:", e)


def main():
    port = free_port()
    home = make_home(port, contacts=["測試對象A", "測試對象B"])
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    base = "http://127.0.0.1:%d/" % port
    body = os.path.join(home, "body.txt")
    busy_home, busy_sock = None, None
    try:
        def draft(text, to="測試對象A"):
            open(body, "w", encoding="utf-8").write(text)
            return cli(env, "draft", "--service", "fake", "--to", to, "--body-file", body,
                       "--why", "測試：使用者請我回覆")

        def state():
            return json.loads(http(base + "api/state")[1])

        def pending(did):
            return next(d for d in state()["pending"] if d["id"] == did)

        d1, rc = draft("我大概會晚 10 分鐘到。")
        check("1 draft 成功並啟動伺服器", rc == 0 and d1.get("ok") and d1.get("server") == "running", str(d1))
        s, _ = cli(env, "status", d1["id"])
        check("1 status=pending", s.get("state") == "pending", str(s))
        p1 = pending(d1["id"])
        check("1 對象標籤由伺服器解析", p1.get("resolved") is True and p1.get("label") == "假聊天視窗（測試）：測試對象A", str(p1))

        code, txt = http(base + "api/decide", {"id": d1["id"], "action": "approve", "text": "x", "rev": p1["rev"]})
        check("2 不帶密鑰核准 → 403", code == 403, "%s %s" % (code, txt))
        code, _ = http(base + "api/decide", {"id": d1["id"], "action": "approve", "text": "x", "key": "guess", "rev": p1["rev"]})
        check("2 錯誤密鑰 → 403", code == 403)
        code, _ = http(base + "api/state", headers={"Host": "evil.example:%d" % port})
        check("2 錯誤 Host → 403", code == 403)
        code, _ = http(base + "api/decide", {"id": d1["id"], "action": "approve", "text": "x"},
                       headers={"Origin": "http://evil.example"})
        check("2 跨站 Origin → 403", code == 403)

        # 模擬使用者：從頁面取密鑰（只有打開頁面的人拿得到）
        code, page = http(base)
        key = json.loads(re.search(r"const KEY=(\"[^\"]+\")", page).group(1))
        check("頁面標題含 NE-OUTBOX（ned 禁區標記）", "NE-OUTBOX" in page)
        check("頁面有 webdriver 軟性防呆", "navigator.webdriver" in page)

        code, _ = http(base + "api/decide", {"id": d1["id"], "action": "approve", "text": "x", "key": key,
                                             "rev": p1["rev"]},
                       headers={"User-Agent": "Mozilla/5.0 HeadlessChrome/157.0"})
        check("2 自動化瀏覽器 UA（含密鑰）→ 403", code == 403)
        check("2 以上都沒有送出", feed_lines(home) == [])

        code, txt = http(base + "api/decide", {"id": d1["id"], "action": "approve",
                                               "text": "我大概會晚 10 分鐘到。", "key": key, "rev": p1["rev"]})
        fl = feed_lines(home)
        check("3 核准 → 假聊天收到原文", code == 200 and len(fl) == 1 and fl[0]["text"] == "我大概會晚 10 分鐘到。"
              and fl[0]["via"] == "ne-outbox" and fl[0]["to"] == "測試對象A", "%s %s %s" % (code, txt, fl))
        s, _ = cli(env, "status", d1["id"])
        check("3 status=sent, edited=false", s.get("state") == "sent" and s.get("edited") is False, str(s))
        sent = next(d for d in state()["sent"] if d["id"] == d1["id"])
        check("3 紀錄含 decided_via 與標籤", sent.get("decided_via") == BROWSER_UA and sent.get("label"), str(sent))
        code, _ = http(base + "api/decide", {"id": d1["id"], "action": "approve", "text": "again", "key": key, "rev": p1["rev"]})
        check("3 重複核准 → 409，不重送", code == 409 and len(feed_lines(home)) == 1)

        d2, _ = draft("原稿：明天見")
        p2 = pending(d2["id"])
        http(base + "api/decide", {"id": d2["id"], "action": "edit", "text": "改稿：明天下午三點見", "key": key, "rev": p2["rev"]})
        fl = feed_lines(home)
        check("4 修改後送出 → 送出的是修改版", len(fl) == 2 and fl[1]["text"] == "改稿：明天下午三點見", str(fl))
        s, _ = cli(env, "status", d2["id"])
        check("4 status=sent, edited=true", s.get("state") == "sent" and s.get("edited") is True, str(s))

        d3, _ = draft("這則不該送出")
        p3 = pending(d3["id"])
        http(base + "api/decide", {"id": d3["id"], "action": "reject", "reason": "語氣太隨便", "key": key, "rev": p3["rev"]})
        s, _ = cli(env, "status", d3["id"])
        check("5 退回 → 沒有送出", len(feed_lines(home)) == 2)
        check("5 status=rejected 附原因", s.get("state") == "rejected" and s.get("reason") == "語氣太隨便", str(s))

        # 6. 頁面載入後竄改 to
        d4, _ = draft("給 A 的訊息")
        p4 = pending(d4["id"])                       # 使用者看到的版本
        fp = os.path.join(home, "outbox", "pending", d4["id"] + ".json")
        rec = json.load(open(fp, encoding="utf-8")); rec["to"] = "測試對象B"
        json.dump(rec, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        code, txt = http(base + "api/decide", {"id": d4["id"], "action": "approve", "text": "給 A 的訊息",
                                               "key": key, "rev": p4["rev"]})
        check("6 草稿被竄改 to → 核准被拒（stale）", code == 409 and json.loads(txt).get("stale") is True, "%s %s" % (code, txt))
        check("6 竄改後沒有送出", len(feed_lines(home)) == 2)
        p4b = pending(d4["id"])
        check("6 重新載入後頁面顯示新對象", p4b["label"].endswith("測試對象B") and p4b["rev"] != p4["rev"], str(p4b))
        http(base + "api/decide", {"id": d4["id"], "action": "reject", "key": key, "rev": p4b["rev"]})

        # 7. 對象無法解析
        d5, _ = draft("給不存在的人", to="不存在的人")
        p5 = pending(d5["id"])
        check("7 無法解析 → resolved=false 附原因", p5.get("resolved") is False and p5.get("resolve_error"), str(p5))
        code, txt = http(base + "api/decide", {"id": d5["id"], "action": "approve", "text": "給不存在的人",
                                               "key": key, "rev": p5["rev"]})
        check("7 無法解析 → 核准被拒、沒有送出", code == 409 and len(feed_lines(home)) == 2, "%s %s" % (code, txt))
        http(base + "api/decide", {"id": d5["id"], "action": "reject", "key": key, "rev": p5["rev"]})

        st = state()
        check("歷史頁資料：sent 2、rejected 3、pending 0",
              len(st["sent"]) == 2 and len(st["rejected"]) == 3 and len(st["pending"]) == 0,
              "%d/%d/%d" % (len(st["sent"]), len(st["rejected"]), len(st["pending"])))

        # 8. 埠被占用
        busy_sock = socket.socket(); busy_sock.bind(("127.0.0.1", 0)); busy_sock.listen(1)
        busy_home = make_home(busy_sock.getsockname()[1], contacts=["測試對象A"])
        benv = dict(env, NO_ERRANDS_HOME=busy_home)
        open(os.path.join(busy_home, "b.txt"), "w", encoding="utf-8").write("hi")
        o, rc = cli(benv, "draft", "--service", "fake", "--to", "測試對象A",
                    "--body-file", os.path.join(busy_home, "b.txt"))
        check("8 埠被占用 → ok=false 附 recovery", rc != 0 and o.get("ok") is False and "outbox.port" in o.get("error", ""), str(o))
        check("8 草稿保留在待核准", o.get("saved") is True and
              os.path.exists(os.path.join(busy_home, "outbox", "pending", o.get("id", "x") + ".json")))

        # 10. 有上限的等待與 stop_polling
        d6, _ = draft("等待測試")
        for _ in range(3):
            s, _ = cli(env, "status", d6["id"])
        check("10 60 秒內前 3 次 status 沒有 stop_polling", not s.get("stop_polling"), str(s))
        s, _ = cli(env, "status", d6["id"])
        check("10 第 4 次 status 附 stop_polling 與結束回合提示", s.get("stop_polling") is True and "結束回合" in s.get("next", ""), str(s))
        t0 = time.time()
        w, _ = cli(env, "wait", d6["id"], "--timeout", "2")
        check("10 wait 逾時回 pending 與結束回合", w.get("state") == "pending" and w.get("waited", 0) >= 2
              and "結束回合" in w.get("next", "") and time.time() - t0 < 15, str(w))
        p6 = pending(d6["id"])
        http(base + "api/decide", {"id": d6["id"], "action": "reject", "reason": "x", "key": key, "rev": p6["rev"]})
        t0 = time.time()
        w, _ = cli(env, "wait", d6["id"], "--timeout", "30")
        check("10 已有結果時 wait 立刻回", w.get("state") == "rejected" and time.time() - t0 < 10, str(w))
        o, rc = cli(env, "send", d3["id"])
        check("9 沒有 send 子指令", rc != 0 and not o.get("ok"), str(o))
    finally:
        stop_server(home)
        if busy_sock:
            busy_sock.close()
        time.sleep(0.5)
        shutil.rmtree(home, ignore_errors=True)
        if busy_home:
            shutil.rmtree(busy_home, ignore_errors=True)
    bad = [r for r in RESULTS if not r[1]]
    print("ALL PASS (%d)" % len(RESULTS) if not bad else "FAILED %d/%d" % (len(bad), len(RESULTS)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
