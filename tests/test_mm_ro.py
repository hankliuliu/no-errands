#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mm_ro 唯讀工具離線測試（純 stdlib，三平台 CI）：本機假 Mattermost 伺服器。

  - 讀得懂 Mattermost 個人存取 token 頁面複製下來的格式（全形冒號、鍵含空白），網址從政策檔來
  - whoami / channels（預設只回數量）/ posts（預設只回則數與時間範圍）
  - 伺服器端只收到 GET；POST、非 /api/v4 路徑、search 都在本機被拒絕（伺服器沒收到）
  - 任何輸出都不含 token
"""
import json, os, shutil, socket, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MR = os.path.join(REPO, "skills", "no-errands", "scripts", "mm_ro.py")
TOKEN = "abcdefghijklmnopqrstuvwxyz"
SEEN = []
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -- " + str(detail)[:300]))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        SEEN.append(("GET", self.path, self.headers.get("Authorization") == "Bearer " + TOKEN))
        p = self.path.split("?")[0]
        if p == "/api/v4/users/me":
            return self._j({"id": "u1", "username": "tester"})
        if p == "/api/v4/users/u1/teams":
            return self._j([{"id": "t1"}])
        if p == "/api/v4/users/u1/teams/t1/channels":
            return self._j([{"id": "c1", "type": "O", "name": "town"}, {"id": "c2", "type": "D", "name": "u1__u2"}])
        if p == "/api/v4/channels/c1/posts":
            return self._j({"order": ["p2", "p1"], "posts": {"p1": {"id": "p1", "create_at": 1700000000000, "message": "hi"},
                                                            "p2": {"id": "p2", "create_at": 1700000600000, "message": "yo"}}})
        self.send_response(404); self.end_headers()

    def do_POST(self):
        SEEN.append(("POST", self.path, True))
        self.send_response(500); self.end_headers()


def main():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    home = tempfile.mkdtemp(prefix="ne-mm-test-")
    tok = os.path.join(home, "mattermost.txt")
    open(tok, "w", encoding="utf-8").write("Token 敘述：測試用\nToken ID：zyxwvutsrqponmlkjihgfedcba\n存取 Token：%s\n" % TOKEN)
    json.dump({"mattermost": {"token_file": tok, "url": "http://127.0.0.1:%d" % port}},
              open(os.path.join(home, "policy.json"), "w", encoding="utf-8"), ensure_ascii=False)
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    outputs = []

    def run(*a):
        p = subprocess.run([sys.executable, MR] + list(a), capture_output=True, text=True, encoding="utf-8", env=env)
        outputs.append(p.stdout + p.stderr)
        try:
            return json.loads(p.stdout), p.returncode
        except Exception:
            return {"raw": p.stdout}, p.returncode

    try:
        o, rc = run("whoami")
        check("whoami（全形冒號 token 檔 + 政策檔網址）", o.get("ok") and o.get("username") == "tester", o)
        check("請求帶正確的 Bearer token", SEEN and all(x[2] for x in SEEN if x[0] == "GET"), SEEN)
        o, rc = run("channels")
        check("channels 預設只回數量", o.get("ok") and o.get("channels") == 2 and "list" not in o, o)
        o, rc = run("posts", "c1", "--n", "5")
        check("posts 預設只回則數與時間範圍", o.get("ok") and o.get("count") == 2 and o.get("range") and "posts" not in o, o)
        n_before = len(SEEN)
        o, rc = run("request", "POST", "/api/v4/posts")
        check("POST 在本機被拒絕", rc == 3 and o.get("refused"), o)
        o, rc = run("request", "DELETE", "/api/v4/posts/p1")
        check("DELETE 在本機被拒絕", rc == 3 and o.get("refused"), o)
        o, rc = run("request", "GET", "/oauth/x")
        check("/api/v4 以外的路徑被拒絕", rc == 3, o)
        o, rc = run("search", "hi")
        check("search（POST API）被拒絕", rc == 3, o)
        check("被拒絕的請求都沒有送到伺服器", len(SEEN) == n_before, SEEN[n_before:])
        check("伺服器從未收到非 GET", all(x[0] == "GET" for x in SEEN), SEEN)
        # 英文介面複製下來的格式（半形冒號、鍵名有空格）
        open(tok, "w", encoding="utf-8").write("Token Description: test\nToken ID: zyxwvutsrqponmlkjihgfedcba\nAccess Token: %s\n" % TOKEN)
        o, rc = run("whoami")
        check("whoami（英文頁面格式 Access Token: …）", o.get("ok") and o.get("username") == "tester", o)
        check("所有輸出都不含 token", not any(TOKEN in t for t in outputs))
    finally:
        srv.shutdown()
        shutil.rmtree(home, ignore_errors=True)
    bad = RESULTS.count(False)
    print("ALL PASS (%d)" % len(RESULTS) if not bad else "FAILED %d/%d" % (bad, len(RESULTS)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
