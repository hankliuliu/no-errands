#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_guard — 桌面聊天 app 的機制級守門（純 stdlib，建在 agent-desktop 與政策檔之上）。

  chat_guard.py <app> locate --chat <精確名稱>
  chat_guard.py <app> open   --chat <精確名稱> [--expect <指紋>]
  chat_guard.py <app> read   --chat <精確名稱> | --file <匯出的 .txt>
  chat_guard.py <app> export --chat <精確名稱> --to <資料夾>   # 驗預設檔名與首行
  chat_guard.py <app> close
  chat_guard.py <app> act click|set-value --target <元素名稱> [--text <文字>]   # 只為驗證守門；幾乎都會被擋

<app>：目前支援 fake-chat（假聊天視窗測試靶）。line 的介面結構尚未實測，P0.6 才加。

不變量（不可調）：
  - 訊息輸入框、送出鈕、表情/貼圖/附件鈕、清單中非目標項目：永遠是禁區（assert_allowed）。
  - 另存新檔對話框：只允許對檔名欄 set-value、對「存檔」鈕 click，且必須確認是系統檔案對話框。
  - 只用語意操作（UIA press / set-value），不送鍵盤事件、不動滑鼠。
  - locate/open/export/close 的輸出不含任何 app 內容字串（對話名稱只回雜湊）；只有 read 回內容，
    且只回給呼叫者，不寫 log。
政策（policy.json）：app 必須在 granted_apps；白名單對話直接開；不在白名單且未讀保護 on →
  需要 --expect 指紋且狀態沒變，有未讀就拒開。
"""
import hashlib, json, os, re, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402
import idle  # noqa: E402
try:
    import popup_menu  # noqa: E402  (Windows)
except Exception:
    popup_menu = None

HOME = policy.HOME
LEDGER = os.path.join(HOME, "chat-guard-ledger.jsonl")

# 每個 app 的介面描述。名稱比對一律「完全相等」。
PROFILES = {
    "fake-chat": {
        "window_title": "NE-FAKECHAT",
        "list_container": "chat list",
        "item_role": "button",
        "chat_name": lambda packed: packed.split(" | ", 1)[0],
        "raises_foreground": True,   # 實測：語意點擊可能讓視窗跳到前景（視前景鎖而定）
        "has_unread": lambda packed: bool(re.search(r"\| \d+ unread \|", packed)),
        "unread_format_known": True,
        "messages": "messages",
        "header_name": lambda chat: "open chat: " + chat,    # 目前開啟的對話標頭（只比對、不輸出）
        "forbidden_names": {"message input", "Send", "Emoji"},
        "forbidden_roles_main": {"textfield"},          # 主視窗裡的任何文字框都不碰
        "menu_path": ["Chat", "Export chat"],
        "export_via": "popup",                 # 像 LINE：標頭的「More」按鈕 → 彈出選單（獨立頂層視窗）→ Save chat
        "menu_button": "More",
        "popup_export_items": ("儲存聊天", "Save chat"),
        "close_button": "Close chat",
        "dialog_title": "Save chat history",
        "dialog_filename_ids": {"1001", "1148"},          # 系統檔案對話框的檔名欄 automation id
        "dialog_save_ids": {"1"},                         # 「存檔」鈕
        "dialog_cancel_ids": {"2"},                       # 「取消」鈕
        "overwrite_confirm_titles": ("Confirm Save As", "確認另存新檔"),
        "confirm_no_ids": {"CommandButton_7", "7"},       # 覆寫確認的「否」（IDNO=7）
        # 身分驗證：app 產生的預設檔名與匯出檔第一行都要含目標對話名稱（只比對、不輸出）
        "verify_export_filename_contains_chat": True,
        "verify_export_first_line_contains_chat": True,
    },
    # LINE 桌面版（Windows、LINE 26.x、Qt）——結構實測 2026-11-21，見 playbook/apps/line-desktop.md
    "line": {
        "window_title": "LINE",
        "list_role": "listbox",                # 聊天清單 = 主視窗裡的 listbox，項目是 button
        "item_role": "button",
        "chat_name": lambda packed: packed.split(", ", 1)[0],
        "has_unread": lambda packed: bool(re.match(r"^[^,]+, \d+人, \d+, ", packed)),
        "unread_format_known": False,          # 非白名單對話一律不開
        "header_toolbar": True,                # 開著的對話：右側 7 顆按鈕的工具列，第一顆 = 「名稱, N人」
        "raises_foreground": True,
        "forbidden_names": {"開始通話", "關閉提醒", "傳送檔案", "Keep筆記", "截取畫面", "截取畫面選項", "貼圖",
                            "以個別視窗開啟", "記事本", "搜尋聊天", "建立聊天室", "聊天室排序", "收起聊天視窗"},
        "forbidden_roles_main": {"textfield"},
        "export_via": "popup",
        "menu_button": "其他功能",
        "popup_export_items": ("儲存聊天", "儲存聊天記錄", "Save chat", "Save chat history"),
        "dialog_titles": ("另存新檔", "Save As", "儲存", "Save"),
        "dialog_filename_ids": {"1001", "1148"},
        "dialog_save_ids": {"1"},
        "dialog_cancel_ids": {"2"},
        "overwrite_confirm_titles": ("確認另存新檔", "Confirm Save As"),
        "confirm_no_ids": {"CommandButton_7", "7"},
        "verify_export_filename_contains_chat": True,
        "verify_export_first_line_contains_chat": True,
        "close_button": None,                  # 收尾 = 交還（縮小視窗）
    },
}


def header_ok(prof, snap, chat):   # snap 必須是完整 snapshot（-i 會漏掉標籤類靜態文字）
    """目前開著的就是指名對話？（只比對、不輸出名稱）"""
    if prof.get("header_name"):
        return find_node(snap, lambda n: n.get("name") == prof["header_name"](chat))[0] is not None
    if prof.get("header_toolbar"):
        pat = re.compile(re.escape(chat) + r"(, \d+人)?$")
        for node, _ in walk(snap["tree"]):
            kids = [k for k in node.get("children") or [] if k.get("role") == "button"]
            if node.get("role") != "listbox" and len(kids) >= 6 and pat.match(kids[0].get("name") or ""):
                return True
        return False
    return True


class Refused(Exception):
    pass


# ---------------------------------------------------------------- 基礎

def ad_path():
    return os.path.join(HOME, "bin", "agent-desktop.exe" if os.name == "nt" else "agent-desktop")


def ad(*args, timeout=30):
    p = subprocess.run([ad_path()] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        d = json.loads(p.stdout)
    except Exception:
        raise RuntimeError("agent-desktop 輸出無法解析（rc=%d）" % p.returncode)
    if not d.get("ok"):
        err = d.get("error") or {}
        det = err.get("details") or {}
        raise RuntimeError("agent-desktop %s 失敗：%s%s" % (args[0], err.get("code") or err,
                           (" (%s)" % det.get("kind")) if det.get("kind") else ""))
    return d.get("data")


def h(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def ledger(app, sub, allowed, reason="", chat=None):
    os.makedirs(HOME, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "app": app, "sub": sub,
                            "allowed": allowed, "reason": reason,
                            "chat_sha256_16": h(chat) if chat else None}, ensure_ascii=False) + "\n")


def out(obj, code=0):
    # 語意操作也可能讓 app 自己把視窗拉到前景（UIA press 會觸發某些 app 的啟用；是否成功取決於
    # Windows 的前景鎖）。照實回報，讓 agent 告訴使用者。
    if FG_EVENTS:
        obj["foreground_taken"] = True
        obj["foreground_taken_by"] = FG_EVENTS[:5]
        # 交還：背景模式下把目標視窗縮小，前景會回到使用者原本的視窗（實測），再確認有沒有回去
        if HANDBACK.get("enabled") and WATCH.get("win") and FG0.get("h"):
            try:
                subprocess.run([ad_path(), "minimize", "--window-id", WATCH["win"]["id"]], capture_output=True, timeout=15)
                time.sleep(0.6)
                if _fg() != FG0["h"] and os.name == "nt":
                    # 系統把前景交給 z-order 下一個視窗、不是原本那個 → 把前景還給原本的視窗（是交還，不是搶）
                    import ctypes
                    ctypes.windll.user32.SetForegroundWindow(FG0["h"])
                    time.sleep(0.3)
                obj["foreground_returned"] = _fg() == FG0["h"]
            except Exception:
                obj["foreground_returned"] = False
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def walk(node, parents=()):
    yield node, parents
    for c in node.get("children") or []:
        yield from walk(c, parents + (node,))


# ---------------------------------------------------------------- 視窗與元素

def find_window(prof, title=None):
    wins = [w for w in ad("list-windows") if w.get("title") == (title or prof["window_title"])]
    if len(wins) != 1:
        raise Refused("找不到唯一的目標視窗（符合 %d 個）" % len(wins))
    WATCH["win"] = wins[0]
    if wins[0].get("minimized") and HANDBACK.get("may_restore"):
        # 上一個指令交還前景時把它縮小了；縮小的視窗無法語意操作 → 先還原（記下還原前的前景，之後交還到那裡）
        if FG0["h"] is None:
            FG0["h"] = _fg()
        ad("restore", "--window-id", wins[0]["id"])
        time.sleep(0.6)
        if _fg() != FG0["h"]:
            FG_EVENTS.append("restore")
    return wins[0]


def snapshot(win, interactive=True):
    args = ["snapshot", "--window-id", win["id"], "--compact"]
    if interactive:
        args.insert(3, "-i")
    return ad(*args)


def _is_list(prof, node):
    if prof.get("list_role"):
        return node.get("role") == prof["list_role"]
    return node.get("name") == prof.get("list_container")


def list_items(prof, snap):
    items = []
    for node, parents in walk(snap["tree"]):
        if node.get("role") == prof["item_role"] and parents and _is_list(prof, parents[-1]):
            items.append(node)
    return items


def match_chat(prof, snap, chat):
    hits = [n for n in list_items(prof, snap) if prof["chat_name"](n.get("name", "")) == chat]
    return hits


# ---------------------------------------------------------------- 守門

def assert_allowed(prof, action, node, parents, ctx):
    """每一次 click / set-value 之前呼叫。ctx: {"zone": "main"|"dialog", "target_chat": str|None,
    "allow": "list-target"|"menu"|"close"|"dialog"}。不合規就丟 Refused。"""
    name = node.get("name", "")
    role = node.get("role", "")
    if ctx["zone"] == "dialog":
        nid = str((node.get("native_id") or {}).get("value", ""))
        if action == "set-value" and role in ("textfield", "combobox") and nid in prof["dialog_filename_ids"]:
            return
        if action == "click" and role == "button" and nid in prof["dialog_save_ids"]:
            return
        if action == "click" and role == "button" and nid in prof["dialog_cancel_ids"]:
            return
        raise Refused("另存新檔對話框只允許：檔名欄 set-value、存檔鈕或取消鈕 click")
    # 主視窗：禁區優先
    if name in prof["forbidden_names"]:
        raise Refused("禁區元素（訊息輸入框/送出/表情等）：永遠不可操作")
    if role in prof["forbidden_roles_main"]:
        raise Refused("主視窗的文字輸入框一律不碰（不代發）")
    if action != "click":
        raise Refused("主視窗只允許語意點擊（不輸入任何文字）")
    in_list = any(_is_list(prof, p_) for p_ in parents)
    if in_list:
        if ctx.get("allow") == "list-target" and prof["chat_name"](name) == ctx.get("target_chat"):
            return
        raise Refused("清單中非目標項目：不可點")
    if ctx.get("allow") == "menu" and role == "menuitem" and name in prof["menu_path"]:
        return
    if ctx.get("allow") == "close" and role == "button" and name == prof.get("close_button"):
        return
    if ctx.get("allow") == "menu-button" and role == "button" and name == prof.get("menu_button"):
        return
    raise Refused("這個元素不在本步驟的允許清單內")


def find_node(snap, pred):
    for node, parents in walk(snap["tree"]):
        if pred(node):
            return node, parents
    return None, None


FG_EVENTS = []   # 每次操作後前景是否被換走（只記 handle 是否改變，不記標題）
FG0 = {"h": None}          # 第一次操作前的前景視窗（交還的目標）
HANDBACK = {"enabled": False}
WATCH = {"watch": None, "win": None}   # 閒置閘門：操作中監看使用者輸入


def idle_gate(prof, pol, a):
    """會搶前景的 app：只在使用者閒置時做（或使用者已同意）。回傳後開始監看。"""
    if not prof.get("raises_foreground") or pol.get("visibility") == "watch":
        return
    mode = pol.get("busy_behavior", "wait_idle")
    if mode == "ask":
        if "--user-ok" not in a:
            raise Refused("這個 app 操作時可能跳到前景：先問使用者現在方便嗎，同意後加 --user-ok")
    else:
        need = float(pol.get("idle_min_s", 180))
        s = idle.idle_seconds()
        if s is None:
            raise Refused("這個平台無法判斷使用者是否在用電腦：先問使用者現在方便嗎（或把政策 busy_behavior 設為 ask）")
        if s < need:
            if "--wait-idle" not in a:
                raise Refused("使用者正在用電腦（%d 秒前還有輸入）：等他離開（閒置滿 %d 秒）再做，可加 --wait-idle 讓工具等"
                              % (s, need))
            ok, info = idle.wait_idle(need, timeout=float(arg(a, "--wait-timeout", "3600")))
            if not ok:
                raise Refused("等不到使用者閒置（%s），沒有操作" % info)
    WATCH["watch"] = idle.IdleWatch()
    # 背景守衛：會搶前景的 app 跳到前景時，鍵盤焦點常落在訊息輸入框——使用者這時若在別處打字，
    # 字會進輸入框、按 Enter 就送出。每 100ms 檢查一次，使用者一有輸入就立刻把目標視窗縮小（不等下一步）。
    import threading

    def guard():
        while WATCH.get("watch") is not None and not WATCH.get("tripped"):
            if WATCH["watch"].interrupted(slack=0.3) and WATCH.get("win"):
                WATCH["tripped"] = True
                try:
                    subprocess.run([ad_path(), "minimize", "--window-id", WATCH["win"]["id"]],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass
                return
            time.sleep(0.1)
    threading.Thread(target=guard, daemon=True).start()


def check_interrupt():
    w = WATCH["watch"]
    if WATCH.get("tripped") or (w and w.interrupted()):
        if WATCH["win"]:
            try:
                ad("minimize", "--window-id", WATCH["win"]["id"])
            except Exception:
                pass
        raise Refused("使用者回來用電腦了：已中止並把視窗縮小交還，下次他離開再做")


def _fg():
    if os.name != "nt":
        return None
    try:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return None


def guarded(prof, action, node, parents, ctx, text=None):
    assert_allowed(prof, action, node, parents, ctx)
    before = _fg()
    if FG0["h"] is None:
        FG0["h"] = before
    try:
        if action == "click":
            ad("click", node["ref_id"])
        elif action == "set-value":
            ad("set-value", node["ref_id"], text)
    finally:
        after = _fg()
        if before is not None and after is not None and before != after:
            FG_EVENTS.append(action)
    check_interrupt()


# ---------------------------------------------------------------- 子指令

def cmd_locate(app, prof, chat):
    win = find_window(prof)
    hits = match_chat(prof, snapshot(win), chat)
    if len(hits) != 1:
        ledger(app, "locate", False, "matches=%d" % len(hits), chat)
        out({"ok": False, "found": False, "matches": len(hits),
             "error": "名稱要完全相符且唯一（符合 %d 個）" % len(hits)}, 3)
    ledger(app, "locate", True, "", chat)
    out({"ok": True, "found": True, "matches": 1, "fingerprint": h(hits[0].get("name", ""))})


def cmd_open(app, prof, chat, expect, pol):
    win = find_window(prof)
    snap = snapshot(win)
    hits = match_chat(prof, snap, chat)
    if len(hits) != 1:
        raise Refused("名稱要完全相符且唯一（符合 %d 個）" % len(hits))
    node = hits[0]
    packed = node.get("name", "")
    white = policy.in_whitelist(pol, chat)
    if not white and pol["chat"].get("unread_protection") == "on":
        if not prof["unread_format_known"]:
            raise Refused("這個 app 的未讀格式尚未掌握：非白名單對話一律不開")
        if not expect:
            raise Refused("非白名單對話需要 --expect <locate 回的指紋>")
        if h(packed) != expect:
            raise Refused("指紋不符：這個對話的狀態在 locate 之後變了（可能有新訊息），不開")
        if prof["has_unread"](packed):
            raise Refused("這個對話有未讀訊息，未讀保護開啟中，不開")
    _, parents = find_node(snap, lambda n: n is node)
    guarded(prof, "click", node, parents, {"zone": "main", "allow": "list-target", "target_chat": chat})
    time.sleep(0.8)
    if not header_ok(prof, snapshot(win, interactive=False), chat):
        raise Refused("開啟後確認不到開著的是指名對話（標頭不符或沒有開成）：停手，不再點任何東西")
    ledger(app, "open", True, "whitelist" if white else "fingerprint", chat)
    out({"ok": True, "opened": True, "via": "whitelist" if white else "fingerprint"})


def cmd_read(app, prof, chat, file_):
    if file_:
        with open(file_, encoding="utf-8-sig") as f:
            lines = [l.rstrip("\r\n") for l in f]
        ledger(app, "read-file", True)
        out({"ok": True, "source": "file", "lines": lines})
    if not chat:
        raise Refused("read 需要 --chat <名稱>（先確認目前開啟的就是這個對話）")
    win = find_window(prof)
    snap = snapshot(win, interactive=False)
    want = prof["header_name"](chat)
    hdr, _ = find_node(snap, lambda n: n.get("name") == want)
    if not hdr:
        raise Refused("目前開啟的對話不是指名的對話（標頭不符），不讀")
    box, _ = find_node(snap, lambda n: n.get("name") == prof["messages"])
    if not box:
        raise Refused("找不到訊息區")
    msgs = [c.get("name", "") for c in box.get("children") or []]
    ledger(app, "read", True)
    out({"ok": True, "source": "window", "messages": msgs})


def cmd_export(app, prof, dest, chat=None):
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    win = find_window(prof)
    snap = snapshot(win)
    if prof.get("export_via") == "popup":
        _export_menu_popup(prof, win, snap, chat)
    # 1) 選單 → 匯出（選單列版）
    for step in ([] if prof.get("export_via") == "popup" else prof["menu_path"]):
        node, parents = find_node(snap, lambda n, s=step: n.get("role") == "menuitem" and n.get("name") == s)
        if not node:
            raise Refused("找不到選單項目（第 %d 層）" % (prof["menu_path"].index(step) + 1))
        last = step == prof["menu_path"][-1]
        try:
            guarded(prof, "click", node, parents, {"zone": "main", "allow": "menu"})
        except RuntimeError as e:
            # 有些 app 在點擊處理函式裡直接開模態對話框，UIA press 要等對話框關掉才返回 → TIMEOUT。
            # 最後一步才容許，接著去找對話框；對話框若也不回應就放棄（絕不改用鍵盤）。
            if not (last and "TIMEOUT" in str(e)):
                raise
        time.sleep(0.4)
        if not last:
            snap = snapshot(win)
    # 2) 等系統另存新檔對話框
    titles = tuple(prof.get("dialog_titles") or ()) + ((prof["dialog_title"],) if prof.get("dialog_title") else ())
    dlg = None
    for _ in range(25):
        time.sleep(0.2)
        cands = [w for w in ad("list-windows") if w.get("title") in titles and w.get("pid") == win.get("pid")]
        if len(cands) == 1:
            dlg = cands[0]
            break
    if not dlg:
        raise Refused("另存新檔對話框沒有出現（或不唯一）")
    def _fn(roles):
        return find_node(dsnap, lambda n: n.get("role") in roles and str(
            (n.get("native_id") or {}).get("value", "")) in prof["dialog_filename_ids"]
            and "SetValue" in (n.get("available_actions") or []))
    # 對話框剛出現時控制項可能還沒建好（慢的機器上第一次 snapshot 只有外框）→ 等到檔名欄與存檔鈕都在
    fname = save = cancel = None
    for attempt in range(8):
        try:
            dsnap = snapshot(dlg)
        except RuntimeError as e:
            if "TIMEOUT" in str(e):
                raise Refused("另存新檔對話框不回應無障礙查詢（app 在點擊處理中開了模態對話框，UIA 被卡住）。"
                              "不改用鍵盤；請使用者自己按存檔或取消，這個 app 的匯出要另找路徑")
            raise
        # 先找內層的 Edit（textfield）：對外層 ComboBox set-value，有些 Windows 版本的對話框存檔時不採用
        # （CI 的 Windows Server 實測：欄位讀回是我們的路徑，卻用預設檔名存到預設資料夾）
        fname, fparents = _fn(("textfield",))
        if not fname:
            fname, fparents = _fn(("combobox",))
        save, sparents = find_node(dsnap, lambda n: n.get("role") == "button" and str(
            (n.get("native_id") or {}).get("value", "")) in prof["dialog_save_ids"])
        cancel, cparents = find_node(dsnap, lambda n: n.get("role") == "button" and str(
            (n.get("native_id") or {}).get("value", "")) in prof["dialog_cancel_ids"])
        if fname and save:
            break
        time.sleep(0.5)
    if not fname or not save:
        # 不要把對話框留著（會擋住 app 之後的所有操作）：有取消鈕就按取消
        if cancel:
            try:
                guarded(prof, "click", cancel, cparents, {"zone": "dialog"})
            except Exception:
                pass
        raise Refused("這不像系統檔案對話框（找不到檔名欄或存檔鈕），不操作" + ("（已按取消）" if cancel else "（對話框仍開著，請使用者關掉）"))

    def cancel_and_refuse(why):
        if cancel:
            try:
                guarded(prof, "click", cancel, cparents, {"zone": "dialog"})
            except Exception:
                pass
        raise Refused(why)

    # 身分驗證 1：app 帶進來的預設檔名要含目標對話名稱（在讀到任何內容之前）
    default_name = ""
    if prof.get("verify_export_filename_contains_chat"):
        try:
            default_name = (ad("get", fname["ref_id"], "--property", "value") or {}).get("value") or ""
        except RuntimeError:
            default_name = ""
        if not chat or chat not in default_name:
            cancel_and_refuse("預設檔名不含指名對話名稱（或讀不到）：開著的可能不是目標對話，已按取消、沒有存檔")
    target = os.path.join(dest, "chat-export-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
    # 對話框目前所在的資料夾（網址列「Address: C:\...」）：對話框若不採用我們寫的路徑、照預設檔名存，就是存到這裡
    dialog_folder = None
    for n, _ in walk(dsnap["tree"]):
        nm = n.get("name") or ""
        for pre in ("Address: ", "位址: ", "地址: ", "Address:", "位址:", "地址:"):
            if nm.startswith(pre) and os.path.isdir(nm[len(pre):].strip()):
                dialog_folder = nm[len(pre):].strip()
                break
        if dialog_folder:
            break
    guarded(prof, "set-value", fname, fparents, {"zone": "dialog"}, text=target)
    try:
        readback = (ad("get", fname["ref_id"], "--property", "value") or {}).get("value")
    except RuntimeError:
        readback = None
    if readback != target:
        cancel_and_refuse("檔名欄寫入後讀回不符，不存檔（已按取消）")
    t_save = time.time()
    guarded(prof, "click", save, sparents, {"zone": "dialog"})
    # 不覆寫：若跳出「確認另存新檔」（檔案已存在），按「否」並停手
    time.sleep(0.6)
    conf = [w for w in ad("list-windows") if w.get("pid") == win.get("pid")
            and w.get("title") in prof.get("overwrite_confirm_titles", ())]
    if not conf:
        # 有些版本的確認框長在另存新檔對話框的樹裡：對話框還開著、而且出現「否」鈕 → 同樣處理
        still = [w for w in ad("list-windows") if w.get("title") == prof["dialog_title"] and w.get("pid") == win.get("pid")]
        if still:
            try:
                if find_node(snapshot(still[0]), lambda n: n.get("role") == "button"
                             and n.get("name") in ("No", "&No", "否", "否(N)", "否(&N)"))[0]:
                    conf = still
            except RuntimeError:
                pass
    if conf:
        try:
            NO_NAMES = ("No", "&No", "否", "否(N)", "否(&N)")
            def is_no(n):
                return n.get("role") == "button" and (str((n.get("native_id") or {}).get("value", ""))
                                                      in prof.get("confirm_no_ids", set()) or n.get("name") in NO_NAMES)
            no_btn = None
            for w in conf + [x for x in ad("list-windows") if x.get("title") == prof["dialog_title"]
                             and x.get("pid") == win.get("pid")]:
                no_btn, _ = find_node(snapshot(w), is_no)
                if no_btn:
                    break
            if no_btn:
                ad("click", no_btn["ref_id"])
                time.sleep(0.4)
            d2 = [w for w in ad("list-windows") if w.get("title") == prof["dialog_title"] and w.get("pid") == win.get("pid")]
            if d2:
                s2 = snapshot(d2[0])
                c2, _ = find_node(s2, lambda n: n.get("role") == "button" and str(
                    (n.get("native_id") or {}).get("value", "")) in prof["dialog_cancel_ids"])
                if c2:
                    ad("click", c2["ref_id"])
        except Exception:
            pass
        raise Refused("目標檔已存在而跳出覆寫確認：按了「否」與取消，沒有覆寫任何檔案")
    for _ in range(50):
        time.sleep(0.2)
        if os.path.exists(target) and os.path.getsize(target) > 0:
            break
    moved_from = None
    if not os.path.exists(target) and prof.get("verify_export_filename_contains_chat") and default_name:
        # 有些 Windows 版本的對話框（CI 的 Windows Server 實測）不採用無障礙寫入的檔名，照預設檔名存到預設資料夾。
        # 預設檔名已驗證含對話名稱 → 到常見資料夾找「剛剛建立、檔名等於預設檔名」的檔案，搬到目標位置。
        base = os.path.basename(default_name.replace("\\", "/"))
        stem = os.path.splitext(base)[0]
        home_dir = os.path.expanduser("~")
        for d in ([dialog_folder] if dialog_folder else []) + [dest, os.path.join(home_dir, "Documents"), os.path.join(home_dir, "Downloads"),
                  os.path.join(home_dir, "Desktop"), os.path.join(home_dir, "OneDrive", "Documents")]:
            try:
                for fn in os.listdir(d):
                    fp = os.path.join(d, fn)
                    if os.path.splitext(fn)[0] == stem and os.path.getmtime(fp) >= t_save - 2:
                        shutil.move(fp, target)
                        moved_from = "dialog-folder" if d == dialog_folder else "common-folder"
                        break
            except OSError:
                continue
            if moved_from:
                break
    if not os.path.exists(target):
        # 診斷（只含系統對話框的 UI 字串）：對話框還在嗎？上面有哪些按鈕？
        diag = {"dialog_still_open": False, "dialog_folder_known": bool(dialog_folder),
                "default_name_in_dialog_folder": bool(dialog_folder and default_name and os.path.exists(
                    os.path.join(dialog_folder, os.path.basename(default_name.replace("\\", "/")))))}
        try:
            still = [w for w in ad("list-windows") if w.get("title") == prof["dialog_title"]
                     and w.get("pid") == win.get("pid")]
            diag["dialog_still_open"] = bool(still)
            other = [w.get("title") for w in ad("list-windows") if w.get("pid") == win.get("pid")
                     and w.get("title") not in (prof["dialog_title"], prof["window_title"])]
            diag["other_windows_same_pid"] = other[:3]
            if still:
                s2 = snapshot(still[0])
                diag["dialog_buttons"] = [n.get("name") for n, _ in walk(s2["tree"])
                                          if n.get("role") == "button" and n.get("name")][:8]
                fv = find_node(s2, lambda n: n.get("role") in ("textfield", "combobox") and str(
                    (n.get("native_id") or {}).get("value", "")) in prof["dialog_filename_ids"])[0]
                if fv:
                    diag["filename_field_equals_target"] = (ad("get", fv["ref_id"], "--property", "value") or {}).get("value") == target
        except Exception as e:
            diag["diag_error"] = str(e)[:120]
        raise Refused("按下存檔後沒有看到檔案；診斷：%s" % json.dumps(diag, ensure_ascii=False))
    # 身分驗證 2：只讀第一行比對；不符就刪檔、不再讀任何一行
    if prof.get("verify_export_first_line_contains_chat"):
        with open(target, encoding="utf-8-sig") as f:
            first = f.readline()
        if chat not in first:
            os.remove(target)
            raise Refused("匯出檔第一行不含指名對話名稱：已刪除檔案（沒有讀其他內容）")
    with open(target, encoding="utf-8-sig") as f:
        n = sum(1 for _ in f)
    ledger(app, "export", True, chat=chat)
    out({"ok": True, "path": target, "bytes": os.path.getsize(target), "lines": n,
         "verified": {"default_filename": True, "first_line": True},
         "saved_via": ("default-name-in-%s-then-moved" % moved_from) if moved_from else "typed-path",
         "dialog_folder_known": bool(dialog_folder)})


def _export_menu_popup(prof, win, snap, chat):
    """標頭的選單按鈕 → 彈出選單（獨立頂層視窗）→ 只點名稱完全等於預期項目的那一項（投遞訊息，不動游標）。"""
    if popup_menu is None or os.name != "nt":
        raise Refused("彈出選單路徑目前只支援 Windows")
    if not header_ok(prof, snapshot(win, interactive=False), chat):
        raise Refused("目前開著的不是指名對話：先 open --chat 並確認標頭")
    btn, bparents = find_node(snap, lambda n: n.get("role") == "button" and n.get("name") == prof["menu_button"])
    if not btn:
        raise Refused("找不到選單按鈕")
    pid = win.get("pid")
    before = popup_menu.visible_hwnds(pid)
    guarded(prof, "click", btn, bparents, {"zone": "main", "allow": "menu-button"})
    pops = []
    for _ in range(15):
        time.sleep(0.2)
        pops = popup_menu.popup_windows(pid, exclude=before)
        if pops:
            break
    if not pops:
        raise Refused("點了選單按鈕，但沒有出現彈出選單")
    # 新出現的頂層視窗可能不只選單本身（陰影、提示框…）→ 逐一讀，取真的有項目的那個；項目可能還沒建好 → 重試
    pop, items = None, []
    for _ in range(4):
        for cand in pops:
            res = popup_menu.popup_items(cand["hwnd"])
            its = [i for i in res.get("items", []) if i.get("name")]
            if its:
                pop, items = cand, its
                break
        if pop:
            break
        time.sleep(0.4)
        pops = popup_menu.popup_windows(pid, exclude=before) or pops
    pop = pop or pops[0]
    def _item_like(i):
        # 選單項目的 UIA 型別不一定是 MenuItem：WinForms 平面按鈕經 FromHandle 讀成 Pane（CI 實測）；自繪選單也常是 Pane。
        # Pane 只在「項目大小」時才算（避免名稱剛好相同的整塊容器）。
        if i.get("type") in ("MenuItem", "ListItem", "Button"):
            return True
        r = i.get("rect") or [0, 0, 0, 0]
        return i.get("type") == "Pane" and 0 < r[3] - r[1] <= 60 and 0 < r[2] - r[0] <= 600

    def _center_in(a, b):   # a 的中心落在 b 裡
        ra, rb = a.get("rect") or [0, 0, 0, 0], b.get("rect") or [0, 0, 0, 0]
        cx, cy = (ra[0] + ra[2]) / 2, (ra[1] + ra[3]) / 2
        return rb[0] <= cx <= rb[2] and rb[1] <= cy <= rb[3]
    want = []
    for i in items:   # 同一個項目可能被 UIA 以兩層元素露出（Win32 代理＋MSAA，位置可能差幾像素）→ 中心互相包含就算同一個
        if i["name"] in prof["popup_export_items"] and _item_like(i):
            if not any(i["name"] == w["name"] and (_center_in(i, w) or _center_in(w, i)) for w in want):
                want.append(i)
    if len(want) != 1:
        popup_menu.close_popup(pop["hwnd"])
        diag = [{"class": c.get("class"), "w": c["rect"][2] - c["rect"][0], "h": c["rect"][3] - c["rect"][1]} for c in pops][:5]
        hits = [{"name": i["name"], "type": i.get("type"), "rect": i.get("rect")} for i in items
                if i["name"] in prof["popup_export_items"]][:6]
        raise Refused("選單裡沒有唯一的匯出項目；選單項目：%s；名稱相符者：%s；彈出視窗候選：%s"
                      % ([i["name"] for i in items][:15], hits, diag))
    if want[0].get("enabled") is False:
        popup_menu.close_popup(pop["hwnd"])
        raise Refused("匯出項目目前不可用")
    before_fg = _fg()
    popup_menu.click_item(pop["hwnd"], want[0])
    if before_fg is not None and _fg() != before_fg:
        FG_EVENTS.append("popup-click")


def cmd_close(app, prof):
    win = find_window(prof)
    snap = snapshot(win)
    if not prof.get("close_button"):
        ad("minimize", "--window-id", win["id"])
        ledger(app, "close", True, "minimized")
        out({"ok": True, "closed": "minimized"})
    node, parents = find_node(snap, lambda n: n.get("role") == "button" and n.get("name") == prof["close_button"])
    if not node:
        raise Refused("找不到離開聊天室的按鈕")
    guarded(prof, "click", node, parents, {"zone": "main", "allow": "close"})
    ledger(app, "close", True)
    out({"ok": True, "closed": True})


def cmd_act(app, prof, action, target, text):
    """測試守門用：對任意名稱的元素嘗試 click / set-value，必定先過 assert_allowed。"""
    win = find_window(prof)
    snap = snapshot(win)
    node, parents = find_node(snap, lambda n: n.get("name") == target or
                              prof["chat_name"](n.get("name", "")) == target and n.get("role") == prof["item_role"])
    if not node:
        raise Refused("找不到這個元素")
    guarded(prof, action, node, parents, {"zone": "main", "allow": None}, text=text)
    out({"ok": True})


def arg(a, name, default=None):
    if name in a:
        i = a.index(name)
        return a[i + 1] if i + 1 < len(a) else default
    return default


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    app, sub = policy.canonical_app(a[0]), a[1]
    prof = PROFILES.get(app)
    try:
        if not prof:
            raise Refused("chat_guard 尚未支援這個 app（目前：fake-chat、line）")
        pol = policy.load(HOME)
        if not policy.is_granted(pol, app):
            raise Refused("使用者尚未授權這個 app（同意閘門）：先在對話中取得指名同意")
        if sub in ("open", "export", "close", "act") or (sub == "read" and not arg(a, "--file")):
            idle_gate(prof, pol, a)
            HANDBACK["may_restore"] = True   # 只有過了閒置閘門的指令可以把縮小的視窗還原
        HANDBACK["enabled"] = pol.get("visibility") != "watch" and bool(prof.get("raises_foreground"))
        if sub == "locate":
            cmd_locate(app, prof, arg(a, "--chat") or "")
        elif sub == "open":
            cmd_open(app, prof, arg(a, "--chat") or "", arg(a, "--expect"), pol)
        elif sub == "read":
            cmd_read(app, prof, arg(a, "--chat"), arg(a, "--file"))
        elif sub == "export":
            cmd_export(app, prof, arg(a, "--to") or ".", arg(a, "--chat"))
        elif sub == "close":
            cmd_close(app, prof)
        elif sub == "act" and len(a) >= 3 and a[2] in ("click", "set-value"):
            cmd_act(app, prof, a[2], arg(a, "--target") or "", arg(a, "--text", ""))
        else:
            raise Refused("沒有這個子指令（chat_guard 沒有送出訊息的能力；要回覆請用 outbox.py draft）")
    except Refused as e:
        ledger(app, sub, False, str(e), arg(a, "--chat"))
        out({"ok": False, "refused": True, "error": str(e)}, 3)
    except RuntimeError as e:
        ledger(app, sub, False, "error: " + str(e)[:200], arg(a, "--chat"))
        out({"ok": False, "error": str(e)}, 1)


if __name__ == "__main__":
    main()
