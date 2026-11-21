#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mm_ro — Mattermost 唯讀工具（純 stdlib）。**只會發 GET**，路徑限 /api/v4/。

  mm_ro.py whoami                         # 我是誰（使用者名稱、id）
  mm_ro.py channels [--names]             # 我的頻道（預設只回數量與類型統計；--names 才列名稱）
  mm_ro.py posts <channel_id> [--n 20]    # 某頻道最近 N 則（內容只回給呼叫者，不寫檔）
  mm_ro.py search "<詞>" [--n 20]         # 搜尋貼文（Mattermost 的搜尋 API 是 POST → 本工具不支援，會拒絕）
  mm_ro.py request <METHOD> <path>        # 通用唯讀請求：METHOD 必須是 GET、path 必須在 /api/v4/ 底下

設定（policy.json）：
  "mattermost": {"token_file": "<路徑>", "url": "https://<伺服器>"}
token 由本程式在執行期從 token_file 讀取：不輸出、不寫檔、不放進錯誤訊息。
會改變已讀狀態的端點（channels/members/.../view、mark-*、set-post-unread）都是 POST，本工具發不出去。
要傳訊息：outbox.py draft --service mattermost（使用者在發件匣核准後才送出）。
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402
import mm_common  # noqa: E402

ALLOWED_METHODS = {"GET"}   # 寫死


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def conf():
    pol = policy.load()
    mm = pol.get("mattermost", {})
    return mm_common.conf(mm.get("token_file"), mm.get("url"))


def arg(a, k, d=None):
    return a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else d


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    cmd = a[0]
    if cmd == "request":
        if len(a) < 3:
            out({"ok": False, "error": "用法：mm_ro.py request <METHOD> <path>"}, 2)
        method, path = a[1].upper(), a[2]
        if method not in ALLOWED_METHODS:
            out({"ok": False, "refused": True, "error": "唯讀工具只允許 GET（%s 被拒絕）。要傳訊息請用發件匣" % method}, 3)
        if not path.startswith("/api/v4/") or ".." in path:
            out({"ok": False, "refused": True, "error": "路徑必須在 /api/v4/ 底下"}, 3)
    if cmd == "search":
        out({"ok": False, "refused": True,
             "error": "Mattermost 的貼文搜尋 API 是 POST（/teams/{id}/posts/search），唯讀工具不發 POST"}, 3)
    try:
        base, tok = conf()
        if cmd == "whoami":
            u = mm_common.get(base, tok, "/users/me")
            out({"ok": True, "username": u.get("username"), "id": u.get("id")})
        if cmd == "channels":
            me = mm_common.get(base, tok, "/users/me")
            teams = mm_common.get(base, tok, "/users/%s/teams" % me["id"]) or []
            chans = []
            for t in teams:
                chans += mm_common.get(base, tok, "/users/%s/teams/%s/channels" % (me["id"], t["id"])) or []
            seen = {c["id"]: c for c in chans}
            types = {}
            for c in seen.values():
                types[c.get("type")] = types.get(c.get("type"), 0) + 1
            res = {"ok": True, "teams": len(teams), "channels": len(seen), "by_type": types}
            if "--names" in a:
                res["list"] = [{"id": c["id"], "team_id": c.get("team_id"), "type": c.get("type"),
                                "display_name": c.get("display_name") or c.get("name")} for c in seen.values()]
            out(res)
        if cmd == "posts":
            if len(a) < 2:
                out({"ok": False, "error": "用法：mm_ro.py posts <channel_id> [--n 20]"}, 2)
            n = max(1, min(200, int(arg(a, "--n", "20"))))
            d = mm_common.get(base, tok, "/channels/%s/posts" % a[1], {"per_page": n, "page": 0})
            posts = [d["posts"][i] for i in d.get("order", []) if i in d.get("posts", {})]
            res = {"ok": True, "count": len(posts)}
            if posts:
                ts = [p.get("create_at", 0) for p in posts]
                res["range"] = [time.strftime("%Y-%m-%d %H:%M", time.localtime(min(ts) / 1000)),
                                time.strftime("%Y-%m-%d %H:%M", time.localtime(max(ts) / 1000))]
            if "--content" in a:
                res["posts"] = [{"id": p["id"], "user_id": p.get("user_id"), "create_at": p.get("create_at"),
                                 "message": p.get("message")} for p in posts]
            out(res)
        if cmd == "request":
            out({"ok": True, "data": mm_common.get(base, tok, a[2][len("/api/v4"):])})
        out({"ok": False, "error": "沒有這個子指令（唯讀工具沒有送出能力）"}, 2)
    except RuntimeError as e:
        out({"ok": False, "error": str(e)[:300]}, 1)


if __name__ == "__main__":
    main()
