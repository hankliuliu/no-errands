#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自架真實服務（Gitea）端到端：鑰匙＋登入後內容＋網頁介面建立 token（CI Linux runner）。

前置（CI 步驟負責）：Gitea 跑在 $GITEA_URL，管理員 $GITEA_USER/$GITEA_PASS，私有 repo secret-repo 裡有 secret.txt（內容 $MARKER）。
流程全部用 agent-browser（headless、專用 profile），像 agent 一樣用 snapshot 找元素：
  1 登入一次（測試腳本代替使用者填帳密）  2 新的瀏覽器程序、同一個 profile 仍是登入狀態
  3 讀私有內容（全新 profile 讀不到）     4 網頁介面建立 token → 只讀一次 → 寫進設定檔（值不輸出）
  5 用設定檔裡的 token 呼叫 API，確認有效且是同一個使用者
"""
import json, os, re, shutil, sys, tempfile, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from abhelp import AB  # noqa: E402

R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


def main():
    ab_bin = sys.argv[sys.argv.index("--ab") + 1]
    base, user, pw, marker = (os.environ["GITEA_URL"].rstrip("/"), os.environ["GITEA_USER"],
                              os.environ["GITEA_PASS"], os.environ["MARKER"])
    td = tempfile.mkdtemp(prefix="ne-gitea-")
    prof = os.path.join(td, "profile")
    outputs = []
    try:
        a = AB(ab_bin, "ne-gitea", prof)
        a.run("open", "about:blank")
        a.run("open", base + "/user/login")
        a.fill("textbox", r"user ?name|email", user)
        a.fill("textbox", r"password", pw)
        a.check("checkbox", r"remember")      # 使用者替 agent 登入時勾「記住這台裝置」，關掉瀏覽器後才仍是登入狀態
        a.click("button", r"sign in")
        check("1 登入後離開登入頁", "/user/login" not in a.url(), a.url())
        a.close()

        b = AB(ab_bin, "ne-gitea-2", prof)          # 新的程序、同一個 profile
        b.run("open", base + "/user/settings")
        check("2 新程序仍是登入狀態（設定頁沒有被導回登入）", "/user/login" not in b.url(), b.url())
        b.run("open", base + "/%s/secret-repo/raw/branch/main/secret.txt" % user)
        body = b.text()
        check("3 讀得到私有內容", marker in body, body[:120])
        c = AB(ab_bin, "ne-gitea-anon", os.path.join(td, "fresh"))
        c.run("open", base + "/%s/secret-repo/raw/branch/main/secret.txt" % user)
        check("3 全新 profile 讀不到", marker not in c.text())
        c.close()

        b.run("open", base + "/user/settings/applications")
        b.fill("textbox", r"token name|name", "no-errands")
        for it in b.snapshot():                     # 權限範圍：把 user / repository 設成可讀
            if it["role"] in ("combobox", "listbox") and re.search(r"user|repository", it["name"], re.I):
                b.run("select", it["ref"], "read:" + ("user" if re.search("user", it["name"], re.I) else "repository"))
        b.click("button", r"generate token")
        page = b.text()
        outputs.append(page)
        toks = re.findall(r"\b[0-9a-f]{40}\b", page)
        check("4 頁面顯示了新 token（一次性）", len(toks) >= 1)
        if not toks:   # 診斷：這是測試伺服器，頁面上沒有 token 可外洩
            b.dump()
            raw = b.run("snapshot").stdout
            print("form-area:", " | ".join(l.strip() for l in raw.splitlines()
                                           if re.search(r"scope|permission|select|combobox|option|token|error|flash", l, re.I))[:1500])
        cfg = os.path.join(td, ".env")
        open(cfg, "w").write("OTHER=keep\nGITEA_TOKEN=%s\n" % (toks[0] if toks else ""))
        b.close()
        tok = [l.split("=", 1)[1] for l in open(cfg).read().splitlines() if l.startswith("GITEA_TOKEN=")][0]
        req = urllib.request.Request(base + "/api/v1/user", headers={"Authorization": "token " + tok})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                me = json.loads(r.read().decode())
            check("5 設定檔裡的 token 呼叫 API 有效、是同一個使用者", me.get("login") == user, me.get("login"))
        except Exception as e:
            check("5 設定檔裡的 token 呼叫 API 有效、是同一個使用者", False, str(e)[:120])
        logs = " ".join(a.log + b.log)
        check("token 值沒有出現在指令輸出紀錄", tok and tok not in logs)
        print("steps:", logs)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
