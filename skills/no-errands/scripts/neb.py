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
import nowin  # noqa: E402,F401  Windows：子程序不開主控台視窗（使用者回報視窗一直彈出）

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
# ---- agent-browser 0.43.0 指令與選項對照（升級 agent-browser 時要重對：docs/agent-browser-command-audit.md、tests/test_neb.py）----
# 已授權的敏感網站上只放行這些（其餘一律拒）：只讀、或經 ref＋名稱檢查的點擊、或導覽到檢查過的網址
SENSITIVE_ALLOWED = {"snapshot", "get", "is", "screenshot", "scroll", "scrollintoview", "wait", "back", "forward",
                     "reload", "close", "console", "errors", "tab", "session", "pdf", "diff", "find",
                     "click", "dblclick", "hover", "tap", "open", "goto", "navigate", "read"}
# 繞過 neb 逐步檢查的控制面指令（AI 代理、MCP、錄製重播、外掛、直接 CDP、自動填帳密、確認待決動作…）：任何網站都拒
CONTROL_CMDS = {"chat", "mcp", "webmcp", "flow", "plugin", "connect", "auth", "confirm", "deny", "stream",
                "dashboard", "inspect", "removeinitscript", "upgrade"}
# 能注入腳本/標頭、改 UA、接管或換掉瀏覽器、改信任設定的全域選項：出現在指令列任何位置都拒（不只子指令前面）
BAD_OPTS = {"--init-script", "--enable", "--extension", "--headers", "--args", "--cdp", "--cdp-headers",
            "--auto-connect", "--executable-path", "--state", "--restore", "--restore-check-fn", "--restore-check-url",
            "--restore-check-text", "--restore-save", "--session-name", "--action-policy", "--confirm-actions",
            "--confirm-interactive", "--config", "--provider", "-p", "--user-agent", "--proxy", "--proxy-bypass",
            "--ca-cert", "--no-ca-cert", "--allow-file-access", "--capture", "--engine", "--namespace",
            "--ignore-https-errors", "--device", "--model"}
# find 的動作字：預設動作就是 click，所以敏感網站上只接受明寫 text、且沒有任何動作字
FIND_ACTIONS = {"click", "dblclick", "fill", "type", "hover", "focus", "check", "uncheck", "tap", "press", "select",
                "clear", "upload", "drag"}
# 舊名保留給其他模組／測試參照
INPUT_CMDS = {"fill", "type", "press", "keyboard", "keydown", "keyup", "upload", "eval", "insert", "select",
              "check", "uncheck", "drag", "set", "clipboard", "dialog", "mouse", "focus", "download", "cookies",
              "storage", "network", "pushstate", "vitals", "a11y", "record", "trace", "profiler", "react", "highlight"}
READ_CMDS = SENSITIVE_ALLOWED
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


_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):(.*)$")


def norm_url(u):
    """回 (正規化網址, 錯誤)。只接受 http(s) 與 about:blank；javascript:/data:/file: 等一律拒。"""
    u = (u or "").strip()
    if u.lower() == "about:blank":
        return u, None
    m = _SCHEME.match(u)
    if m and not re.match(r"^\d", m.group(2)):          # 有 scheme（排除 localhost:8065 這種「主機:埠」）
        sch = m.group(1).lower()
        if sch not in ("http", "https"):
            return None, "不接受 %s: 開頭的網址（只允許 http/https 與 about:blank）" % sch
        return u, None
    return "https://" + u, None


def nav_targets(sub, args):
    """這個指令會把瀏覽器帶到哪些網址（open/read/tab new/diff url/vitals/a11y/record/pushstate）。"""
    pos = [x for x in args if not x.startswith("-")]
    if sub in ("open", "goto", "navigate", "pushstate"):
        return pos[:1]
    if sub in ("read", "vitals", "a11y"):
        return pos[:1]
    if sub == "tab" and pos[:1] == ["new"]:
        return pos[1:2]
    if sub == "diff" and pos[:1] == ["url"]:
        return pos[1:3]
    if sub == "record" and pos[:1] == ["start"]:
        return pos[2:3]
    return []


def run_batch(glob, args):
    """batch 會連續執行多個指令、中途換頁 → 拆開，每一個都重新經過 neb 檢查（任何一個被拒就停）。"""
    cmds = [x for x in args if not x.startswith("--")]
    if not cmds:
        refuse("batch 從 stdin 讀指令時 neb 看不到內容：把每個指令寫成參數，或分開執行")
    import shlex
    rc_all = 0
    for i, c in enumerate(cmds, 1):
        toks = shlex.split(c, posix=(os.name != "nt"))
        toks = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in toks]
        p = subprocess.run([sys.executable, os.path.abspath(__file__)] + glob + toks, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        sys.stdout.write(p.stdout)
        if p.returncode == 3:
            refuse("batch 的第 %d 個指令被拒，後面的不執行" % i, sub="batch")
        rc_all = rc_all or p.returncode
    sys.exit(rc_all)


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
    # 任何網站都適用的檢查（先於網域判斷）
    for tok in a:
        if tok in BAD_OPTS or tok.split("=", 1)[0] in BAD_OPTS:
            refuse("不接受 %s：它能注入腳本或標頭、改身分、或接管瀏覽器，會繞過守門" % tok.split("=", 1)[0], sub=sub)
    if sub in CONTROL_CMDS:
        refuse("%s 會繞過 neb 的逐步檢查（AI 代理／MCP／重播／外掛／直接 CDP／自動登入），不經 neb 使用" % sub, sub=sub)
    if sub == "get" and args[:1] == ["cdp-url"]:
        refuse("get cdp-url 會交出瀏覽器的直接控制權，不提供", sub=sub)
    if sub == "batch":
        run_batch(glob, args)
    session = glob[glob.index("--session") + 1] if "--session" in glob else "default"
    pol = policy.load(HOME)
    fake = os.environ.get("NE_FAKE_AB_PY")      # 測試掛鉤
    base_cmd = ([sys.executable, fake] if fake else [ab_path()]) + glob
    st = load_state()

    # 目標網域：導覽類看參數裡的每個網址；會作用在目前頁面的指令再加上目前網址
    targets = []
    for u in nav_targets(sub, args):
        nu, err = norm_url(u)
        if err:
            refuse(err, sub=sub)
        targets.append(nu)
    url = targets[0] if targets else ""
    acts_on_current = sub not in ("open", "goto", "navigate", "read") and not (sub == "tab" and args[:1] == ["new"])
    hosts = [urllib.parse.urlparse(t).hostname or "" for t in targets]
    if acts_on_current:
        # 一律問瀏覽器目前在哪（點擊可能已經導到別的網域；不信任上次記下的網址）
        p = subprocess.run(base_cmd + ["get", "url"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        cur = (p.stdout.strip().splitlines() or [""])[-1].strip() or st.get(session, {}).get("url", "")
        url = url or cur
        hosts.append(urllib.parse.urlparse(cur).hostname or "")
    apps = [x for x in (app_for_host(h, pol) for h in hosts) if x]
    app = apps[0] if apps else None
    host = hosts[0] if hosts else ""

    for ap in apps:
        if not policy.is_granted(pol, ap):
            refuse("敏感網站（%s）尚未授權：先在對話中取得使用者同意（同意閘門）" % ap, app=ap, sub=sub)
    if app:
        if sub not in SENSITIVE_ALLOWED:
            refuse("已授權的敏感網站只能讀：%s 不在允許清單（不輸入、不按鍵、不執行腳本；要傳訊息請用 outbox.py draft）" % sub,
                   app=app, sub=sub)
        if sub == "find":
            rest_tokens = [x.lower() for x in args[2:]]
            if "text" not in rest_tokens or any(t in FIND_ACTIONS for t in rest_tokens):
                refuse("敏感網站上的 find 只能讀文字（明寫 text 動作；不寫動作時預設是 click）。"
                       "要點東西：neb snapshot 取 ref，再 neb click @ref", app=app, sub=sub)
        if sub == "wait" and any(x == "--fn" or x.startswith("--fn=") for x in args):
            refuse("wait --fn 會在頁面上執行 JavaScript（等同 eval），敏感網站上不接受", app=app, sub=sub)
        if sub == "diff" and args[:1] not in (["snapshot"], ["screenshot"]):
            refuse("敏感網站上 diff 只接受 snapshot／screenshot", app=app, sub=sub)
        if sub in ("click", "dblclick", "hover", "tap"):
            m = REF_RE.match(args[0]) if args else None
            if not m:
                refuse("敏感網站上的點擊只接受 neb snapshot 產生的 ref（例如 @e5），才能確認點的是什麼", app=app, sub=sub)
            name = st.get(session, {}).get("refs", {}).get(m.group(1))
            if name is None:
                refuse("這個 ref 不是 neb 最近一次 snapshot 產生的：先用 neb snapshot 重拍", app=app, sub=sub)
            if SEND_WORDS.search(name):
                refuse("這個元素看起來是送出/回覆/發佈類按鈕：agent 不能送出訊息，請用發件匣", app=app, sub=sub)

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
