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
import nowin  # noqa: E402,F401  Windows：子程序不開主控台視窗（使用者回報視窗一直彈出）

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
        "header_prefix": "open chat: ",        # 目前開啟的對話標頭「open chat: 名稱 (N)」（只比對、不輸出）
        "view_buttons": {"notes": "記事本"},     # 標頭工具列（第一顆＝對話名稱）裡的記事本：開一個獨立視窗 NE-NOTES
        "forbidden_names": {"message input", "Send", "Emoji", "記事本"},
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
        # 真機實測（LINE 26.x）：匯出檔沒有標頭、檔案裡完全不含對話名稱——第一行是日期「YYYY.MM.DD 星期X」，
        # 之後是「HH:MM 發言者 內容」。所以存檔後的身分檢查改成：不同發言者數 ≤ 標頭寫的人數（只數、不輸出）。
        "verify_export_first_line_contains_chat": False,
        "verify_export_senders_within_members": {"line_re": r"^\d\d:\d\d ", "sender_field": 1},
        "close_button": None,                  # 收尾 = 交還（縮小視窗）
        # Sprint E（只讀探索）：標頭工具列裡可以點開來「看」的按鈕；仍在 forbidden_names 裡，只有 allow=view-button 時放行
        "view_buttons": {"notes": "記事本"},
    },
}


# ---------------------------------------------------------------- 名稱比對（只看名稱，不看人數）
# 群組的顯示名稱結尾會帶人數：清單「名稱 (21)」、標頭「名稱, 21人」。人數會變，名稱不會。
# 比對：顯示名稱去掉結尾「一組」人數標示後等於指名名稱（或完全相等）＝強符合；兩邊都去掉人數後相等＝弱符合
# （使用者把帶人數的顯示名稱整串存進白名單），只在沒有強符合時用。人數不存、不拿來比對名稱。
strip_count = policy.strip_count


def name_level(display, wanted):
    """2 = 強符合、1 = 弱符合、0 = 不符。"""
    if not display or not wanted:
        return 0
    if display == wanted or strip_count(display) == wanted:
        return 2
    if strip_count(display) == strip_count(wanted):
        return 1
    return 0


def _header_display(prof, snap):
    """開著的對話標頭工具列第一顆的名稱（「名稱, N人」或「名稱 (N)」）；沒有標頭回 None。只比對、不輸出。"""
    for node, _ in walk(snap["tree"]):
        kids = [k for k in node.get("children") or [] if k.get("role") == "button"]
        if node.get("role") != "listbox" and len(kids) >= 6 and kids[0].get("name"):
            return kids[0]["name"]
    return None


def is_header_toolbar(node, chat):
    """開著的對話標頭工具列：非 listbox、≥6 顆按鈕、第一顆的名稱（去掉人數）和指名對話相符。"""
    kids = [k for k in (node or {}).get("children") or [] if k.get("role") == "button"]
    return bool(node) and node.get("role") != "listbox" and len(kids) >= 6 and bool(chat) \
        and name_level(kids[0].get("name") or "", chat) > 0


def header_toolbar(snap, chat):
    for node, _ in walk(snap["tree"]):
        if is_header_toolbar(node, chat):
            return node
    return None


def header_ok(prof, snap, chat):   # snap 必須是完整 snapshot（-i 會漏掉標籤類靜態文字）
    """目前開著的就是指名對話？（只比對、不輸出名稱）"""
    if prof.get("header_prefix"):
        pre = prof["header_prefix"]
        return find_node(snap, lambda n: (n.get("name") or "").startswith(pre)
                         and name_level(n["name"][len(pre):], chat) > 0)[0] is not None
    if prof.get("header_toolbar"):
        for node, _ in walk(snap["tree"]):
            kids = [k for k in node.get("children") or [] if k.get("role") == "button"]
            if node.get("role") != "listbox" and len(kids) >= 6 and name_level(kids[0].get("name") or "", chat):
                return True
        return False
    return True


_MEMBERS_RE = re.compile(r"(?:, ?(\d+)人|\s+\((\d+)\)|\s*（(\d+)）)$")


def header_members(prof, snap, chat):
    """當下標頭上的人數（「名稱, N人」或「名稱 (N)」）；一對一聊天沒有人數 → 2；找不到標頭回 None。
    「名稱本身以括號數字結尾」（某某(2024)）且標頭沒有另外的人數時，不會把 2024 當人數。"""
    if not prof.get("header_toolbar"):
        return None
    for node, _ in walk(snap["tree"]):
        kids = [k for k in node.get("children") or [] if k.get("role") == "button"]
        if node.get("role") != "listbox" and len(kids) >= 6:
            disp = kids[0].get("name") or ""
            if not name_level(disp, chat):
                continue
            m = _MEMBERS_RE.search(disp)
            if m and disp != chat:
                return int(m.group(1) or m.group(2) or m.group(3))
            return 2
    return None


def file_key(s):
    """預設檔名比對用：去掉檔名不能有、app 會替換掉的字元（/ \\ : * ? " < > |）與常見替代字元、空白。"""
    return re.sub(r'[\\/:*?"<>|_\-\s／＼：＊？＂＜＞｜]', "", s or "").lower()


EXPORT_CTX = {}
OPEN_CTX = {}


def _safe_remove(path, tries=20):
    """刪檔；app 可能還開著檔案（Windows 會 PermissionError）→ 重試約 10 秒。回傳是否刪掉。"""
    for _ in range(tries):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.5)
    return False


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


class Ambiguous(Exception):
    pass


def match_chat(prof, snap, chat):
    """回傳符合的清單項目。強符合優先，沒有才用弱符合。符合的那個去掉人數後若和別的對話同名 → Ambiguous（不猜）。"""
    items = list_items(prof, snap)
    lv = [(name_level(prof["chat_name"](n.get("name", "")), chat), n) for n in items]
    best = max([l for l, _ in lv] or [0])
    hits = [n for l, n in lv if best and l == best]
    disp = {prof["chat_name"](n.get("name", "")) for n in hits}
    if len(hits) > 1 and len(disp) > 1 and len({strip_count(d) for d in disp}) == 1:
        raise Ambiguous("去掉人數標示後有 %d 個同名對話：不猜是哪一個（人數會變，不能拿來分辨；請使用者把其中一個改名）" % len(hits))
    if len(hits) == 1:
        base = strip_count(prof["chat_name"](hits[0].get("name", "")))
        same = [n for n in items if strip_count(prof["chat_name"](n.get("name", ""))) == base]
        if len(same) > 1:
            raise Ambiguous("去掉人數標示後有 %d 個同名對話：不猜是哪一個（人數會變，不能拿來分辨；請使用者把其中一個改名）" % len(same))
    return hits


# ---------------------------------------------------------------- 守門

def assert_allowed(prof, action, node, parents, ctx):
    """每一次 click / set-value 之前呼叫。ctx: {"zone": "main"|"dialog", "target_chat": str|None,
    "allow": "list-target"|"menu"|"close"|"dialog"}。不合規就丟 Refused。"""
    name = node.get("name", "")
    role = node.get("role", "")
    if ctx.get("allow") == "view-button":
        # 只讀檢視（例如記事本）：按鈕的直接父節點必須就是「指名對話」的標頭工具列（第一顆名稱相符），和 list-target 一樣再比對一次
        if action == "click" and role == "button" and name in (prof.get("view_buttons") or {}).values() \
                and parents and is_header_toolbar(parents[-1], ctx.get("target_chat") or ""):
            return
        raise Refused("檢視按鈕只能是開著的指名對話標頭工具列裡的那顆")
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
    if action == "scroll":
        # 往上載入歷史（gather 的範圍實驗與補齊用）：只能捲目前對話的訊息區；聊天清單（含其他對話的預覽）不行
        if ctx.get("allow") == "history-scroll" and role in ("list", "listbox") and not _is_chat_list(node) \
                and not any(_is_chat_list(p_) for p_ in parents):
            return
        raise Refused("捲動只允許在目前對話的訊息區（往上載入歷史），不可捲聊天清單")
    if action == "scroll-to":
        # 把「目標對話自己的清單項目」捲進可見範圍（UIA ScrollIntoView，不動游標）：LINE 依最新訊息排序，
        # 目標可能被擠到清單下方看不到的地方，這時語意點擊會 ACTION_FAILED（真機 2026-11-22）
        if ctx.get("allow") == "list-target" and any(_is_list(prof, p_) for p_ in parents)                 and prof["chat_name"](name) == ctx.get("target_chat"):
            return
        raise Refused("只能把目標對話的清單項目捲進畫面")
    if action != "click":
        raise Refused("主視窗只允許語意點擊（不輸入任何文字）")
    in_list = any(_is_list(prof, p_) for p_ in parents)
    if in_list:
        if ctx.get("allow") == "list-target" and prof["chat_name"](name) == ctx.get("target_chat"):
            return
        raise Refused("清單中非目標項目：不可點")
    if ctx.get("allow") == "menu" and role == "menuitem" and name in prof.get("menu_path", ()):
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
    elif "--user-ok" in a:
        pass   # 使用者當下同意現在做（在電腦前、同意視窗跳出來）：不等閒置；下面的輸入監看照樣開著
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
        elif action == "scroll":
            ad("scroll", node["ref_id"], "--direction", "up", "--amount", str(text or 25))
        elif action == "scroll-to":
            ad("scroll-to", node["ref_id"])
    finally:
        after = _fg()
        if before is not None and after is not None and before != after:
            FG_EVENTS.append(action)
    check_interrupt()


# ---------------------------------------------------------------- 子指令

def cmd_locate(app, prof, chat):
    win = find_window(prof)
    try:
        hits = match_chat(prof, snapshot(win), chat)
    except Ambiguous as e:
        raise Refused(str(e))
    if len(hits) != 1:
        ledger(app, "locate", False, "matches=%d" % len(hits), chat)
        out({"ok": False, "found": False, "matches": len(hits),
             "error": "名稱（不含人數標示）要相符且唯一（符合 %d 個）" % len(hits)}, 3)
    ledger(app, "locate", True, "", chat)
    out({"ok": True, "found": True, "matches": 1, "fingerprint": h(hits[0].get("name", ""))})


def cmd_open(app, prof, chat, expect, pol):
    win = find_window(prof)
    snap = snapshot(win)
    try:
        hits = match_chat(prof, snap, chat)
    except Ambiguous as e:
        raise Refused(str(e))
    if len(hits) != 1:
        raise Refused("名稱（不含人數標示）要相符且唯一（符合 %d 個）" % len(hits))
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
    ctx = {"zone": "main", "allow": "list-target", "target_chat": prof["chat_name"](packed)}
    try:
        guarded(prof, "click", node, parents, ctx)
    except RuntimeError as e:
        if "ACTION_FAILED" not in str(e):
            raise
        # 項目可能在清單看不到的地方：捲進畫面、重新取樹、再點一次（只一次）
        guarded(prof, "scroll-to", node, parents, ctx)
        time.sleep(0.6)
        snap = snapshot(win)
        hits = match_chat(prof, snap, chat)
        if len(hits) != 1 or prof["chat_name"](hits[0].get("name", "")) != ctx["target_chat"]:
            raise Refused("捲動後對不到同一個對話（符合 %d 個），停手" % len(hits))
        node = hits[0]
        _, parents = find_node(snap, lambda n: n is node)
        guarded(prof, "click", node, parents, ctx)
        OPEN_CTX["scrolled_into_view"] = True
    time.sleep(0.8)
    if not header_ok(prof, snapshot(win, interactive=False), chat):
        raise Refused("開啟後確認不到開著的是指名對話（標頭不符或沒有開成）：停手，不再點任何東西")
    ledger(app, "open", True, ("whitelist" if white else "fingerprint") + (" scrolled" if OPEN_CTX.get("scrolled_into_view") else ""), chat)
    out({"ok": True, "opened": True, "via": "whitelist" if white else "fingerprint", "scrolled_into_view": bool(OPEN_CTX.get("scrolled_into_view"))})


def cmd_read(app, prof, chat, file_):
    if file_:
        with open(file_, encoding="utf-8-sig") as f:
            lines = [l.rstrip("\r\n") for l in f]
        ledger(app, "read-file", True)
        out({"ok": True, "source": "file", "lines": lines})
    if not chat:
        raise Refused("read 需要 --chat <名稱>（先確認目前開啟的就是這個對話）")
    if not prof.get("messages"):
        # LINE 的訊息區在 UIA 裡沒有可靠的容器名稱 → 讀取一律走匯出檔
        raise Refused("這個 app 不從畫面讀訊息：先 export --chat <名稱> --to <資料夾>，再 read --file <匯出檔>")
    win = find_window(prof)
    snap = snapshot(win, interactive=False)
    if not header_ok(prof, snap, chat):
        raise Refused("目前開啟的對話不是指名的對話（標頭不符），不讀")
    box, _ = find_node(snap, lambda n: n.get("name") == prof["messages"])
    if not box:
        raise Refused("找不到訊息區")
    msgs = [c.get("name", "") for c in box.get("children") or []]
    ledger(app, "read", True)
    out({"ok": True, "source": "window", "messages": msgs})


# 探索輸出只留介面用語；其他名稱一律換成字數（聊天內容、人名、對話名稱都不會出現在輸出裡）
UI_WORDS = re.compile(r"^(記事本|公告|檔案|照片|影片|照片・影片|相簿|連結|全部|建立|貼文|留言|分享|編輯|刪除|下載|儲存|另存新檔|另存|"
                      r"搜尋|設定|關閉|返回|上一頁|更多|其他功能|讚|回覆|置頂|取消|確定|新增|排序|篩選|顯示更多|查看更多|開啟|"
                      r"資料夾|傳送|儲存聊天|儲存聊天記錄|收起|展開|全部顯示|Keep|Notes?|Announcements?|Files?|Photos?|Videos?|"
                      r"Albums?|Links?|All|Create|Post|Comment|Share|Edit|Delete|Download|Save( as)?|Search|Close|Back|More|"
                      r"Like|Reply|Pin|Open|Cancel|OK)$")


_SHAPE_PUNCT = set(".-/:,()[]~・|" + "．－／：，（）［］～｜")


def _shape_of(name):
    """名稱的「形狀」：數字→9、中日韓字→字、拉丁字母→a、空白→_，標點保留；只看得出格式（例如 9999.99.99 是日期），看不到內容。
    只用在 12 字以內的短名稱；更長的一律只給字數。"""
    out = []
    for ch in name:
        if ch.isdigit():
            out.append("9")
        elif "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ" or "㐀" <= ch <= "䶿" or "𠀀" <= ch <= "𯿿":
            out.append("字")
        elif ch.isalpha():
            out.append("a")
        elif ch.isspace():
            out.append("_")
        elif ch in _SHAPE_PUNCT:
            out.append(ch)
        else:
            out.append("?")   # 其他一律佔位：表情符號、符號、擴充區漢字……原字都不能出現（顯示名稱裡的表情符號就是內容）
    return "".join(out)


def _mask(name):
    if UI_WORDS.match(name or ""):
        return name
    if not name:
        return 0
    return {"len": len(name), "shape": _shape_of(name)} if len(name) <= 12 else len(name)


def _shape(node, depth=0, maxd=14):
    d = {"r": node.get("role"), "n": _mask(node.get("name") or "")}
    if node.get("available_actions"):
        d["a"] = [x for x in node["available_actions"] if x in ("Click", "Invoke", "SetValue", "Scroll", "Toggle", "Select")]
    kids = [_shape(c, depth + 1) for c in (node.get("children") or [])] if depth < maxd else []
    if kids:
        d["c"] = kids
    return d


# 真機（2026-11-22）：LINE 開出來的視窗標題一律是「LINE」——不能靠標題認。改看視窗內容：預期的那個視窗裡一定有這個檢視的項目
#（記事本視窗有「記事本」分頁；其他功能面板有「儲存聊天」）。假聊天的記事本視窗標題就是記事本，也算。
VIEW_MARKERS = {"notes": ("記事本", "Notes", "Note"), "notes-tab": ("記事本", "Notes", "Note"), "notes-deep": ("記事本", "Notes", "Note"),
                "menu": ("儲存聊天", "儲存聊天記錄", "Save chat", "Save chat history")}
# 記事本視窗裡可以點的分頁（只讀切換）：名稱完全相符、型別是 Button、在那個視窗裡唯一
NOTES_TABS = {"notes-tab": ("記事本", "Notes")}
# 分頁列：要點的分頁旁邊一定有成對的「相簿」分頁（真機第一次探索看到兩顆成對）；兩顆都在才點
TAB_PAIRS = {"notes-tab": ("相簿", "Album", "Albums")}


def _close_new(pid, before, res, view=None):
    """收尾：只關「這次點擊預期會開的」——無標題的彈出選單照舊關；有標題的頂層視窗只有在新出現的剛好一個、
    而且標題是這個檢視的介面用語（記事本／Notes…）時才送 WM_CLOSE。其他（例如 LINE 剛好跳出的來電視窗）一律不碰：
    關掉來電視窗可能等於拒接，是對外的動作。不關的照實記在 windows_left_open 與標題字數。"""
    import ctypes
    u32 = ctypes.windll.user32
    news = popup_menu.popup_windows(pid, exclude=before)

    def title(h):
        n = u32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1); u32.GetWindowTextW(h, buf, n + 1)
        return buf.value

    news = [w for w in news if not w["class"].startswith("shadow_")]   # 視窗陰影：隨主視窗消失，不是要關的東西
    titled = [w for w in news if u32.GetWindowTextLengthW(w["hwnd"]) > 0]
    expected = None
    if len(titled) == 1 and view in VIEW_MARKERS:
        marks = VIEW_MARKERS[view]
        if title(titled[0]["hwnd"]).strip() in marks:
            expected = titled[0]["hwnd"]
        else:
            try:
                names = {i.get("name") for i in (popup_menu.popup_items(titled[0]["hwnd"]).get("items") or [])}
            except Exception:
                names = set()
            if names & set(marks):
                expected = titled[0]["hwnd"]
    closed, left = 0, []
    for w in news:
        h = w["hwnd"]
        is_titled = w in titled
        if is_titled and h != expected:
            left.append({"title_len": len(title(h)), "class": w["class"]})
            continue
        try:
            if is_titled:
                u32.PostMessageW(h, 0x0010, 0, 0)   # WM_CLOSE
            else:
                popup_menu.close_popup(h)
        except Exception:
            pass
        for _ in range(20):
            time.sleep(0.15)
            if not u32.IsWindow(h) or not u32.IsWindowVisible(h):
                break
        if u32.IsWindow(h) and u32.IsWindowVisible(h):
            left.append({"title_len": len(title(h)) if is_titled else 0, "class": w["class"], "close_failed": True})
        else:
            closed += 1
    res["closed_windows"], res["windows_left_open"] = closed, len(left)
    if left:
        res["left_windows"] = left


def _click_notes_tab(pid, before, view, res, app=None, chat=None):
    """在剛開出的記事本視窗裡點「記事本」分頁（只讀切換）。只認：新出現的有標題視窗剛好一個、裡面有記事本項目；
    分頁按鈕名稱完全相符、型別 Button、唯一。投遞滑鼠訊息到那個視窗（游標不動）；不符就不點、照實回報。"""
    import ctypes
    u32 = ctypes.windll.user32
    titled = [w for w in popup_menu.popup_windows(pid, exclude=before)
              if not w["class"].startswith("shadow_") and u32.GetWindowTextLengthW(w["hwnd"]) > 0]
    if len(titled) != 1:
        res["tab_click"] = {"done": False, "why": "新視窗不是剛好一個（%d）" % len(titled)}
        return
    it = popup_menu.popup_items(titled[0]["hwnd"])
    def button_like(i):   # Qt 讀成 Button；WinForms 平面按鈕經 FromHandle 讀成 Pane（CI 實測）→ 只在按鈕大小時算
        r = i.get("rect") or [0, 0, 0, 0]
        return i.get("type") == "Button" or (i.get("type") == "Pane" and 0 < r[3] - r[1] <= 60 and 0 < r[2] - r[0] <= 300)
    tabs = [i for i in (it.get("items") or []) if i.get("name") in NOTES_TABS[view] and button_like(i)]
    pair = [i for i in (it.get("items") or []) if i.get("name") in TAB_PAIRS[view] and button_like(i)]
    if len(tabs) != 1 or len(pair) != 1:
        res["tab_click"] = {"done": False, "why": "分頁列對不上（記事本 %d、相簿 %d；兩顆都要剛好一顆）" % (len(tabs), len(pair))}
        ledger(app or "line", "probe-tab-click", False, res["tab_click"]["why"], chat)
        return
    try:
        popup_menu.click_item(titled[0]["hwnd"], tabs[0], root=it.get("root"))
    except popup_menu.CoordMismatch:
        res["tab_click"] = {"done": False, "why": "座標檢查沒過"}
        ledger(app or "line", "probe-tab-click", False, "座標檢查沒過：%s" % tabs[0].get("name"), chat)
        return
    # 對真實聊天程式的每一次點擊都要查得到（這一下走投遞滑鼠訊息，不經 guarded，所以在這裡記）
    ledger(app or "line", "probe-tab-click", True, "分頁：%s" % tabs[0].get("name"), chat)
    time.sleep(1.5)
    after = popup_menu.popup_items(titled[0]["hwnd"])
    res["tab_click"] = {"done": True}
    tabname = lambda i: i.get("name") in NOTES_TABS[view] + TAB_PAIRS[view] and button_like(i)
    # 分頁的選取狀態（只這兩顆；不含任何內容）：點擊前後哪一顆被選取
    res["tab_state"] = {"before": {i["name"]: i.get("sel") for i in (it.get("items") or []) if tabname(i)},
                        "after": {i["name"]: i.get("sel") for i in (after.get("items") or []) if tabname(i)}}
    res["notes_tab_items"] = [{"n": _mask(i.get("name")), "t": i.get("type")} for i in (after.get("items") or [])][:150]


def _deep_notes(pid, before, res):
    """只看結構（不點、不截、不輸出文字）：記事本視窗的子視窗類別與大小；RawView 全樹的類型個數、名稱長度分布、
    Text／Value 模式與文字總長度；若有 Chrome 系子視窗（嵌入的網頁），從子視窗再掃一次。
    （LegacyIAccessible 不在 .NET 的 UIA 用戶端裡，這一版不查。）"""
    import ctypes
    u32 = ctypes.windll.user32
    titled = [w for w in popup_menu.popup_windows(pid, exclude=before)
              if not w["class"].startswith("shadow_") and u32.GetWindowTextLengthW(w["hwnd"]) > 0]
    if len(titled) != 1:
        res["deep"] = {"error": "新視窗不是剛好一個（%d）" % len(titled)}
        return
    h = titled[0]["hwnd"]
    kids = popup_menu.child_windows(h)
    # 掃兩次、中間隔 2.5 秒：嵌入的網頁常在第一次有人查無障礙資訊時才非同步建樹，只掃一次可能得到假的「拿不到」
    web = [c for c in kids if c["class"].startswith("Chrome_") or "WebEngine" in c["class"] or "RenderWidget" in c["class"]]
    deep = {"children": [{k: c[k] for k in ("class", "w", "h", "visible")} for c in kids][:40], "web_children": len(web)}
    for rnd in ("scan1", "scan2"):
        deep[rnd] = {"root": popup_menu.deep_scan(h),
                     "children": [{"class": c["class"], "scan": popup_menu.deep_scan(c["hwnd"])} for c in web[:4]]}
        if rnd == "scan1":
            time.sleep(2.5)
    deep["scan_root"] = deep["scan2"]["root"]
    res["deep"] = deep


def _peek_notes(pid, before, chat, res, app):
    """看一眼（只限探索）：只截這次探索自己開出的記事本視窗（新出現的有標題視窗剛好一個、裡面有記事本分頁）。
    單一視窗擷取；主視窗絕不截（聊天列表會露出白名單以外的名稱與預覽）。圖存在該目標收集資料夾底下的暫存處，
    回傳路徑給執行的 agent 看完就刪；輸出不含任何內容。"""
    import ctypes, hashlib
    u32 = ctypes.windll.user32
    titled = [w for w in popup_menu.popup_windows(pid, exclude=before)
              if not w["class"].startswith("shadow_") and u32.GetWindowTextLengthW(w["hwnd"]) > 0]
    ok = False
    if len(titled) == 1:
        names = {i.get("name") for i in (popup_menu.popup_items(titled[0]["hwnd"]).get("items") or [])}
        if names & set(VIEW_MARKERS["notes"]):
            # chat 已經是白名單裡的那筆名稱（main 先做了 canonical_chat）；代號算法與 gather 相同 → 落在同一個目標資料夾
            tid = hashlib.sha256(("%s|%s" % (app, chat)).encode("utf-8")).hexdigest()[:12]
            d = os.path.join(HOME, "gather", app, tid, "_peek")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "notes-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
            try:
                popup_menu.capture_window(titled[0]["hwnd"], path)
                res["peek"] = {"path": path, "note": "看完就刪（只在本機，不印內容、不進紀錄或回報）"}
                ok = True
            except Exception as e:
                res["peek"] = {"error": type(e).__name__}
        else:
            res["peek"] = {"error": "新視窗裡沒有記事本分頁，不截"}
    else:
        res["peek"] = {"error": "新視窗不是剛好一個（%d），不截" % len(titled)}
    ledger(app, "probe-peek", ok, "記事本視窗" if ok else res["peek"].get("error", ""), chat)


PEEK_MAX_AGE_S = 30 * 60


def purge_old_peeks(now=None):
    """兜底：各目標 _peek 底下超過 30 分鐘的截圖一律刪掉（就算哪一次忘了刪或中途斷掉，記事本截圖也不會長期留在硬碟上）。"""
    import glob
    now = now or time.time()
    n = 0
    for f in glob.glob(os.path.join(HOME, "gather", "*", "*", "_peek", "*")):
        try:
            if now - os.path.getmtime(f) > PEEK_MAX_AGE_S:
                os.remove(f); n += 1
        except OSError:
            pass
    return n


def cmd_probe(app, prof, chat, view, pol, peek=False):
    """只讀探索 UI 結構（Sprint E）：只限白名單對話，開著的必須是指名對話；可點一顆檢視按鈕（notes，必須在驗過的標頭工具列裡）
    或打開選單（menu）看項目。輸出只有角色、可用動作與名稱字數（介面用語除外）。不輸入、不點其他東西。
    收尾（成功或失敗都一樣）：新出現的視窗一律關掉；記事本若是在主視窗內原地切換，標明 left_open。"""
    if not policy.in_whitelist(pol, chat):
        raise Refused("probe 只限白名單裡的對話（點開記事本等可能清掉「有新內容」的標記）")
    if popup_menu is None or os.name != "nt":
        raise Refused("probe 目前只支援 Windows")
    win = find_window(prof)
    full = snapshot(win, interactive=False)
    if not header_ok(prof, full, chat):
        raise Refused("目前開著的不是指名對話：先 open --chat 並確認標頭")
    pid = win.get("pid")
    res = {"view": view, "main_before": _shape(full["tree"])}
    before = popup_menu.visible_hwnds(pid)
    try:
        if view == "menu":
            snap = snapshot(win)
            btn, bp = find_node(snap, lambda n: n.get("role") == "button" and n.get("name") == prof["menu_button"])
            if not btn:
                raise Refused("找不到選單按鈕")
            guarded(prof, "click", btn, bp, {"zone": "main", "allow": "menu-button"})
        elif view in (prof.get("view_buttons") or {}) or view in NOTES_TABS or view == "notes-deep":
            nm = prof["view_buttons"]["notes" if (view in NOTES_TABS or view == "notes-deep") else view]
            tb = header_toolbar(full, chat)
            btn = next((k for k in (tb or {}).get("children") or [] if k.get("role") == "button" and k.get("name") == nm), None)
            if not btn:
                raise Refused("標頭工具列裡找不到「%s」" % nm)
            _, bp = find_node(full, lambda n: n is btn)
            guarded(prof, "click", btn, bp, {"zone": "main", "allow": "view-button", "target_chat": chat})
        elif view:
            raise Refused("不認得的檢視")
        time.sleep(1.5)
        if view in NOTES_TABS:
            _click_notes_tab(pid, before, view, res, app, chat)
        if view == "notes-deep":
            _deep_notes(pid, before, res)
        if peek and view in ("notes", "notes-tab"):
            _peek_notes(pid, before, chat, res, app)
        res["main_after"] = _shape(snapshot(win, interactive=False)["tree"])
        res["new_popups"], res["new_windows"] = [], []
        import ctypes
        for w in popup_menu.popup_windows(pid, exclude=before):
            if ctypes.windll.user32.GetWindowTextLengthW(w["hwnd"]) > 0:
                aw = next((x for x in ad("list-windows") if x.get("pid") == pid and str(x.get("hwnd") or "") == str(w["hwnd"])), None)
                entry = {"class": w["class"], "w": w["rect"][2] - w["rect"][0], "h": w["rect"][3] - w["rect"][1]}
                try:
                    it = popup_menu.popup_items(w["hwnd"])
                    entry["items"] = [{"n": _mask(i.get("name")), "t": i.get("type")} for i in (it.get("items") or [])][:120]
                except Exception as e:
                    entry["error"] = type(e).__name__
                if aw:
                    entry["title"] = _mask(aw.get("title") or "")
                res["new_windows"].append(entry)
            else:
                it = popup_menu.popup_items(w["hwnd"])
                res["new_popups"].append({"class": w["class"], "w": w["rect"][2] - w["rect"][0], "h": w["rect"][3] - w["rect"][1],
                                          "items": [{"n": _mask(i.get("name")), "t": i.get("type")} for i in (it.get("items") or [])][:80]})
        if view in (prof.get("view_buttons") or {}) and not res["new_windows"] and not res["new_popups"]:
            res["left_open"] = True   # 主視窗內原地切換：先不自己亂點返回，回報結構再決定正式路徑怎麼回去
    finally:
        _close_new(pid, before, res, view)
    ledger(app, "probe", True, view or "", chat)
    out(dict(res, ok=True))


def _dialog_titles(prof):
    return tuple(prof.get("dialog_titles") or ()) + ((prof["dialog_title"],) if prof.get("dialog_title") else ())


def save_dialogs(prof, pid):
    """這個 app（同 pid）目前開著的存檔對話框（標題相符）。"""
    t = _dialog_titles(prof)
    return [w for w in ad("list-windows") if w.get("title") in t and w.get("pid") == pid]


def cancel_dialogs(prof, wins):
    """把這些存檔對話框按取消（只認系統檔案對話框：要有檔名欄與取消鈕）。回傳按掉幾個。"""
    n = 0
    for w in wins:
        try:
            ds = snapshot(w)
            fn = find_node(ds, lambda x: str((x.get("native_id") or {}).get("value", "")) in prof["dialog_filename_ids"])[0]
            cn, cp = find_node(ds, lambda x: x.get("role") == "button" and str(
                (x.get("native_id") or {}).get("value", "")) in prof["dialog_cancel_ids"])
            if fn and cn:
                guarded(prof, "click", cn, cp, {"zone": "dialog"})
                n += 1
                time.sleep(0.4)
        except Exception:
            continue
    return n


def cmd_export(app, prof, dest, chat=None, load_hist=False):
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    win = find_window(prof)
    # 開始前：上一次遲到的存檔對話框（app 準備匯出比我們等的久）若還開著 → 先取消，不然會被算進這一次
    stray = save_dialogs(prof, win.get("pid"))
    if stray:
        EXPORT_CTX["stray_cancelled"] = cancel_dialogs(prof, stray)
        for _ in range(20):   # 對話框關掉要一點時間
            if not save_dialogs(prof, win.get("pid")):
                break
            time.sleep(0.25)
        if save_dialogs(prof, win.get("pid")):
            raise Refused("有之前留下的存檔對話框取消不掉：沒有開始匯出（請使用者把它關掉）")
    snap = snapshot(win)
    if prof.get("export_via") == "popup":
        if load_hist:   # gather：先把對話往上捲到底，讓匯出涵蓋畫面能載入的最早訊息
            EXPORT_CTX["history"] = load_history(prof, win)
            snap = snapshot(win)
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
    # 真機（2026-11-22）：人多、訊息多的 LINE 群組，點了「儲存聊天」之後要過一陣子對話框才出現 → 持續輪詢到 dialog_wait_s
    titles = _dialog_titles(prof)   # 後面的覆寫確認、存檔後檢查都用到
    dlg, cands = None, []
    t_end = time.time() + float(prof.get("dialog_wait_s", 90))
    while time.time() < t_end:
        time.sleep(0.3)
        cands = save_dialogs(prof, win.get("pid"))
        if len(cands) == 1:
            dlg = cands[0]
            break
        if len(cands) > 1:
            n = cancel_dialogs(prof, cands)
            raise Refused("同時出現 %d 個存檔對話框（已取消 %d 個）：不猜是哪一個，沒有存檔" % (len(cands), n))
        check_interrupt()
    if not dlg:
        diag = {"popup_click": EXPORT_CTX.get("popup_click"), "same_title_windows": len(cands),
                "waited_s": prof.get("dialog_wait_s", 90), "menu_open_after_click": EXPORT_CTX.get("menu_open_after_click"),
                "menu_retried": bool(EXPORT_CTX.get("menu_retried")), "stray_cancelled": EXPORT_CTX.get("stray_cancelled", 0)}
        try:
            diag["pid_windows"] = sorted({w.get("class") for w in popup_menu.popup_windows(win.get("pid"))}) if popup_menu else None
        except Exception:
            pass
        raise Refused("另存新檔對話框沒有出現（或不唯一）；診斷：%s" % json.dumps(diag, ensure_ascii=False))
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
        if not chat or not file_key(strip_count(chat)) or file_key(strip_count(chat)) not in file_key(default_name):
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
        still = [w for w in ad("list-windows") if w.get("title") in titles and w.get("pid") == win.get("pid")]
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
            for w in conf + [x for x in ad("list-windows") if x.get("title") in titles
                             and x.get("pid") == win.get("pid")]:
                no_btn, _ = find_node(snapshot(w), is_no)
                if no_btn:
                    break
            if no_btn:
                ad("click", no_btn["ref_id"])
                time.sleep(0.4)
            d2 = [w for w in ad("list-windows") if w.get("title") in titles and w.get("pid") == win.get("pid")]
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
            still = [w for w in ad("list-windows") if w.get("title") in titles
                     and w.get("pid") == win.get("pid")]
            diag["dialog_still_open"] = bool(still)
            other = [w.get("title") for w in ad("list-windows") if w.get("pid") == win.get("pid")
                     and w.get("title") not in titles + (prof["window_title"],)]
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
    # app 可能還在寫：等檔案大小連續 1 秒不變再驗（真機：一出現就讀，app 還開著檔案，刪不掉）
    last, stable_since = -1, time.time()
    for _ in range(40):
        sz = os.path.getsize(target)
        if sz != last:
            last, stable_since = sz, time.time()
        elif time.time() - stable_since >= 1.0:
            break
        time.sleep(0.25)

    def reject(why):
        if _safe_remove(target):
            raise Refused(why + "：已刪除檔案")
        raise Refused(why + "：**檔案刪不掉（app 還開著它），留在 %s**，請使用者確認後刪除" % target)

    verified = {"default_filename": bool(prof.get("verify_export_filename_contains_chat"))}
    # 身分驗證 2a：只讀第一行比對；不符就刪檔、不再讀任何一行
    if prof.get("verify_export_first_line_contains_chat"):
        with open(target, encoding="utf-8-sig", errors="replace") as f:
            first = f.readline()
        if strip_count(chat) not in first:
            reject("匯出檔第一行不含指名對話名稱（沒有讀其他內容）")
        verified["first_line"] = True
    # 身分驗證 2b：不同發言者數 ≤ 標頭人數（只數、不輸出任何名字或內容）
    sw = prof.get("verify_export_senders_within_members")
    if sw:
        members = EXPORT_CTX.get("members")
        rx = re.compile(sw["line_re"])
        senders = set()
        with open(target, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                if rx.match(line):
                    parts = line.rstrip("\r\n").split(" ")
                    if len(parts) > sw["sender_field"] + 1:
                        senders.add(parts[sw["sender_field"]])
        if members is None:
            reject("讀不到標頭人數，無法做存檔後的身分檢查")
        if len(senders) > members:
            reject("匯出檔的發言者數（%d）多於標頭人數（%d）：可能不是指名對話" % (len(senders), members))
        verified["senders_within_members"] = {"senders": len(senders), "members": members}
    with open(target, encoding="utf-8-sig", errors="replace") as f:
        n = sum(1 for _ in f)
    ledger(app, "export", True, chat=chat)
    out({"ok": True, "path": target, "bytes": os.path.getsize(target), "lines": n,
         "history": EXPORT_CTX.get("history"),
         "verified": verified,
         "saved_via": ("default-name-in-%s-then-moved" % moved_from) if moved_from else "typed-path",
         "dialog_folder_known": bool(dialog_folder)})


def _is_chat_list(node):
    """聊天清單：子項目幾乎都是按鈕（每個對話一顆，名稱打包著預覽）。LINE 的訊息區也是 listbox，但子項目不是按鈕。"""
    kids = node.get("children") or []
    return node.get("role") in ("list", "listbox") and len(kids) >= 3 and \
        sum(1 for k in kids if k.get("role") == "button") >= 0.8 * len(kids)


def load_history(prof, win, max_rounds=80, settle=1.5):
    """把目前對話往上捲到不再載入更早的訊息。用 UIA 找訊息區的位置（子項不是按鈕的那個 List；聊天清單的子項是按鈕），
    再對主視窗投遞 WM_MOUSEWHEEL（不動實體游標）。回傳只有數字：{"rounds","items_before","items_after","stable"}。"""
    if popup_menu is None or os.name != "nt":
        raise Refused("往上載入歷史目前只支援 Windows")
    pid = win.get("pid")
    tops = sorted(popup_menu.popup_windows(pid), key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]))
    if not tops:
        raise Refused("找不到 app 的主視窗")
    hwnd = tops[-1]["hwnd"]

    def msg_list():
        cands = [l for l in popup_menu.list_rects(hwnd) if l.get("children", 0) >= 1 and l.get("buttons", 0) < 0.5 * l["children"]]
        if not cands:
            return None
        return max(cands, key=lambda l: (l["rect"][2] - l["rect"][0]) * (l["rect"][3] - l["rect"][1]))
    ml = msg_list()
    if not ml:
        raise Refused("找不到目前對話的訊息區，無法往上載入歷史")
    before = last = ml["children"]; stable = rounds = 0
    while rounds < max_rounds and stable < 4:
        rounds += 1
        cx, cy = (ml["rect"][0] + ml["rect"][2]) // 2, (ml["rect"][1] + ml["rect"][3]) // 2
        try:
            popup_menu.wheel_up(hwnd, cx, cy, 10)
        except popup_menu.CoordMismatch as e:
            raise Refused("訊息區座標檢查沒過，沒有捲：%s" % e)
        time.sleep(settle)
        check_interrupt()
        ml2 = msg_list()
        if not ml2:
            break
        ml = ml2
        stable = stable + 1 if ml["children"] == last else 0
        last = ml["children"]
    return {"rounds": rounds, "items_before": before, "items_after": last, "stable": stable >= 4}


def _export_menu_popup(prof, win, snap, chat):
    """標頭的選單按鈕 → 彈出選單（獨立頂層視窗）→ 只點名稱完全等於預期項目的那一項（投遞訊息，不動游標）。"""
    if popup_menu is None or os.name != "nt":
        raise Refused("彈出選單路徑目前只支援 Windows")
    full = snapshot(win, interactive=False)
    if not header_ok(prof, full, chat):
        raise Refused("目前開著的不是指名對話：先 open --chat 並確認標頭")
    EXPORT_CTX["members"] = header_members(prof, full, chat)
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
    pop, items, pop_root = None, [], None
    for _ in range(4):
        for cand in pops:
            res = popup_menu.popup_items(cand["hwnd"])
            its = [i for i in res.get("items", []) if i.get("name")]
            if its:
                pop, items, pop_root = cand, its, res.get("root")
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
    time.sleep(0.4)   # 真機：開完對話後第一次點選單常常沒反應——選單還在淡入、位置還在變；先等它穩定
    before_fg = _fg()
    try:
        EXPORT_CTX["popup_click"] = popup_menu.click_item(pop["hwnd"], want[0], root=pop_root)
    except popup_menu.CoordMismatch as e:
        popup_menu.close_popup(pop["hwnd"])
        raise Refused("選單項目座標檢查沒過，沒有點：%s" % e)
    if before_fg is not None and _fg() != before_fg:
        FG_EVENTS.append("popup-click")
    # 點了 3 秒內沒出現存檔對話框、而且選單還開著（點擊沒送到）→ 關掉選單、重來一次（只重來一次）。
    # 選單已經關了＝點擊送到了，app 只是準備得慢：不可重點（真機：重點讓 LINE 排了兩次匯出，對話框一個比一個多）。
    titles = _dialog_titles(prof)
    for _ in range(15):
        time.sleep(0.2)
        if any(w.get("title") in titles and w.get("pid") == pid for w in ad("list-windows")):
            return
    menu_open = pop["hwnd"] in popup_menu.visible_hwnds(pid)
    EXPORT_CTX["menu_open_after_click"] = menu_open
    if menu_open and not EXPORT_CTX.get("menu_retried"):
        EXPORT_CTX["menu_retried"] = True
        try:
            popup_menu.close_popup(pop["hwnd"])
        except Exception:
            pass
        time.sleep(0.8)
        return _export_menu_popup(prof, win, snapshot(win), chat)


def cmd_close(app, prof, cancel_late=False):
    win = find_window(prof)
    if cancel_late:
        # 匯出失敗（對話框遲到）後的收尾：再看最多 30 秒，自己引出的存檔對話框一出現就按取消，不留在使用者面前
        t_end, n = time.time() + 30, 0
        while time.time() < t_end:
            d = save_dialogs(prof, win.get("pid"))
            if d:
                n += cancel_dialogs(prof, d)
                break
            time.sleep(0.5)
        EXPORT_CTX["late_cancelled"] = n
    snap = snapshot(win)
    if not prof.get("close_button"):
        ad("minimize", "--window-id", win["id"])
        ledger(app, "close", True, "minimized")
        out({"ok": True, "closed": "minimized", "late_dialogs_cancelled": EXPORT_CTX.get("late_cancelled")})
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
        purge_old_peeks()
    except Exception:
        pass
    try:
        if not prof:
            raise Refused("chat_guard 尚未支援這個 app（目前：fake-chat、line）")
        pol = policy.load(HOME)
        if not policy.is_granted(pol, app):
            raise Refused("使用者尚未授權這個 app（同意閘門）：先在對話中取得指名同意")
        if sub == "probe" and not policy.in_whitelist(pol, policy.canonical_chat(pol, arg(a, "--chat") or "") or ""):
            raise Refused("probe 只限白名單裡的對話（點開記事本等可能清掉「有新內容」的標記）")
        if sub in ("open", "export", "close", "act", "probe") or (sub == "read" and not arg(a, "--file")):
            idle_gate(prof, pol, a)
            HANDBACK["may_restore"] = True   # 只有過了閒置閘門的指令可以把縮小的視窗還原
        HANDBACK["enabled"] = pol.get("visibility") != "watch" and bool(prof.get("raises_foreground"))
        if arg(a, "--chat"):
            canon = policy.canonical_chat(pol, arg(a, "--chat"))
            if canon is None:
                raise Refused("白名單裡去掉人數標示後有兩筆以上對得上這個名稱：不猜，請使用者整理白名單")
            a[a.index("--chat") + 1] = canon
        if sub == "locate":
            cmd_locate(app, prof, arg(a, "--chat") or "")
        elif sub == "open":
            cmd_open(app, prof, arg(a, "--chat") or "", arg(a, "--expect"), pol)
        elif sub == "read":
            cmd_read(app, prof, arg(a, "--chat"), arg(a, "--file"))
        elif sub == "export":
            cmd_export(app, prof, arg(a, "--to") or ".", arg(a, "--chat"), load_hist="--load-history" in a)
        elif sub == "close":
            cmd_close(app, prof, cancel_late="--cancel-late-dialog" in a)
        elif sub == "probe":
            cmd_probe(app, prof, arg(a, "--chat") or "", arg(a, "--view"), pol, peek="--peek" in a)
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
    except Exception as e:   # 任何意外都要回 JSON（真機曾在存檔後當掉、什麼都沒回報）
        ledger(app, sub, False, "internal: %s" % type(e).__name__, arg(a, "--chat"))
        out({"ok": False, "error": "internal: %s: %s" % (type(e).__name__, str(e)[:200])}, 1)


if __name__ == "__main__":
    main()
