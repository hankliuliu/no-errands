#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ned — app 範圍鎖定的 agent-desktop 代理（純 stdlib）。

呼叫形式固定：  ned.py <App.exe> <子指令> [args...]
目的：讓「目標 app」出現在指令文字裡（權限規則可比對）、
     讓跨 app 誤操作在機制上不可能、讓敏感 app 直接被拒。

規則：
- <App> 必須是正規名稱：Windows 程序名、macOS 顯示名稱或 bundle id、Linux list-apps 名稱；
  含路徑分隔、引號、分號等一律拒絕。敏感判定用三平台別名表正規化（policy.APP_ALIASES）。
- 敏感類別（通訊/郵件/金融）：未列入政策檔 granted_apps → 拒絕（同意閘門）。
  已授權 → 只放行觀察類子指令；通訊類在未讀保護開啟時連觀察也拒絕（清單會露出未讀預覽），
  改走 chat_guard（只碰指名對話）。任何會改狀態的動作（click/set-value…）一律走 chat_guard。
- 可見度 background（預設）：拒絕會搶焦點或動滑鼠鍵盤的子指令（focus/type/hover/drag）。
- 發件匣頁面是 agent 禁區：snapshot 內含發件匣標記的 ref 一律不得操作。
- snapshot/find 自動綁 --app；ref 動作檢查 ref 的 snapshot 屬於宣告的 app，否則拒絕。
- 每次呼叫寫 ledger（~/.no-errands/ned-ledger.jsonl；不含任何 app 內容字串）。
"""
import json, os, re, subprocess, sys, time
import nowin  # noqa: E402,F401  Windows：子程序不開主控台視窗（使用者回報視窗一直彈出）

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402  同目錄（setup 一起複製到 ~/.no-errands/bin/）

HOME = policy.HOME  # 同一套解析：env → 本檔所在的 <home>/bin → ~/.no-errands
STATE = os.path.join(HOME, "ned-state.json")
LEDGER = os.path.join(HOME, "ned-ledger.jsonl")

SENSITIVE = policy.SENSITIVE_APPS        # 正規名（三平台別名見 policy.APP_ALIASES）
CHAT_APPS = policy.CHAT_APPS
# Windows 程序名（Notepad.exe）、macOS 顯示名稱或 bundle id（Google Chrome、com.apple.mail）、
# Linux list-apps 名稱（gnome-calculator）。允許空白，不允許路徑分隔、引號、分號等。
APP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,79}$")
REF_RE = re.compile(r"@(s[0-9a-z]+):e\d+")
# ref 動作子指令（需要 app 一致性檢查）
REF_CMDS = {"click", "double-click", "triple-click", "right-click", "type", "set-value",
            "clear", "focus", "select", "toggle", "check", "uncheck", "expand", "collapse",
            "scroll", "scroll-to", "get", "is", "hover", "drag"}
APP_CMDS = {"snapshot", "find", "screenshot", "list-windows"}  # 自動綁 --app
PASS_CMDS = {"launch", "close-app", "wait", "status", "version", "skills"}
OBSERVE_CMDS = {"snapshot", "find", "screenshot", "list-windows", "get", "is", "status", "version"}
FOCUS_CMDS = {"focus", "type", "hover", "drag"}   # 會搶焦點或動滑鼠鍵盤
# find/snapshot 的 --surface：只允許屬於這個 app 的表面；desktop/taskbar/notification-center/start-menu/focused…
# 會讀到別的 app 的內容（例如通知中心裡的聊天預覽）→ 拒。（agent-desktop 1.3.1 對照：docs/agent-desktop-command-audit.md）
APP_SURFACES = {"window", "menu", "menubar", "sheet", "popover", "alert", "toolbar"}
OUTBOX_MARK = "NE-OUTBOX"                           # 發件匣頁面標題與 a11y 名稱都含此字串

def ledger(app, sub, allowed, reason=""):
    os.makedirs(HOME, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "app": app,
                            "sub": sub, "allowed": allowed, "reason": reason},
                           ensure_ascii=False) + "\n")

def refuse(app, sub, reason, code=3):
    ledger(app, sub, False, reason)
    print(json.dumps({"ok": False, "tool": "ned", "error": reason}, ensure_ascii=False))
    sys.exit(code)

def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}

def save_state(st):
    os.makedirs(HOME, exist_ok=True)
    # 只保留最近 200 筆 snapshot 對應
    if len(st) > 200:
        for k in list(st.keys())[:-200]:
            st.pop(k, None)
    json.dump(st, open(STATE, "w", encoding="utf-8"))

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    app, sub = sys.argv[1], sys.argv[2]
    rest = sys.argv[3:]
    ad = os.path.join(HOME, "bin", "agent-desktop.exe" if os.name == "nt" else "agent-desktop")

    if not APP_RE.match(app) or app != app.strip() or "  " in app:
        refuse(app, sub, "app 名不是正規名稱（Windows 程序名、macOS 顯示名稱/bundle id、Linux list-apps 名稱）；"
                         "其他拼法一律拒絕")
    pol = policy.load(HOME)
    canon = policy.canonical_app(app)
    if canon in SENSITIVE or canon in CHAT_APPS:
        if not policy.is_granted(pol, app):
            refuse(app, sub, "敏感類別（通訊/郵件/金融）預設禁區：使用者尚未在對話中指名授權此 app"
                             "（同意閘門）。先在對話中說明要用哪個 app、讀什麼、不做什麼並取得同意，"
                             "再由使用者要求下執行 policy.py grant")
        if sub not in OBSERVE_CMDS:
            refuse(app, sub, "已授權的敏感 app 只允許觀察類子指令；會改狀態的操作走 chat_guard"
                             "（訊息輸入框、送出鈕永遠是禁區；要傳訊息只能起草到發件匣）")
        if canon in CHAT_APPS and pol["chat"].get("unread_protection") == "on":
            refuse(app, sub, "未讀保護開啟中：聊天 app 的畫面/結構可能含未讀預覽，不可用一般桌面"
                             "操作觀察；請用 chat_guard 只開指名對話")
    if "--headed" in rest and pol.get("visibility") != "watch":
        refuse(app, sub, "--headed 允許實體鍵盤滑鼠與搶焦點；背景模式（預設）不接受。"
                         "使用者要看你操作時，請他要求後把政策改成觀看模式")
    if "--surface" in rest:
        sv = rest[rest.index("--surface") + 1] if rest.index("--surface") + 1 < len(rest) else ""
        if sv not in APP_SURFACES:
            refuse(app, sub, "--surface %s 會讀到 %s 以外的內容（桌面、工作列、通知中心、目前焦點…）：只允許 %s"
                             % (sv, app, "/".join(sorted(APP_SURFACES))))
    if sub == "wait" and "--notification" in rest:
        refuse(app, sub, "wait --notification 會讀到所有 app 的通知內容，不提供")
    if sub in FOCUS_CMDS and pol.get("visibility") != "watch":
        refuse(app, sub, "背景模式（預設）不送鍵盤事件、不搶焦點、不動滑鼠：改用 set-value / click "
                         "語意操作；若使用者要看你操作，請他要求後把政策改成觀看模式")

    st = load_state()
    # 測試掛鉤：NE_FAKE_AD_PY 指向一個假的 agent-desktop（python 腳本），只給 tests/test_ned.py 用
    fake = os.environ.get("NE_FAKE_AD_PY")
    cmd = ([sys.executable, fake] if fake else [ad]) + [sub]
    if sub in APP_CMDS:
        if "--app" in rest or "--window-id" in rest:
            pass  # 尊重呼叫者給的 scope，但 --app 必須一致
            if "--app" in rest:
                given = rest[rest.index("--app") + 1]
                if given.lower() != app.lower():
                    refuse(app, sub, "--app 與宣告的 app 不一致")
            cmd += rest
        else:
            cmd += rest + ["--app", app]
    elif sub in REF_CMDS:
        refs = [m.group(1) for a in rest for m in [REF_RE.search(a)] if m]
        for sid in refs:
            ent = st.get(sid)
            owner = ent.get("app") if isinstance(ent, dict) else ent
            if isinstance(ent, dict) and ent.get("outbox"):
                refuse(app, sub, "這份 snapshot 含核准發件匣頁面：發件匣是 agent 禁區，"
                                 "核准只能由使用者本人按")
            if owner is None:
                refuse(app, sub, "ref 的 snapshot (%s) 不是由 ned 產生，無法證明屬於 %s——"
                                 "先用 ned %s snapshot 重拍" % (sid, app, app))
            if owner.lower() != app.lower():
                refuse(app, sub, "ref 屬於 %s，不屬於宣告的 %s：跨 app 操作被拒" % (owner, app))
        cmd += rest
    elif sub in PASS_CMDS:
        if sub == "launch":
            # 事故一：受測 agent 自己啟動了沒登入的 LINE。launch 的目標必須就是宣告的 app（不能借一般 app 的名義啟動別的）
            tgt = next((x for x in rest if not x.startswith("-")), "")
            base = re.split(r"[\\/]", tgt.strip("\"'"))[-1]
            if not base or (base.lower() != app.lower() and policy.canonical_app(base) != canon):
                refuse(app, sub, "launch 的目標（%s）必須等於宣告的 app（%s）" % (base or "未指定", app))
        if sub == "wait":
            refs = [m.group(1) for a in rest for m in [REF_RE.search(a)] if m]
            for sid in refs:
                ent = st.get(sid)
                owner = ent.get("app") if isinstance(ent, dict) else ent
                if owner is None or owner.lower() != app.lower():
                    refuse(app, sub, "wait 的 ref 不屬於宣告的 %s（或不是 ned 拍的）" % app)
            if any(x in rest for x in ("--text", "-w", "--wait-for", "--wait-for-gone", "--menu", "--menu-closed")) \
                    and "--app" not in rest:
                rest = rest + ["--app", app]
        cmd += rest
        if sub == "close-app" and (not rest or rest[0].lower() != app.lower()):
            refuse(app, sub, "close-app 目標必須等於宣告的 app")
    else:
        refuse(app, sub, "未知或未允許的子指令")

    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stdout
    # 記錄 snapshot 歸屬
    if sub in ("snapshot", "find"):
        for m in re.finditer(r'"snapshot_id":"(s[0-9a-z]+)"', out):
            st[m.group(1)] = {"app": app, "outbox": OUTBOX_MARK in out}
        save_state(st)
    ledger(app, sub, True)
    sys.stdout.write(out)
    if p.stderr:
        sys.stderr.write(p.stderr)
    sys.exit(p.returncode)

if __name__ == "__main__":
    main()
