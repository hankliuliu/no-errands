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
import hashlib, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402

HOME = policy.HOME
LEDGER = os.path.join(HOME, "chat-guard-ledger.jsonl")

# 每個 app 的介面描述。名稱比對一律「完全相等」。
PROFILES = {
    "fake-chat": {
        "window_title": "NE-FAKECHAT",
        "list_container": "chat list",
        "item_role": "button",
        "chat_name": lambda packed: packed.split(" | ", 1)[0],
        "has_unread": lambda packed: bool(re.search(r"\| \d+ unread \|", packed)),
        "unread_format_known": True,
        "messages": "messages",
        "header_name": lambda chat: "open chat: " + chat,    # 目前開啟的對話標頭（只比對、不輸出）
        "forbidden_names": {"message input", "Send", "Emoji"},
        "forbidden_roles_main": {"textfield"},          # 主視窗裡的任何文字框都不碰
        "menu_path": ["Chat", "Export chat"],
        "close_button": "Close chat",
        "dialog_title": "Save chat history",
        "dialog_filename_ids": {"1001", "1148"},          # 系統檔案對話框的檔名欄 automation id
        "dialog_save_ids": {"1"},                         # 「存檔」鈕
        "dialog_cancel_ids": {"2"},                       # 「取消」鈕
        # 身分驗證：app 產生的預設檔名與匯出檔第一行都要含目標對話名稱（只比對、不輸出）
        "verify_export_filename_contains_chat": True,
        "verify_export_first_line_contains_chat": True,
    },
}


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
        raise RuntimeError("agent-desktop %s 失敗：%s" % (args[0], err.get("code") or err))
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
    return wins[0]


def snapshot(win, interactive=True):
    args = ["snapshot", "--window-id", win["id"], "--compact"]
    if interactive:
        args.insert(3, "-i")
    return ad(*args)


def list_items(prof, snap):
    items = []
    for node, parents in walk(snap["tree"]):
        if node.get("role") == prof["item_role"] and any(
                p.get("name") == prof["list_container"] for p in parents):
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
    in_list = any(p.get("name") == prof["list_container"] for p in parents)
    if in_list:
        if ctx.get("allow") == "list-target" and prof["chat_name"](name) == ctx.get("target_chat"):
            return
        raise Refused("清單中非目標項目：不可點")
    if ctx.get("allow") == "menu" and role == "menuitem" and name in prof["menu_path"]:
        return
    if ctx.get("allow") == "close" and role == "button" and name == prof["close_button"]:
        return
    raise Refused("這個元素不在本步驟的允許清單內")


def find_node(snap, pred):
    for node, parents in walk(snap["tree"]):
        if pred(node):
            return node, parents
    return None, None


def guarded(prof, action, node, parents, ctx, text=None):
    assert_allowed(prof, action, node, parents, ctx)
    if action == "click":
        ad("click", node["ref_id"])
    elif action == "set-value":
        ad("set-value", node["ref_id"], text)


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
    # 1) 選單 → 匯出
    for step in prof["menu_path"]:
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
    dlg = None
    for _ in range(25):
        time.sleep(0.2)
        cands = [w for w in ad("list-windows") if w.get("title") == prof["dialog_title"]
                 and w.get("pid") == win.get("pid")]
        if len(cands) == 1:
            dlg = cands[0]
            break
    if not dlg:
        raise Refused("另存新檔對話框沒有出現（或不唯一）")
    try:
        dsnap = snapshot(dlg)
    except RuntimeError as e:
        if "TIMEOUT" in str(e):
            raise Refused("另存新檔對話框不回應無障礙查詢（app 在點擊處理中開了模態對話框，UIA 被卡住）。"
                          "不改用鍵盤；請使用者自己按存檔或取消，這個 app 的匯出要另找路徑")
        raise
    fname, fparents = find_node(dsnap, lambda n: n.get("role") in ("textfield", "combobox") and str(
        (n.get("native_id") or {}).get("value", "")) in prof["dialog_filename_ids"] and "SetValue" in (n.get("available_actions") or []))
    save, sparents = find_node(dsnap, lambda n: n.get("role") == "button" and str(
        (n.get("native_id") or {}).get("value", "")) in prof["dialog_save_ids"])
    if not fname or not save:
        raise Refused("這不像系統檔案對話框（找不到檔名欄或存檔鈕），不操作")
    cancel, cparents = find_node(dsnap, lambda n: n.get("role") == "button" and str(
        (n.get("native_id") or {}).get("value", "")) in prof["dialog_cancel_ids"])

    def cancel_and_refuse(why):
        if cancel:
            try:
                guarded(prof, "click", cancel, cparents, {"zone": "dialog"})
            except Exception:
                pass
        raise Refused(why)

    # 身分驗證 1：app 帶進來的預設檔名要含目標對話名稱（在讀到任何內容之前）
    if prof.get("verify_export_filename_contains_chat"):
        try:
            default_name = (ad("get", fname["ref_id"], "--property", "value") or {}).get("value") or ""
        except RuntimeError:
            default_name = ""
        if not chat or chat not in default_name:
            cancel_and_refuse("預設檔名不含指名對話名稱（或讀不到）：開著的可能不是目標對話，已按取消、沒有存檔")
    target = os.path.join(dest, "chat-export-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
    guarded(prof, "set-value", fname, fparents, {"zone": "dialog"}, text=target)
    guarded(prof, "click", save, sparents, {"zone": "dialog"})
    for _ in range(50):
        time.sleep(0.2)
        if os.path.exists(target) and os.path.getsize(target) > 0:
            break
    if not os.path.exists(target):
        # 診斷（只含系統對話框的 UI 字串）：對話框還在嗎？上面有哪些按鈕？
        diag = {"dialog_still_open": False}
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
         "verified": {"default_filename": True, "first_line": True}})


def cmd_close(app, prof):
    win = find_window(prof)
    snap = snapshot(win)
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
            raise Refused("chat_guard 尚未支援這個 app（目前只有 fake-chat）")
        pol = policy.load(HOME)
        if not policy.is_granted(pol, app):
            raise Refused("使用者尚未授權這個 app（同意閘門）：先在對話中取得指名同意")
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
