#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neb — agent-browser 的網域守門代理（純 stdlib），仿 ned。

  neb.py [--session S] [--profile P] <agent-browser 子指令> [args...]

- 敏感網域（郵件、聊天、社群、金融）：對應的 app 不在政策檔 granted_apps → 拒絕（同意閘門）。
- 已授權的敏感網域：**唯讀**——放行 open/snapshot/get/screenshot/scroll/wait/close 與一般點擊；
  拒絕 fill/type/press/keyboard/upload/eval 等輸入類，以及名稱像「送出/傳送/回覆/發佈/Send/Reply/Post/Submit」的點擊；
  點擊只接受 neb snapshot 產生的 ref（才知道點的是什麼）。要傳訊息 → outbox.py draft。
- 其他網域：原樣交給 agent-browser。
- 每次呼叫寫 ledger（~/.no-errands/neb-ledger.jsonl；不含頁面內容）。
"""
import json, os, re, subprocess, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402

HOME = policy.HOME
LEDGER = os.path.join(HOME, "neb-ledger.jsonl")
STATE = os.path.join(HOME, "neb-state.json")

# 網域（尾綴比對）→ 正規 app 名
DOMAINS = {
    "mail.google.com": "gmail", "inbox.google.com": "gmail",
    "outlook.live.com": "outlook", "outlook.office.com": "outlook", "outlook.office365.com": "outlook",
    "mail.yahoo.com": "yahoo-mail", "mail.proton.me": "protonmail",
    "line.me": "line", "chat.line.biz": "line",
    "slack.com": "slack", "app.slack.com": "slack", "teams.microsoft.com": "teams", "teams.live.com": "teams",
    "discord.com": "discord", "web.whatsapp.com": "whatsapp", "web.telegram.org": "telegram",
    "messenger.com": "messenger", "facebook.com": "facebook", "instagram.com": "instagram",
    "x.com": "x", "twitter.com": "x", "threads.net": "threads", "linkedin.com": "linkedin",
    "chat.google.com": "google-chat", "web.wechat.com": "wechat",
}
INPUT_CMDS = {"fill", "type", "press", "keyboard", "keydown", "keyup", "upload", "eval", "insert", "select",
              "check", "uncheck", "drag", "set", "clipboard", "dialog"}
READ_CMDS = {"open", "goto", "navigate", "snapshot", "get", "screenshot", "scroll", "scrollintoview", "wait",
             "close", "back", "forward", "reload", "is", "find", "pdf", "tab", "console", "errors"}
SEND_WORDS = re.compile(r"(送出|傳送|发送|發送|回覆|回复|發佈|发布|發表|分享|轉傳|send|reply|post|submit|publish|share|forward|tweet)",
                        re.I)
REF_RE = re.compile(r"^@?(e\d+)$")


def ledger(**kw):
    os.makedirs(HOME, exist_ok=True)
    kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def refuse(reason, **kw):
    ledger(allowed=False, reason=reason, **kw)
    print(json.dumps({"ok": False, "tool": "neb", "error": reason}, ensure_ascii=False))
    sys.exit(3)


def ab_path():
    return os.path.join(HOME, "bin", "agent-browser.exe" if os.name == "nt" else "agent-browser")


def app_for_host(host, pol):
    host = (host or "").lower().split(":")[0]
    mm = (pol.get("mattermost") or {}).get("url")
    extra = {}
    if mm:
        extra[urllib.parse.urlparse(mm).hostname or ""] = "mattermost"
    for dom, app in list(extra.items()) + list(DOMAINS.items()):
        if dom and (host == dom or host.endswith("." + dom)):
            return app
    return None


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    os.makedirs(HOME, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


def main():
    a = sys.argv[1:]
    glob, i = [], 0
    while i < len(a) and a[i].startswith("--"):          # 全域旗標（--session/--profile/--headed…）
        glob.append(a[i])
        if a[i] in ("--session", "--profile", "--cdp", "--proxy", "--user-agent") and i + 1 < len(a):
            glob.append(a[i + 1]); i += 1
        i += 1
    rest = a[i:]
    if not rest:
        print(__doc__); sys.exit(2)
    sub, args = rest[0].lower(), rest[1:]
    session = glob[glob.index("--session") + 1] if "--session" in glob else "default"
    pol = policy.load(HOME)
    fake = os.environ.get("NE_FAKE_AB_PY")      # 測試掛鉤
    base_cmd = ([sys.executable, fake] if fake else [ab_path()]) + glob
    st = load_state()

    # 目標網域：open/goto 看參數；其他指令看這個 session 目前所在的網址
    if sub in ("open", "goto", "navigate") and args:
        url = args[0] if "://" in args[0] else "https://" + args[0]
    else:
        # 一律問瀏覽器目前在哪（點擊可能已經導到別的網域；不信任上次記下的網址）
        p = subprocess.run(base_cmd + ["get", "url"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        url = (p.stdout.strip().splitlines() or [""])[-1].strip() or st.get(session, {}).get("url", "")
    host = urllib.parse.urlparse(url).hostname or ""
    app = app_for_host(host, pol)

    if app:
        if not policy.is_granted(pol, app):
            refuse("敏感網站（%s）尚未授權：先在對話中取得使用者同意（同意閘門）" % app, app=app, sub=sub)
        if sub in INPUT_CMDS:
            refuse("已授權的敏感網站只能讀：不輸入、不按鍵、不上傳（要傳訊息請用 outbox.py draft）", app=app, sub=sub)
        if sub in ("click", "dblclick", "hover", "tap"):
            m = REF_RE.match(args[0]) if args else None
            if not m:
                refuse("敏感網站上的點擊只接受 neb snapshot 產生的 ref（例如 @e5），才能確認點的是什麼", app=app, sub=sub)
            name = st.get(session, {}).get("refs", {}).get(m.group(1))
            if name is None:
                refuse("這個 ref 不是 neb 最近一次 snapshot 產生的：先用 neb snapshot 重拍", app=app, sub=sub)
            if SEND_WORDS.search(name):
                refuse("這個元素看起來是送出/回覆/發佈類按鈕：agent 不能送出訊息，請用發件匣", app=app, sub=sub)
        elif sub not in READ_CMDS:
            refuse("已授權的敏感網站只允許讀取類指令（%s 不在允許清單）" % sub, app=app, sub=sub)

    p = subprocess.run(base_cmd + [rest[0]] + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stdout
    ent = st.setdefault(session, {})
    if sub in ("open", "goto", "navigate") and p.returncode == 0:
        ent["url"] = url
        ent["refs"] = {}
    if sub == "snapshot":
        refs = {}
        for line in out.splitlines():               # 例：- button "Send" [ref=e12] / - heading "X" [level=1, ref=e1]
            mm = re.search(r'"([^"]*)".*?\bref=(e\d+)', line)
            if mm:
                refs[mm.group(2)] = mm.group(1)
        ent["refs"] = refs
    if sub == "close":
        st.pop(session, None)
    save_state(st)
    ledger(allowed=True, app=app, sub=sub, host=host)
    sys.stdout.write(out)
    if p.stderr:
        sys.stderr.write(p.stderr)
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
