#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""no-errands 政策檔（~/.no-errands/policy.json）——純 stdlib，ned / chat_guard / outbox 共用。

規則分三層：
  不變量（不能調）：對外訊息一律走核准發件匣、agent 沒有直接送出的能力；不碰密碼/2FA；
                    不繞過存取控制；機密值不輸出。這些寫死在程式裡，不在本檔。
  閘門（逐一授權）：granted_apps——使用者在對話中指名同意過的個人帳號面。
  偏好（使用者可調）：chat.unread_protection / chat.whitelist / visibility。

CLI（agent 只在「使用者於對話中明確要求」時才可呼叫 set/grant/revoke/whitelist；
     無人值守的執行不得修改政策）：
  policy.py show                     # JSON
  policy.py summary                  # 一行摘要（doctor 寫進受管區塊）
  policy.py grant <app>  --reason "<使用者原話摘要>"
  policy.py revoke <app> --reason "..."
  policy.py whitelist add|remove <對話名稱> --reason "..."
  policy.py set unread_protection on|off --reason "..."
  policy.py set visibility background|watch --reason "..."
  policy.py set demo.persist on|off --reason "..."        # off＝示範一次性：不留紀錄、不改劇本
  policy.py set demo.learn propose|off --reason "..."
  policy.py domain add <host[:port]> <app> --reason "..."  # 登記敏感網域（例：經通道的示範主機）
  policy.py domain remove <host[:port]> --reason "..."
每次修改寫一筆 ledger（~/.no-errands/policy-ledger.jsonl；白名單名稱只記雜湊）。
"""
import copy, hashlib, json, os, sys, time

def _resolve_home():
    """NO_ERRANDS_HOME 優先；否則若本檔位於 <home>/bin/ 且該 home 有 config.json / policy.json，
    就用那個 home（--home 安裝時 agent 不會帶環境變數——實測 P0.3 驗收時草稿因此跑進了
    使用者真實的 ~/.no-errands）；最後才是 ~/.no-errands。"""
    env = os.environ.get("NO_ERRANDS_HOME")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(here) == "bin":
        cand = os.path.dirname(here)
        if any(os.path.exists(os.path.join(cand, f)) for f in ("config.json", "policy.json")):
            return cand
    return os.path.join(os.path.expanduser("~"), ".no-errands")


HOME = _resolve_home()

DEFAULTS = {
    "version": 1,
    "granted_apps": [],                      # 例：["line", "mattermost"]（小寫正規名）
    "chat": {
        "unread_protection": "on",           # on：不開、不讀「有未讀且不在白名單」的對話
        "whitelist": [],                     # 使用者指名、不受未讀保護限制的對話（私人資訊，不進 repo）
    },
    "visibility": "background",              # background：headless、語意操作、不搶焦點｜watch：讓使用者看
    # 會搶前景的步驟何時可以做：wait_idle（預設：使用者閒置滿 idle_min_s 秒才做，途中他一動就中止交還）｜ask（先問）
    "busy_behavior": "wait_idle",
    "idle_min_s": 180,
    "outbox": {"port": 8765},
    # 示範劇本（demo_runbook.py）：persist=off → 一次性（不留紀錄、不改劇本）；learn=off → 不提出劇本修改
    "demo": {"persist": "on", "learn": "propose"},
    # 使用者登記的敏感網域（例如經 SSH 通道的示範主機 localhost:8065）：neb 把它當敏感網站——
    # 未授權 → 拒；已授權 → 只讀，輸入與點擊都要有核准過的示範劇本。
    # 每筆 {"host": "localhost:8065" 或 "example.org", "app": "<正規 app 名>"}
    "sensitive_domains": [],
}

# app 別名 → 正規名。三平台：Windows 程序名、macOS 顯示名稱與 bundle id、Linux list-apps 名稱與 desktop id。
# 比對前一律小寫、去掉 .exe / .app / .desktop。
APP_ALIASES = {
    # 通訊
    "line": {"line", "lineapp", "line.exe", "jp.naver.line.mac", "jp.naver.line"},
    "mattermost": {"mattermost", "com.mattermost.desktop", "mattermost-desktop"},
    "teams": {"teams", "ms-teams", "microsoft teams", "microsoft teams (work or school)", "com.microsoft.teams",
              "com.microsoft.teams2", "teams-for-linux"},
    "slack": {"slack", "com.tinyspeck.slackmacgap", "com.slack.slack"},
    "telegram": {"telegram", "telegram desktop", "telegram-desktop", "ru.keepcoder.telegram", "org.telegram.desktop"},
    "whatsapp": {"whatsapp", "net.whatsapp.whatsapp", "desktop.whatsapp", "whatsapp-for-linux"},
    "discord": {"discord", "com.hnc.discord", "com.discordapp.discord"},
    "wechat": {"wechat", "weixin", "com.tencent.xinwechat"},
    "signal": {"signal", "signal-desktop", "org.whispersystems.signal-desktop"},
    "skype": {"skype", "com.skype.skype", "skypeforlinux"},
    "messenger": {"messenger", "com.facebook.archon"},
    "messages": {"messages", "com.apple.mobilesms", "com.apple.ichat"},          # macOS 訊息
    "facetime": {"facetime", "com.apple.facetime"},
    # 郵件
    "outlook": {"outlook", "olk", "hxoutlook", "microsoft outlook", "com.microsoft.outlook"},
    "mail": {"mail", "com.apple.mail", "hxmail"},
    "thunderbird": {"thunderbird", "org.mozilla.thunderbird", "betterbird"},
    "evolution": {"evolution", "org.gnome.evolution"},
    "geary": {"geary", "org.gnome.geary"},
    # 金融（常見台灣網銀/券商桌面端另行補充）
    "moneymanager": {"moneymanager"},
    "fake-chat": {"fake-chat", "fakechat"},   # 測試靶
}
SENSITIVE_APPS = {"line", "mattermost", "teams", "slack", "telegram", "whatsapp", "discord", "wechat", "signal",
                  "skype", "messenger", "messages", "facetime", "outlook", "mail", "thunderbird", "evolution",
                  "geary", "moneymanager"}
CHAT_APPS = {"line", "mattermost", "teams", "slack", "telegram", "whatsapp", "discord", "wechat", "signal",
             "skype", "messenger", "messages", "fake-chat"}


def path(home=None):
    return os.path.join(home or HOME, "policy.json")


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(home=None):
    """讀政策；檔案不存在或壞掉 → 回預設值（最保守）。"""
    try:
        with open(path(home), encoding="utf-8") as f:
            return _merge(DEFAULTS, json.load(f))
    except Exception:
        return copy.deepcopy(DEFAULTS)


def save(pol, home=None):
    p = path(home)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pol, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def ensure(home=None):
    """setup 用：不存在才建立預設檔；既有檔案不覆寫。回傳 (policy, created)。"""
    if os.path.exists(path(home)):
        return load(home), False
    pol = copy.deepcopy(DEFAULTS)
    save(pol, home)
    return pol, True


def canonical_app(name):
    n = (name or "").strip().lower()
    for suf in (".exe", ".app", ".desktop"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    for canon, names in APP_ALIASES.items():
        if n in names or n == canon:
            return canon
    return n


def is_granted(pol, app):
    return canonical_app(app) in {canonical_app(a) for a in pol.get("granted_apps", [])}


def in_whitelist(pol, chat_name):
    return chat_name in pol.get("chat", {}).get("whitelist", [])


def name_hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def sensitive_domain_app(pol, netloc):
    """netloc（host 或 host:port）是否為使用者登記的敏感網域 → 回 app 名或 None。
    登記值帶埠號時要完全相同；不帶埠號時比對主機名（含子網域）。"""
    netloc = (netloc or "").lower()
    host = netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc
    for ent in pol.get("sensitive_domains") or []:
        h = str(ent.get("host", "")).lower()
        if not h:
            continue
        if ":" in h:
            if netloc == h:
                return canonical_app(ent.get("app", h))
        elif host == h or host.endswith("." + h):
            return canonical_app(ent.get("app", h))
    return None


def summary(pol):
    apps = "、".join(pol.get("granted_apps") or []) or "（無）"
    up = pol.get("chat", {}).get("unread_protection", "on")
    wl = len(pol.get("chat", {}).get("whitelist", []))
    vis = {"background": "背景（不搶焦點）", "watch": "觀看模式（操作可見，會搶焦點）"}.get(
        pol.get("visibility"), pol.get("visibility"))
    return "已授權的個人帳號：%s｜未讀保護：%s（白名單 %d 個對話）｜可見度：%s" % (
        apps, "開" if up == "on" else "關", wl, vis)


def _ledger(home, action, detail, reason):
    os.makedirs(home or HOME, exist_ok=True)
    with open(os.path.join(home or HOME, "policy-ledger.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action,
                            "detail": detail, "reason": reason}, ensure_ascii=False) + "\n")


def _out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    home = None
    if "--home" in a:
        i = a.index("--home"); home = a[i + 1]; del a[i:i + 2]
    reason = ""
    if "--reason" in a:
        i = a.index("--reason"); reason = a[i + 1]; del a[i:i + 2]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    cmd = a[0]
    pol = load(home)
    if cmd == "show":
        shown = copy.deepcopy(pol)
        shown["chat"]["whitelist"] = ["<%d 個，名稱不顯示>" % len(pol["chat"]["whitelist"])]
        _out({"ok": True, "path": path(home), "policy": shown})
    if cmd == "summary":
        print(summary(pol)); sys.exit(0)
    if not reason:
        _out({"ok": False, "error": "修改政策必須附 --reason（使用者在對話中的要求摘要）；"
                                    "無人值守的執行不得修改政策"}, 3)
    if cmd in ("grant", "revoke") and len(a) == 2:
        app = canonical_app(a[1])
        apps = [canonical_app(x) for x in pol["granted_apps"]]
        if cmd == "grant" and app not in apps:
            apps.append(app)
        if cmd == "revoke":
            apps = [x for x in apps if x != app]
        pol["granted_apps"] = apps
        detail = {"app": app}
    elif cmd == "whitelist" and len(a) == 3 and a[1] in ("add", "remove"):
        wl = pol["chat"]["whitelist"]
        if a[1] == "add" and a[2] not in wl:
            wl.append(a[2])
        if a[1] == "remove":
            pol["chat"]["whitelist"] = [x for x in wl if x != a[2]]
        detail = {"whitelist": a[1], "name_sha256_12": name_hash(a[2])}
    elif cmd == "set" and len(a) == 3 and a[1] == "unread_protection" and a[2] in ("on", "off"):
        pol["chat"]["unread_protection"] = a[2]; detail = {"unread_protection": a[2]}
    elif cmd == "set" and len(a) == 3 and a[1] == "visibility" and a[2] in ("background", "watch"):
        pol["visibility"] = a[2]; detail = {"visibility": a[2]}
    elif cmd == "set" and len(a) == 3 and a[1] == "demo.persist" and a[2] in ("on", "off"):
        pol.setdefault("demo", {})["persist"] = a[2]; detail = {"demo.persist": a[2]}
    elif cmd == "set" and len(a) == 3 and a[1] == "demo.learn" and a[2] in ("propose", "off"):
        pol.setdefault("demo", {})["learn"] = a[2]; detail = {"demo.learn": a[2]}
    elif cmd == "domain" and len(a) == 4 and a[1] == "add":
        ents = [e for e in pol.get("sensitive_domains") or [] if str(e.get("host", "")).lower() != a[2].lower()]
        ents.append({"host": a[2].lower(), "app": canonical_app(a[3])})
        pol["sensitive_domains"] = ents; detail = {"domain_add": a[2].lower(), "app": canonical_app(a[3])}
    elif cmd == "domain" and len(a) == 3 and a[1] == "remove":
        pol["sensitive_domains"] = [e for e in pol.get("sensitive_domains") or []
                                    if str(e.get("host", "")).lower() != a[2].lower()]
        detail = {"domain_remove": a[2].lower()}
    else:
        _out({"ok": False, "error": "看不懂的指令；見 policy.py --help"}, 2)
    save(pol, home)
    _ledger(home, cmd, detail, reason)
    _out({"ok": True, "changed": detail, "summary": summary(pol),
          "note": "請把改了什麼告訴使用者"})


if __name__ == "__main__":
    main()
