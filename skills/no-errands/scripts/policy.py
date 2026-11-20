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
每次修改寫一筆 ledger（~/.no-errands/policy-ledger.jsonl；白名單名稱只記雜湊）。
"""
import copy, hashlib, json, os, sys, time

HOME = os.environ.get("NO_ERRANDS_HOME") or os.path.join(os.path.expanduser("~"), ".no-errands")

DEFAULTS = {
    "version": 1,
    "granted_apps": [],                      # 例：["line", "mattermost"]（小寫正規名）
    "chat": {
        "unread_protection": "on",           # on：不開、不讀「有未讀且不在白名單」的對話
        "whitelist": [],                     # 使用者指名、不受未讀保護限制的對話（私人資訊，不進 repo）
    },
    "visibility": "background",              # background：headless、語意操作、不搶焦點｜watch：讓使用者看
    "outbox": {"port": 8765},
}

# app 別名 → 正規名（ned 用程序名比對；chat_guard/outbox 用服務名）
APP_ALIASES = {
    "line": {"line.exe", "lineapp.exe", "line"},
    "mattermost": {"mattermost.exe", "mattermost"},
    "teams": {"teams.exe", "ms-teams.exe", "teams"},
    "outlook": {"outlook.exe", "olk.exe", "hxoutlook.exe", "outlook"},
    "fake-chat": {"fake-chat", "fakechat"},   # 測試靶
}


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
    for canon, names in APP_ALIASES.items():
        if n in names or n == canon:
            return canon
    return n[:-4] if n.endswith(".exe") else n


def is_granted(pol, app):
    return canonical_app(app) in {canonical_app(a) for a in pol.get("granted_apps", [])}


def in_whitelist(pol, chat_name):
    return chat_name in pol.get("chat", {}).get("whitelist", [])


def name_hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


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
    else:
        _out({"ok": False, "error": "看不懂的指令；見 policy.py --help"}, 2)
    save(pol, home)
    _ledger(home, cmd, detail, reason)
    _out({"ok": True, "changed": detail, "summary": summary(pol),
          "note": "請把改了什麼告訴使用者"})


if __name__ == "__main__":
    main()
