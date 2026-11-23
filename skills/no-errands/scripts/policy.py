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
  policy.py set demo.learn propose|auto|off --reason "..."   # auto＝提出的調整自動套用並記進劇本歷史（凍結的劇本除外）
  policy.py set outbox.port <埠> --reason "..."            # 發件匣頁面的埠（只改這台的設定）
  policy.py domain add <host[:port]> <app> --reason "..."  # 登記敏感網域（例：經通道的示範主機）
      [--demo-accounts a,b] [--agent-login a]   # 示範用途：只有這些帳號能跑核准過的劇本；agent-login＝使用者同意 agent 代為登入
  policy.py set demo.reset_consent <還原指令的 sha256> --reason "<使用者原話>"   # 指令改了就要重新同意
  policy.py domain set <host[:port]> runbook_approval page|standing --reason "<使用者原話>"
      page（預設）：每份劇本清單都進發件匣頁面等使用者核准。
      standing（長期核准）：只要使用者曾在頁面上親自核准過同一台主機、帳號不超出那次範圍的劇本，
      發件匣伺服器就自動記一筆核准（註明依長期核准、連到那筆頁面核准）。只適用登記為示範用途的主機；
      內建的敏感 app（LINE、信箱、真實 Mattermost…）永遠不適用。
  policy.py domain remove <host[:port]> --reason "..."
  policy.py token-allow add|remove <專案資料夾> --reason "<使用者原話>"   # 這個專案裡的 agent 可以讀 Mattermost token 檔
      （預設誰都不行；guard-rules 的 token 組由 hook 依這份白名單判斷）
每次修改寫一筆 ledger（~/.no-errands/policy-ledger.jsonl；白名單名稱只記雜湊）。
"""
import copy, hashlib, json, os, re, sys, time

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
    # 什麼時候動手（wishes 第 3 項）：idle（預設：等使用者離開）｜now（立刻；使用者一動照樣讓開、縮小交還）
    "start_when": "idle",
    "outbox": {"port": 8765},
    # 示範劇本（demo_runbook.py）：persist=off → 一次性（不留紀錄、不改劇本）；learn=off → 不提出劇本修改
    # learn=auto → 提出的調整自動套用（凍結的劇本除外）
    "demo": {"persist": "on", "learn": "propose"},
    # 使用者登記的敏感網域（例如經 SSH 通道的示範主機 localhost:8065）：neb 把它當敏感網站——
    # 未授權 → 拒；已授權 → 只讀，輸入與點擊都要有核准過的示範劇本。
    # 每筆 {"host": "localhost:8065" 或 "example.org", "app": "<正規 app 名>"}；示範用途另有
    # demo/demo_accounts/agent_login，以及 runbook_approval: page（預設）| standing
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


_COUNT_RE = re.compile(r"(?:\s+\(\d+\)|\s*（\d+）|, ?\d+人)$")


def strip_count(name):
    """去掉群組顯示名稱結尾的「一組」人數標示：「名稱 (21)」「名稱（21）」「名稱 （21）」「名稱, 21人」→「名稱」。
    只去一次：「某某(2024) (21)」→「某某(2024)」。人數會變，名稱不會——比對與目標 id 只看名稱。
    半形括號前面「沒有空白」的不當人數（「某某(2024)」是名稱本身）：分不出來的時候寧可對不上（停手），不可對錯。"""
    return _COUNT_RE.sub("", name or "", count=1)


def canonical_chat(pol, chat_name):
    """--chat 對應到白名單裡的哪一筆：完全相同優先；否則去掉人數標示後相同（使用者把帶人數的名稱整串貼進來，
    或白名單存的是帶人數的舊值）。都對不上就原樣回傳。對上兩筆以上 → 回 None（不猜）。"""
    wl = pol.get("chat", {}).get("whitelist", [])
    if chat_name in wl:
        return chat_name
    hits = sorted({w for w in wl if strip_count(w) == strip_count(chat_name)})
    if len(hits) == 1:
        return hits[0]
    return None if hits else chat_name


def in_whitelist(pol, chat_name):
    return canonical_chat(pol, chat_name) in pol.get("chat", {}).get("whitelist", [])


def name_hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


DEFAULT_PORTS = {"http": 80, "https": 443}


def norm_host(host):
    """主機名正規化：小寫、去結尾的點與 IPv6 方括號；loopback 的各種寫法（localhost、127.x.x.x、::1、0.0.0.0）
    一律變成 "loopback"——同一台機器、同一個埠不能換個寫法就不設防。"""
    h = (host or "").strip().lower().rstrip(".").strip("[]")
    if h and all(c in "0123456789abcdefx." for c in h) and any(c.isdigit() for c in h):
        try:                                    # 十進位／十六進位／簡寫的 IPv4（2130706433、0x7f000001、127.1）
            import socket
            h = socket.inet_ntoa(socket.inet_aton(h))
        except (OSError, ValueError):
            pass
    if h in ("localhost", "::1", "0.0.0.0", "0:0:0:0:0:0:0:1", "::") or h.endswith(".localhost") \
            or (h.startswith("127.") and all(p.isdigit() for p in h.split("."))) or h.startswith("::ffff:127."):
        return "loopback"
    return h


def split_reg(reg):
    """登記值 'host[:port]' → (正規化主機, 埠或 None)。"""
    reg = (reg or "").strip().lower()
    if reg.startswith("["):                                   # [::1]:8065
        host, _, rest = reg[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif reg.count(":") == 1:
        host, port = reg.split(":")
    else:
        host, port = reg, ""
    return norm_host(host), (int(port) if port.isdigit() else None)


def url_host_port(url):
    """網址 → (正規化主機, 埠)。用 hostname/port（userinfo 不影響），補預設埠。"""
    import urllib.parse
    u = urllib.parse.urlparse(url or "")
    try:
        port = u.port
    except ValueError:
        port = None
    return norm_host(u.hostname or ""), port or DEFAULT_PORTS.get((u.scheme or "").lower())


def sensitive_domain_entry(pol, host, port):
    """(正規化主機, 埠) 是否為使用者登記的敏感網域 → 回登記項目或 None。
    登記值帶埠號時主機與埠都要相同；不帶埠號時比對主機名（含子網域）。"""
    for ent in pol.get("sensitive_domains") or []:
        h, p = split_reg(str(ent.get("host", "")))
        if not h:
            continue
        if p is not None:
            if host == h and port == p:
                return ent
        elif host == h or (h != "loopback" and host.endswith("." + h)):
            return ent
    return None


def sensitive_domain_app(pol, netloc_or_url):
    """相容舊呼叫：接受網址或 host[:port]。"""
    s = netloc_or_url or ""
    host, port = url_host_port(s) if "://" in s else split_reg(s)
    ent = sensitive_domain_entry(pol, host, port)
    return canonical_app(ent.get("app", ent.get("host"))) if ent else None


def runbook_approval_mode(ent):
    """登記項目的劇本核准方式："standing" 只在示範用途、使用者設成 standing、而且不是內建敏感 app 時成立；
    其他一律 "page"（給其他人的安全預設）。"""
    if not ent or not ent.get("demo") or ent.get("runbook_approval") != "standing":
        return "page"
    if canonical_app(ent.get("app", "")) in SENSITIVE_APPS | CHAT_APPS:
        return "page"
    return "standing"


def summary(pol):
    apps = "、".join(pol.get("granted_apps") or []) or "（無）"
    up = pol.get("chat", {}).get("unread_protection", "on")
    wl = len(pol.get("chat", {}).get("whitelist", []))
    vis = {"background": "背景（不搶焦點）", "watch": "觀看模式（操作可見，會搶焦點）"}.get(
        pol.get("visibility"), pol.get("visibility"))
    sw = "立刻（你一動就讓開）" if pol.get("start_when") == "now" else "等你離開"
    return "已授權的個人帳號：%s｜未讀保護：%s（白名單 %d 個對話）｜可見度：%s｜會跳到前面的操作：%s" % (
        apps, "開" if up == "on" else "關", wl, vis, sw)


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
    demo_accts = agent_login = None
    if "--demo-accounts" in a:
        i = a.index("--demo-accounts"); demo_accts = [x for x in a[i + 1].split(",") if x]; del a[i:i + 2]
    if "--agent-login" in a:
        i = a.index("--agent-login"); agent_login = [x for x in a[i + 1].split(",") if x]; del a[i:i + 2]
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
    elif cmd == "set" and len(a) == 3 and a[1] == "start_when" and a[2] in ("idle", "now"):
        pol["start_when"] = a[2]; detail = {"start_when": a[2]}
    elif cmd == "set" and len(a) == 3 and a[1] == "demo.persist" and a[2] in ("on", "off"):
        pol.setdefault("demo", {})["persist"] = a[2]; detail = {"demo.persist": a[2]}
    elif cmd == "set" and len(a) == 3 and a[1] == "demo.learn" and a[2] in ("propose", "auto", "off"):
        pol.setdefault("demo", {})["learn"] = a[2]; detail = {"demo.learn": a[2]}
    elif cmd == "domain" and len(a) == 4 and a[1] == "add":
        ents = [e for e in pol.get("sensitive_domains") or [] if str(e.get("host", "")).lower() != a[2].lower()]
        old = [e for e in pol.get("sensitive_domains") or [] if str(e.get("host", "")).lower() == a[2].lower()]
        ent = {"host": a[2].lower(), "app": canonical_app(a[3])}
        if demo_accts is not None:          # 示範用途：只有這些帳號能執行核准過的劇本
            ent.update(demo=True, demo_accounts=demo_accts,
                       agent_login=[x for x in (agent_login or []) if x in demo_accts])
            if old and old[0].get("runbook_approval"):      # 重新登記（例如改帳號）不默默改掉核准方式
                ent["runbook_approval"] = old[0]["runbook_approval"]
        ents.append(ent)
        pol["sensitive_domains"] = ents
        detail = {"domain_add": a[2].lower(), "app": canonical_app(a[3]), "demo_accounts": ent.get("demo_accounts"),
                  "agent_login": ent.get("agent_login")}
    elif cmd == "set" and len(a) == 3 and a[1] == "demo.reset_consent":
        pol.setdefault("demo", {})["reset_consent"] = {"commands_sha256": a[2], "reason": reason,
                                                       "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        detail = {"demo.reset_consent": a[2][:16]}
    elif cmd == "domain" and len(a) == 5 and a[1] == "set" and a[3] == "runbook_approval"             and a[4] in ("page", "standing"):
        ents = pol.get("sensitive_domains") or []
        hit = [e for e in ents if str(e.get("host", "")).lower() == a[2].lower()]
        if not hit:
            _out({"ok": False, "error": "沒有登記這個主機；先 policy.py domain add"}, 2)
        if a[4] == "standing":
            if not hit[0].get("demo"):
                _out({"ok": False, "error": "長期核准只適用登記為示範用途的主機（domain add … --demo-accounts …）"}, 3)
            if canonical_app(hit[0].get("app", "")) in SENSITIVE_APPS | CHAT_APPS:
                _out({"ok": False, "error": "內建的敏感 app（通訊、郵件、金融）永遠不適用長期核准"}, 3)
        hit[0]["runbook_approval"] = a[4]
        detail = {"domain": a[2].lower(), "runbook_approval": a[4]}
    elif cmd == "set" and len(a) == 3 and a[1] == "outbox.port" and a[2].isdigit() and 1024 <= int(a[2]) <= 65535:
        pol.setdefault("outbox", {})["port"] = int(a[2]); detail = {"outbox.port": int(a[2])}
    elif cmd == "domain" and len(a) == 3 and a[1] == "remove":
        pol["sensitive_domains"] = [e for e in pol.get("sensitive_domains") or []
                                    if str(e.get("host", "")).lower() != a[2].lower()]
        detail = {"domain_remove": a[2].lower()}
    elif cmd == "token-allow" and len(a) == 3 and a[1] in ("add", "remove"):
        mm = pol.setdefault("mattermost", {})
        d = os.path.abspath(a[2])
        cur = [x for x in mm.get("token_read_allow") or [] if os.path.normcase(x) != os.path.normcase(d)]
        if a[1] == "add":
            cur.append(d)
        mm["token_read_allow"] = cur
        detail = {"token_allow": a[1], "dir": d}
    else:
        _out({"ok": False, "error": "看不懂的指令；見 policy.py --help"}, 2)
    save(pol, home)
    _ledger(home, cmd, detail, reason)
    _out({"ok": True, "changed": detail, "summary": summary(pol),
          "note": "請把改了什麼告訴使用者"})


if __name__ == "__main__":
    main()
