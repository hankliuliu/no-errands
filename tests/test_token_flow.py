#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Token 申請流程（headless 瀏覽器、無視窗）：登入（測試中由腳本代替使用者）→ 建立 token → 讀一次 →
寫進指定的設定檔（KEY=VALUE）→ 驗證檔案裡的值的雜湊 = 伺服器發出的那一個。token 值全程不印出。

  python tests/test_token_flow.py --ab <agent-browser 路徑>
"""
import hashlib, json, os, shutil, socket, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV = os.path.join(REPO, "scenarios", "token-demo", "server.py")
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


def main():
    ab = sys.argv[sys.argv.index("--ab") + 1]
    td = tempfile.mkdtemp(prefix="ne-token-")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    issued = os.path.join(td, "issued.txt")
    srv = subprocess.Popen([sys.executable, SRV, "--port", str(port), "--issued", issued])
    base = "http://127.0.0.1:%d" % port
    sess = ["--session", "ne-token-test", "--profile", os.path.join(td, "profile").replace("\\", "/")]   # agent-browser 會吃掉反斜線
    outputs, steps = [], []

    def b(*a):
        p = subprocess.run([ab] + sess + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        outputs.append(p.stdout + p.stderr)
        steps.append("%s rc=%d" % (a[0] if a[0] != "fill" else "fill " + a[1], p.returncode))
        return p

    try:
        time.sleep(1.0)
        # 1) 使用者在 agent 專用 profile 登入一次（測試裡由腳本代勞；真實情境是人在有頭視窗登入）
        b("open", base + "/login"); b("fill", "#user", "demo"); b("fill", "#pass", "demo123"); b("click", "#signin")
        b("wait", "#tokens")                      # 登入成功的證據：首頁的連結出現
        # 2) agent 接手：開 token 頁、填描述、建立
        b("open", base + "/tokens"); b("fill", "#desc", "no-errands")
        b("click", "#create")
        if b("wait", "#new-token").returncode != 0:   # 有些平台（macOS headless）第一次點擊沒送出 → 重試一次
            b("fill", "#desc", "no-errands"); b("click", "#create"); b("wait", "#new-token")
        # 3) 讀一次 token，直接寫進設定檔；不印出、不經過對話
        cfg = os.path.join(td, ".env")
        open(cfg, "w").write("OTHER=keep\n")
        p = subprocess.run([ab] + sess + ["get", "text", "#new-token"], capture_output=True, text=True, encoding="utf-8", timeout=60)
        val = p.stdout.strip().splitlines()[-1].strip() if p.stdout.strip() else ""
        lines = [l for l in open(cfg).read().splitlines() if not l.startswith("DEMO_TOKEN=")]
        open(cfg, "w").write("\n".join(lines + ["DEMO_TOKEN=" + val]) + "\n")
        b("close")
        got = [l.split("=", 1)[1] for l in open(cfg).read().splitlines() if l.startswith("DEMO_TOKEN=")]
        issued_hashes = open(issued).read().split() if os.path.exists(issued) else []
        check("伺服器發出 1 個 token", len(issued_hashes) == 1, issued_hashes)
        check("設定檔裡的值 = 伺服器發出的 token（比對雜湊）", got and hashlib.sha256(got[0].encode()).hexdigest() in issued_hashes)
        check("設定檔原有內容保留", "OTHER=keep" in open(cfg).read())
        check("過程輸出不含 token 值", got and not any(got[0] in o for o in outputs))
        print("steps:", "; ".join(steps))
    finally:
        subprocess.run([ab, "--session", "ne-token-test", "close"], capture_output=True, timeout=30)
        srv.kill()
        time.sleep(0.5)
        shutil.rmtree(td, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
