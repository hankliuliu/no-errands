#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任何場景「第一次用講的、之後點了就走」（wishes 第 6 項）的四個改動（三平台；不開視窗、不連網）：
  (1) 上場前連線檢查：ping_url／ping_path → 有通道用 Mattermost ping → 都沒有檢查首頁；2xx／3xx 都算通
  (2) 劇本沒有還原指令＝不需要還原（do_reset 回 None；結束標記不顯示還原結果）
  (3) 還原同意可以存很多組（policy.py set／unset demo.reset_consent <sha256>），舊欄位照樣算數、set 不改寫它
  (4) demo_runbook.py launcher 建「上場前準備.lnk」（只在 Windows；其他平台回清楚的錯誤）
"""
import hashlib, http.server, json, os, shutil, socketserver, subprocess, sys, tempfile, threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]), flush=True)


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redir":
            self.send_response(302); self.send_header("Location", "/"); self.end_headers(); return
        if self.path == "/missing":
            self.send_response(404); self.end_headers(); return
        b = b"ok"
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass


def main():
    home = tempfile.mkdtemp(prefix="ne-any-")
    os.environ["NO_ERRANDS_HOME"] = home
    sys.path.insert(0, SC)
    import demo_runbook as D
    D.policy.HOME = home
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    try:
        # (1)
        mm = {"target": {"base_url": base}, "tunnel": "ssh -N x"}
        check("(1) 有通道、沒設定：照舊用 Mattermost 的 ping", D.ping_url_of(mm) == base + "/api/v4/system/ping", D.ping_url_of(mm))
        site = {"target": {"base_url": base + "/", "home_path": "/home"}}
        check("(1) 沒通道、沒設定：檢查首頁", D.ping_url_of(site) == base + "/home", D.ping_url_of(site))
        check("(1) ping_path 比通道優先", D.ping_url_of({"target": {"base_url": base, "ping_path": "/p"}, "tunnel": "x"}) == base + "/p")
        check("(1) ping_url 最優先", D.ping_url_of({"target": {"base_url": base, "ping_url": "http://x/y", "ping_path": "/p"}}) == "http://x/y")
        check("(1) 200 算通", D.http_ok(base + "/"))
        check("(1) 轉址（3xx）算通", D.http_ok(base + "/redir"))
        check("(1) 404 不算通", not D.http_ok(base + "/missing"))
        check("(1) 連不上不算通", not D.http_ok("http://127.0.0.1:9/", timeout=1))
        # (2)
        ok_, why = D.do_reset({"target": {}})
        check("(2) 沒有還原指令＝不適用（None），不是失敗", ok_ is None and "不適用" in why, (ok_, why))
        rb = {"target": {}, "reset": {"commands": ["%s -c \"print(1)\"" % sys.executable.replace("\\", "/")]}}
        check("(2) 有還原指令、沒有同意＝失敗", D.do_reset(rb)[0] is False)
        # (3)
        sha = D.reset_sha(rb)
        other = hashlib.sha256(b"another scenario").hexdigest()
        env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")

        def pol(*x):
            return subprocess.run([sys.executable, os.path.join(SC, "policy.py")] + list(x), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env)
        check("(3) 理由只有空白：拒絕", pol("set", "demo.reset_consent", sha, "--reason", "   ").returncode != 0)
        check("(3) 不是 sha256：拒絕", pol("set", "demo.reset_consent", "abc", "--reason", "x").returncode != 0)
        check("(3) set：加進同意表", pol("set", "demo.reset_consent", sha, "--reason", "測試同意").returncode == 0)
        pol("set", "demo.reset_consent", other, "--reason", "另一個場景")
        pj = json.load(open(os.path.join(home, "policy.json"), encoding="utf-8"))
        check("(3) 兩個場景的同意並存", {sha, other} <= set((pj.get("demo") or {}).get("reset_consents") or {}), pj.get("demo"))
        check("(3) 引擎認得新的同意表", D.reset_consented(rb))
        check("(3) 有同意就真的執行還原指令", D.do_reset(rb)[0] is True, D.do_reset(rb))
        check("(3) unset：撤回那一組", pol("unset", "demo.reset_consent", sha, "--reason", "撤回").returncode == 0
              and not D.reset_consented(rb))
        check("(3) unset 沒有的那一組：回錯誤", pol("unset", "demo.reset_consent", sha, "--reason", "再撤").returncode != 0)
        pj = json.load(open(os.path.join(home, "policy.json"), encoding="utf-8"))
        legacy = {"commands_sha256": sha, "reason": "舊的", "ts": "2026-01-01T00:00:00"}
        pj["demo"]["reset_consent"] = legacy
        json.dump(pj, open(os.path.join(home, "policy.json"), "w", encoding="utf-8"), ensure_ascii=False)
        check("(3) 舊的單一欄位照樣算數", D.reset_consented(rb))
        pol("set", "demo.reset_consent", hashlib.sha256(b"third").hexdigest(), "--reason", "再加一組")
        pj = json.load(open(os.path.join(home, "policy.json"), encoding="utf-8"))
        check("(3) set 別組時舊欄位一個字都不動", pj["demo"].get("reset_consent") == legacy, pj["demo"].get("reset_consent"))
        check("(3) unset 舊欄位那一組：舊欄位清掉", pol("unset", "demo.reset_consent", sha, "--reason", "撤回舊的").returncode == 0
              and not D.reset_consented(rb))
        led = open(os.path.join(home, "policy-ledger.jsonl"), encoding="utf-8").read()
        check("(3) 紀錄只有 sha 前 16 碼", sha[:16] in led and sha not in led)
        # (4)
        rbd = os.path.join(home, "rb")
        os.makedirs(rbd)
        rbp = os.path.join(rbd, "runbook.json")
        json.dump({"title": "測試場景", "target": {"base_url": base}, "accounts": {}, "steps": []}, open(rbp, "w", encoding="utf-8"))
        r4 = D.make_launcher(D.load_rb(rbp))
        if os.name != "nt":
            check("(4) 非 Windows：回清楚的錯誤、不建檔", r4.get("ok") is False and "Windows" in (r4.get("error") or "")
                  and not os.path.exists(r4["path"]), r4)
        else:
            check("(4) 建出 上場前準備.lnk", r4.get("created") and os.path.exists(r4["path"]), r4)
            check("(4) 已經有就不動", D.make_launcher(D.load_rb(rbp)).get("exists") is True)
            _ascii_copy = os.path.join(rbd, "readback.lnk")   # 英文系統上捷徑元件開不了中文檔名：複製一份英文名稱的來讀
            shutil.copyfile(r4["path"], _ascii_copy)
            q = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "$l=(New-Object -ComObject WScript.Shell).CreateShortcut($env:L); $l.TargetPath; $l.Arguments; $l.WorkingDirectory"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ, L=_ascii_copy))
            ln = q.stdout.strip().splitlines()
            check("(4) 雙擊＝講者模式的 standby，工作目錄是劇本資料夾",
                  len(ln) == 3 and "standby" in ln[1] and "--presenter" in ln[1] and "runbook.json" in ln[1]
                  and os.path.normcase(ln[2]) == os.path.normcase(rbd), ln)
        p = subprocess.run([sys.executable, os.path.join(SC, "demo_runbook.py"), "launcher", rbp], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        o = json.loads(p.stdout) if p.stdout.strip() else {}
        check("(4) 子指令 launcher 能用", (o.get("exists") is True) if os.name == "nt" else (o.get("ok") is False), (p.returncode, p.stdout[-200:], p.stderr[-200:]))
    finally:
        srv.shutdown()
        shutil.rmtree(home, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
