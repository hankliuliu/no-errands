#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neb — agent-browser 的網域守門代理（純 stdlib），仿 ned。

  neb.py [--session S] [--profile P] <agent-browser 子指令> [args...]

- 敏感網域（郵件、聊天、社群、金融）：對應的 app 不在政策檔 granted_apps → 拒絕（同意閘門）。
- 已授權的敏感網域：**唯讀**——放行 open/snapshot/get/screenshot/scroll/wait/close 與一般點擊；
  拒絕 fill/type/press/keyboard/upload/eval 等輸入類，以及名稱像「送出/傳送/回覆/發佈/Send/Reply/Post/Submit」的點擊；
  點擊只接受 neb snapshot 產生的 ref（才知道點的是什麼）。要傳訊息 → outbox.py draft。
- 使用者登記的敏感網域（policy.json 的 sensitive_domains，例：經 SSH 通道的示範主機 localhost:8065）同樣處理，
  而且連點擊都要有核准過的示範劇本。
- 示範劇本的窄例外：`neb … <fill|type|press|click|check|uncheck> … --runbook-approval <id> --step <n>`。
  放行條件全部成立：核准有效（未到期、未撤銷、清單雜湊相符）；目前主機＝核准的目標主機；--profile 的資料夾名
  ＝該步核准的示範帳號；輸入的欄位名稱與文字逐字相符（或符合核准的變動規則）／按鍵與點擊的名稱在該步清單內。
  任一不符 → 拒絕並回「需重新核准」。沒帶核准時行為不變。
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


RB_INPUT = {"fill", "type", "press", "check", "uncheck"}
RB_CLICK = {"click", "check", "uncheck"}


def pop_flag(args, name):
    """從參數裡取出 `name <值>` 並移除（--runbook-approval / --step 不傳給 agent-browser）。"""
    if name in args:
        i = args.index(name)
        val = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        return val
    return None


def runbook_check(aid, step, sub, args, refs, profile, netloc):
    """示範劇本的窄例外：全部條件成立才回 (True, '')；否則 (False, 原因)。原因不含輸入內容。"""
    import outbox  # noqa: E402（延遲匯入：沒帶核准時不需要）
    ok, rec = outbox.runbook_approval(aid)
    if not ok:
        return False, rec
    doc = outbox.parse_runbook_body(rec["body"])
    ap = rec.get("approval") or {}
    if outbox.sends_sha256(doc["sends"]) != ap.get("sends_sha256"):
        return False, "核准記錄的清單雜湊對不上（檔案被改過）"
    if (netloc or "").lower() != ap.get("target"):
        return False, "目前主機（%s）不是核准的目標主機（%s）" % (netloc, ap.get("target"))
    ents = [s for s in doc["sends"] if str(s.get("step")) == str(step)]
    if not ents:
        return False, "第 %s 步不在核准的送出清單裡" % step
    ent = ents[0]
    acct = os.path.basename(os.path.normpath(profile or "")) if profile else ""
    if acct != ent.get("account"):
        return False, "這個瀏覽器 profile（%s）不是第 %s 步核准的示範帳號（%s）" % (acct or "未指定", step, ent.get("account"))
    if sub == "press":
        if not args or args[0] not in (ent.get("keys") or []):
            return False, "第 %s 步沒有核准這個按鍵" % step
        return True, ""
    m = REF_RE.match(args[0]) if args else None
    if not m:
        return False, "劇本步驟只接受 neb snapshot 產生的 ref"
    name = refs.get(m.group(1))
    if name is None:
        return False, "這個 ref 不是 neb 最近一次 snapshot 產生的"
    if sub in ("fill", "type"):
        text = args[1] if len(args) > 1 else ""
        for inp in ent.get("inputs") or []:
            if name in (inp.get("fields") or []):
                if inp.get("text") is not None and text == inp["text"]:
                    return True, ""
                if inp.get("pattern") and re.fullmatch(inp["pattern"], text, re.S):
                    return True, ""
        return False, "第 %s 步沒有核准對「%s」輸入這段文字" % (step, name)
    if sub in RB_CLICK:
        if name in (ent.get("clicks") or []):
            return True, ""
        return False, "第 %s 步沒有核准點「%s」" % (step, name)
    return False, "劇本不允許 %s" % sub


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
    rb_id, rb_step = pop_flag(args, "--runbook-approval"), pop_flag(args, "--step")
    rest = [rest[0]] + args
    session = glob[glob.index("--session") + 1] if "--session" in glob else "default"
    profile = glob[glob.index("--profile") + 1] if "--profile" in glob else None
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
    netloc = (urllib.parse.urlparse(url).netloc or "").lower()
    registered = policy.sensitive_domain_app(pol, netloc)      # 使用者登記的敏感網域（例：示範主機）
    app = registered or app_for_host(host, pol)

    if app and rb_id is not None:
        if not policy.is_granted(pol, app):
            refuse("敏感網站（%s）尚未授權：先在對話中取得使用者同意（同意閘門）" % app, app=app, sub=sub)
        if sub in RB_INPUT or sub in RB_CLICK:
            ok, why = runbook_check(rb_id, rb_step, sub, args, load_state().get(session, {}).get("refs", {}),
                                    profile, netloc)
            ledger(runbook=rb_id, step=rb_step, app=app, sub=sub, host=netloc, runbook_ok=ok)
            if not ok:
                refuse("需重新核准：" + why, app=app, sub=sub, runbook=rb_id, step=rb_step)
        elif sub not in READ_CMDS:
            refuse("劇本執行時也只允許讀取類指令與核准過的輸入（%s 不在允許清單）" % sub, app=app, sub=sub)
    elif app:
        if not policy.is_granted(pol, app):
            refuse("敏感網站（%s）尚未授權：先在對話中取得使用者同意（同意閘門）" % app, app=app, sub=sub)
        if registered and sub in ("click", "dblclick", "tap", "check", "uncheck"):
            refuse("登記的敏感網域（%s）上的點擊要有核准過的示範劇本（--runbook-approval <id> --step <n>）" % netloc,
                   app=app, sub=sub)
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
