#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neb real read 的正面測試：真的開 Edge 新視窗讀本機測試頁、關掉、其他視窗沒動。
會開瀏覽器視窗——只在 Windows CI 跑（要 --i-am-ci，且 GITHUB_ACTIONS=true）。拒絕路徑在 test_neb.py（三平台）。"""
import http.server, json, os, shutil, socketserver, subprocess, sys, tempfile, threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "skills", "no-errands", "scripts")
NEB = os.path.join(SCRIPTS, "neb.py")
R = []
MARK = "這是一頁給 real read 測試的頁面 PINEAPPLE-42"


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:400]))


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/private"):
            self.send_response(302); self.send_header("Location", "/login?next=/private"); self.end_headers(); return
        body = ("<!doctype html><html><head><meta charset=utf-8><title>real read test</title></head><body>"
                "<h1>測試頁</h1><p>%s</p>%s</body></html>" % (MARK, "<p>login form</p>" if self.path.startswith("/login") else ""))
        b = body.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass


def main():
    if os.name != "nt" or "--i-am-ci" not in sys.argv or os.environ.get("GITHUB_ACTIONS") != "true":
        print("SKIP real read 正面測試：只在 Windows CI 跑（要 --i-am-ci，且 GITHUB_ACTIONS=true）")
        print("ALL PASS (0)"); return 0
    sys.path.insert(0, SCRIPTS)
    import real_browser
    if not real_browser.edge_path():
        check("這台 runner 有 Edge", False, "找不到 msedge.exe"); print("FAILED"); return 1
    home = tempfile.mkdtemp(prefix="ne-real-")
    json.dump({}, open(os.path.join(home, "policy.json"), "w"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        before = real_browser.edge_windows()
        p = subprocess.run([sys.executable, NEB, "real", "read", "http://127.0.0.1:%d/page" % port], capture_output=True,
                           text=True, encoding="utf-8", env=env, timeout=180)
        try:
            o = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            o = {"raw": p.stdout[-400:], "err": p.stderr[-400:]}
        check("real read 讀回本機測試頁的文字", o.get("ok") and MARK in (o.get("text") or ""), o)
        check("字數回報＝讀到的文字長度", o.get("chars") == len(o.get("text") or ""), (o.get("chars"), len(o.get("text") or "")))
        check("自己開的 Edge 視窗關掉了", o.get("closed") is True, o)
        check("原本的 Edge 視窗一個都沒少", o.get("other_windows_untouched") is True and
              set(before) <= set(real_browser.edge_windows()), (before, real_browser.edge_windows()))
        check("讀完沒有留下新的 Edge 視窗", set(real_browser.edge_windows()) <= set(before) | set(), (before, real_browser.edge_windows()))
        p = subprocess.run([sys.executable, NEB, "real", "read", "http://127.0.0.1:%d/private" % port], capture_output=True,
                           text=True, encoding="utf-8", env=env, timeout=180)
        o2 = json.loads(p.stdout.strip().splitlines()[-1])
        check("被導去登入頁 → 回報沒登入、不交出內容", p.returncode == 1 and o2.get("logged_in") is False and not o2.get("text")
              and "沒登入" in (o2.get("error") or ""), o2)
        check("沒登入那一次也關掉了自己的視窗", o2.get("closed") is True, o2)
        lg = open(os.path.join(home, "neb-ledger.jsonl"), encoding="utf-8").read()
        check("紀錄只有主機與字數，沒有頁面內容", "PINEAPPLE" not in lg and '"chars"' in lg, lg[-300:])
    finally:
        srv.shutdown()
        shutil.rmtree(home, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
