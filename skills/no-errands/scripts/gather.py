#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gather — 聊天收集：指定聊天室與起始時間，之後的訊息全部收下來，可以增量更新（純 stdlib）。

  gather.py add --service line|mattermost --chat <LINE 聊天室名稱｜Mattermost 頻道 id> --since "YYYY-MM-DD[ HH:MM]"
                [--dest <資料夾>] [--label <顯示名>] [--slug <context-gather 專案>]
  gather.py sync [--target <id> | --all] [--load-history]   # LINE 走 chat_guard 匯出（閒置閘門）；Mattermost 走 API（只發 GET）
  gather.py status                                          # 每個目標：起始、已存最早/最晚、筆數、上次同步、缺口
  gather.py read --target <id> [--from YYYY-MM-DD] [--to YYYY-MM-DD]
  gather.py import --target <id> <手機匯出的 txt>            # 桌面版拿不到的更早歷史，一次補齊
  gather.py targets                                         # 列出目標（id、服務、起始；不印聊天室名稱）
  gather.py config --target <id> [--photos on|off] [--videos on|off]   # 照片／影片預設不收（量大）
  gather.py rebuild [--target <id> | --all]                 # 離線：修正已存資料的發話者名字、重建附件索引與未收清單
  gather.py fetch --target <id> [--kind photo|video|file|audio] [--from D] [--to D] [--item <編號>]   # 依未收清單補收
附件：看到的每一筆都進 <目標>/files/index.jsonl（status received|missing＋原因）；人看的清單是 files/未收清單.md。
Mattermost 另存頻道標題／用途與釘選貼文（announcements.json）；read 會一併回傳公告與記事本。

儲存：~/.no-errands/gather/targets.json（目標設定；聊天室名稱只在這裡與政策檔）、
      ~/.no-errands/gather/<service>/<雜湊>/messages.jsonl ＋ state.json。內容不進 repo、log、ledger（ledger 只記筆數）。
--dest 指到 context-gather 的 data/projects/<slug>/cache/line/<id>/（或 cache/mattermost/<…>/）時，照它的落地格式寫每日 md、
_state.json、_meta.json，既有的 Read＋Grep 用法直接可用。

LINE 匯出沒有訊息 id：合併以（日期、時間、發話者、文字）的**序列**對齊——不用集合去重（同一分鐘的同一句話靠出現順序區分）；
新匯出接不上已存的部分 → 接上並記一個缺口，不會把不完整的資料當完整的交出去。
"""
import hashlib, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nowin  # noqa: E402,F401
import policy  # noqa: E402
import gather_items as gi  # noqa: E402

HOME = policy.HOME
GROOT = os.path.join(HOME, "gather")
TARGETS = os.path.join(GROOT, "targets.json")
HERE = os.path.dirname(os.path.abspath(__file__))
ANCHOR = 30


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1)); sys.exit(code)


def arg(a, k, d=None):
    return a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else d


def load_targets():
    try:
        return json.load(open(TARGETS, encoding="utf-8"))
    except Exception:
        return {}


def save_targets(t):
    os.makedirs(GROOT, exist_ok=True)
    json.dump(t, open(TARGETS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def tdir(tid, t):
    return os.path.join(GROOT, t["service"], tid)


def ledger(**kw):
    os.makedirs(GROOT, exist_ok=True)
    kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(os.path.join(HOME, "gather-ledger.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def norm_since(s):
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?$", (s or "").strip())
    if not m:
        return None
    y, mo, d, hh, mm = m.groups()
    return "%04d-%02d-%02d %02d:%02d" % (int(y), int(mo), int(d), int(hh or 0), int(mm or 0))


# ---------------------------------------------------------------- LINE 解析
_DATE = [re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\b"),                       # 桌面版：2026.11.01 星期日
         re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})\s*[（(]")]               # 手機版：2026/11/01（日）
_MSG = re.compile(r"^(\d{1,2}):(\d{2})(?:\s|\t)(.*)$")
_MSG12 = re.compile(r"^(上午|下午)(\d{1,2}):(\d{2})\t(.*)$")


def parse_line_export(text):
    """回 [{"date","time","sender","text","kind"}]（依檔案順序）。多行訊息併到上一則；貼圖、照片、收回等佔位照實保留。"""
    msgs, cur_date = [], None
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        dm = next((m for m in (r.match(line) for r in _DATE) if m), None)
        if dm:
            y, mo, d = dm.groups()
            cur_date = "%04d-%02d-%02d" % (int(y), int(mo), int(d))
            continue
        if cur_date is None:
            continue   # 檔頭（例如手機匯出的「[LINE] 與…的聊天記錄」「儲存日期」）
        mm = _MSG.match(line); m12 = _MSG12.match(line) if not mm else None
        if mm or m12:
            if mm:
                hh, mi, rest = mm.groups()
            else:
                ap, hh, mi, rest = m12.groups()
                hh = str((int(hh) % 12) + (12 if ap == "下午" else 0))
            parts = rest.split("\t", 1) if "\t" in rest else rest.split(" ", 1)
            sender = parts[0].strip()
            body = parts[1] if len(parts) > 1 else ""
            kind = "text"
            if re.match(r"^\[(貼圖|照片|影片|檔案|語音訊息|Sticker|Photo|Video|File)\]", body):
                kind = "attachment"
            elif "收回訊息" in body or "unsent a message" in body:
                kind = "unsent"
            msgs.append({"date": cur_date, "time": "%02d:%02d" % (int(hh), int(mi)), "sender": sender, "text": body, "kind": kind})
        elif msgs:
            msgs[-1]["text"] += "\n" + line   # 多行訊息的續行
    return msgs


# ---- 發話者名字含空白（真機 2026-11-22）：LINE 桌面版匯出用空白分隔「時間 名字 內文」，「Amy Chen 你好」會被切成
# 發話者「Amy」、內文「Chen 你好」。實測 47 位發話者裡 10 位有這個樣子（某人的訊息全部以同一個詞開頭）。
# 修法：從資料學出多段名字（某發話者 ≥4 則、九成以上以同一個詞開頭 → 那個詞屬於名字，最多三段），
# 新匯出先套用已存資料學到的名字再合併（合併鍵含發話者，兩邊要一致），合併後再學一次。
def _apply_names(msgs, names):
    names = sorted(names, key=len, reverse=True)
    for m in msgs:
        line = m["sender"] + " " + m["text"]
        for nm in names:
            if m["sender"] != nm and nm.startswith(m["sender"] + " ") and line.startswith(nm + " "):
                m["sender"], m["text"] = nm, line[len(nm) + 1:]
                break
    return msgs


# 佔位字與系統字樣永遠不是名字的一部分（例如「Amy 貼圖」是只貼貼圖的 Amy，不是名叫「Amy 貼圖」的人）
NAME_STOP = {"貼圖", "圖片", "照片", "影片", "檔案", "語音訊息", "相簿", "記事本", "公告", "收回訊息", "已收回訊息", "通話",
             "未接來電", "已取消", "Sticker", "Stickers", "Photo", "Photos", "Video", "Videos", "File", "Files", "Album",
             "Note", "Notes", "Announcement", "Call", "unsent"}


def learn_split_names(msgs, min_n=4, share=0.9):
    """回 (修正後的 msgs, 學到的多段名字)。可重複呼叫（已修正的不會再動）。"""
    names = set()
    for _ in range(3):
        by = {}
        for m in msgs:
            by.setdefault(m["sender"], []).append(m)
        found = set()
        for s, ms in by.items():
            if len(ms) < min_n:
                continue
            firsts = {}
            for m in ms:
                if " " in m["text"]:
                    t = m["text"].split(" ", 1)[0]
                    firsts[t] = firsts.get(t, 0) + 1
            if firsts:
                tok, n = max(firsts.items(), key=lambda kv: kv[1])
                if tok in NAME_STOP or tok.startswith("["):
                    continue
                if n >= min_n and n / len(ms) >= share:
                    found.add(s + " " + tok)
        if not found:
            break
        names |= found
        msgs = _apply_names(msgs, found)
    return msgs, names


def known_names(msgs):
    return {m["sender"] for m in msgs if " " in m["sender"]}


def key(m):
    return (m["date"], m["time"], m["sender"], m["text"])


def assign_ids(msgs):
    """穩定 id：同一個 (日期, 時間, 發話者, 文字) 的第 k 次出現 → 雜湊。序列合併後重算，順序不變 id 就不變。"""
    seen = {}
    for m in msgs:
        k = key(m); seen[k] = seen.get(k, 0) + 1
        m["id"] = hashlib.sha256(("%s|%s|%s|%s|%d" % (k + (seen[k],))).encode("utf-8")).hexdigest()[:16]
    return msgs


def _find_block(hay, block):
    """block（key 序列）在 hay 裡第一次連續出現的位置；找不到回 -1。"""
    n = len(block)
    if n == 0:
        return -1
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == block:
            return i
    return -1


def merge(stored, new):
    """序列對齊合併。回 (merged, info)。info: {"mode","added","gap"}。"""
    if not stored:
        return list(new), {"mode": "initial", "added": len(new), "gap": None}
    if not new:
        return list(stored), {"mode": "empty", "added": 0, "gap": None}
    S, N = [key(m) for m in stored], [key(m) for m in new]
    k = min(ANCHOR, len(S))
    tail_at = _find_block(N, S[-k:])
    head_at = _find_block(N, S[:k])
    if head_at >= 0 and tail_at >= 0 and tail_at >= head_at:
        # 新匯出包含整段已存（更早或更晚都有）→ 以新匯出為準，但已存中夾在中間、新匯出沒有的不丟：檢查完整包含
        if _find_block(N, S) >= 0:
            return list(new), {"mode": "superset", "added": len(N) - len(S), "gap": None}
    if tail_at >= 0:
        after = new[tail_at + k:]
        return list(stored) + list(after), {"mode": "append", "added": len(after), "gap": None}
    if head_at >= 0:
        before = new[:head_at]
        return list(before) + list(stored), {"mode": "prepend", "added": len(before), "gap": None}
    s_last, n_first = (S[-1][0], S[-1][1]), (N[0][0], N[0][1])
    n_last, s_first = (N[-1][0], N[-1][1]), (S[0][0], S[0][1])
    if n_first > s_last:
        return list(stored) + list(new), {"mode": "append-gap", "added": len(N),
                                          "gap": {"after": "%s %s" % s_last, "before": "%s %s" % n_first}}
    if n_last < s_first:
        return list(new) + list(stored), {"mode": "prepend-gap", "added": len(N),
                                          "gap": {"after": "%s %s" % n_last, "before": "%s %s" % s_first}}
    return list(stored), {"mode": "conflict", "added": 0,
                          "gap": {"note": "新匯出與已存的部分重疊卻對不上（可能有訊息被收回或編輯）；保留已存的，未合併"}}


# ---------------------------------------------------------------- 儲存
def load_msgs(d):
    p = os.path.join(d, "messages.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def save_msgs(d, msgs):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "messages.jsonl"), "w", encoding="utf-8") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")


def load_state(d):
    try:
        return json.load(open(os.path.join(d, "state.json"), encoding="utf-8"))
    except Exception:
        return {"gaps": [], "syncs": []}


def save_state(d, st):
    os.makedirs(d, exist_ok=True)
    json.dump(st, open(os.path.join(d, "state.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def since_filter(msgs, since):
    return [m for m in msgs if "%s %s" % (m["date"], m["time"]) >= since]


# ---------------------------------------------------------------- context-gather 落地
def write_dest(tid, t, msgs):
    dest = t.get("dest")
    if not dest:
        return 0
    os.makedirs(dest, exist_ok=True)
    days = {}
    for m in msgs:
        days.setdefault(m["date"], []).append(m)
    slug = t.get("slug") or "unknown"
    label = t.get("label") or tid
    for day, ms in days.items():
        if t["service"] == "line":
            head = "---\nproject: %s\nsource: line\ngroup_id: %s\ndate: %s\n---\n\n# Line: %s — %s\n\n" % (slug, tid, day, label, day)
            body = []
            for m in ms:
                body.append("### [%s] **%s** [message_id: %s]\n" % (m["time"], m["sender"], m["id"]))
                body.append(m["text"] if m["kind"] == "text" else "(%s) %s" % (m["kind"], m["text"]))
                body.append("")
            name = "%s.md" % day
        else:
            head = ("---\nproject: %s\nsource: mattermost\nchannel: %s\nchannel_kind: channel\ndate: %s\n---\n\n# %s — %s\n\n"
                    % (slug, t["chat"], day, label, day))
            body = []
            for m in ms:
                link = ("(%s/pl/%s)" % (t["permalink_base"], m["id"])) if t.get("permalink_base") else ""
                body.append("### [%s] **%s** [post_id: %s]%s\n" % (m["time"], m["sender"], m["id"], link))
                body.append(m["text"] + ("（已刪除）" if m.get("deleted") else ""))
                body.append("")
            name = "%s_chat.md" % day
        open(os.path.join(dest, name), "w", encoding="utf-8").write(head + "\n".join(body) + "\n")
    last = msgs[-1] if msgs else None
    st = {"last_synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "no-errands gather"}
    if t["service"] == "line" and last:
        st.update(last_message_id=last["id"], last_date=last["date"], last_time=last["time"],
                  # 與 context-gather 的 fetch-line 相同的游標欄位（毫秒，本機時區）
                  last_timestamp_ms=int(time.mktime(time.strptime("%s %s" % (last["date"], last["time"]), "%Y-%m-%d %H:%M")) * 1000))
    if t["service"] == "mattermost" and last:
        st["last_post_create_at_ms"] = max(m.get("create_at", 0) for m in msgs)
    json.dump(st, open(os.path.join(dest, "_state.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    meta = {"id": tid, "label": label, "members": sorted({m["sender"] for m in msgs})}
    json.dump(meta, open(os.path.join(dest, "_meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(days)


# ---------------------------------------------------------------- 指令
def cmd_add(a):
    service, chat, since = arg(a, "--service"), arg(a, "--chat"), norm_since(arg(a, "--since"))
    if service not in ("line", "mattermost") or not chat or not since:
        out({"ok": False, "error": "需要 --service line|mattermost、--chat、--since YYYY-MM-DD[ HH:MM]"}, 2)
    pol = policy.load(HOME)
    if service == "line":
        # 目標 id 用白名單裡的那筆名稱：帶不帶人數標示貼進來、之後人數怎麼變，都是同一個目標
        canon = policy.canonical_chat(pol, chat)
        if canon is None:
            out({"ok": False, "refused": True, "error": "白名單裡去掉人數標示後有兩筆以上對得上：不猜，請使用者整理白名單"}, 3)
        if not policy.in_whitelist(pol, canon):
            out({"ok": False, "refused": True, "error": "LINE 的聊天室必須在白名單（匯出要打開對話，會變已讀）。"
                 "請使用者說「把〈聊天室〉加進白名單」，由 policy.py whitelist add 加入後再登記"}, 3)
        chat = canon
    tid = hashlib.sha256(("%s|%s" % (service, chat)).encode("utf-8")).hexdigest()[:12]
    ts = load_targets()
    ts[tid] = {"service": service, "chat": chat, "since": since, "dest": arg(a, "--dest"), "label": arg(a, "--label"),
               "slug": arg(a, "--slug"), "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_targets(ts)
    ledger(cmd="add", target=tid, service=service)
    out({"ok": True, "target": tid, "service": service, "since": since, "next": "gather.py sync --target %s" % tid})


# 預設等使用者離開（閒置閘門）；--now = 使用者在對話中說「現在直接做」（他在電腦前、同意 LINE 跳出來）。
# 兩種都保留輸入監看：操作中他一碰鍵盤滑鼠就停手、把 LINE 縮回去（避免他打的字落進 LINE）。
GATE = {"args": ["--wait-idle", "--wait-timeout", "7200"], "close": ["--wait-idle", "--wait-timeout", "600"]}


def _line_export(chat, load_hist):
    tmp = os.path.join(GROOT, "_tmp_export")
    os.makedirs(tmp, exist_ok=True)
    cmd = [sys.executable, os.path.join(HERE, "chat_guard.py"), "line", "open", "--chat", chat] + GATE["args"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3 * 3600)
    o = json.loads(r.stdout or "{}") if r.stdout.strip().startswith("{") else {"ok": False, "error": "chat_guard 沒有回應"}
    if not o.get("ok"):
        return None, o
    cmd = [sys.executable, os.path.join(HERE, "chat_guard.py"), "line", "export", "--chat", chat, "--to", tmp]         + GATE["args"] + (["--load-history"] if load_hist else [])
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3 * 3600)
    o = json.loads(r.stdout or "{}") if r.stdout.strip().startswith("{") else {"ok": False, "error": "chat_guard 沒有回應"}
    late = ["--cancel-late-dialog"] if not o.get("ok") and "對話框沒有出現" in str(o.get("error", "")) else []
    subprocess.run([sys.executable, os.path.join(HERE, "chat_guard.py"), "line", "close"] + GATE["close"] + late,
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if not o.get("ok"):
        return None, o
    text = open(o["path"], encoding="utf-8-sig").read()
    try:
        os.remove(o["path"])   # 暫存匯出檔用完就刪；內容只留在 gather 的儲存
    except OSError:
        pass
    return text, o


def _mm_posts(t, since_ms):
    import mm_common
    pol = policy.load(HOME)
    mm = pol.get("mattermost") or {}
    base, tok = mm_common.conf(mm.get("token_file"), mm.get("url"))
    posts, page = {}, 0
    if since_ms:
        d = mm_common.get(base, tok, "/channels/%s/posts" % t["chat"], {"since": since_ms})
        posts.update(d.get("posts") or {})
    else:
        floor = int(time.mktime(time.strptime(t["since"], "%Y-%m-%d %H:%M")) * 1000)
        while page < 200:
            d = mm_common.get(base, tok, "/channels/%s/posts" % t["chat"], {"page": page, "per_page": 200})
            ps = d.get("posts") or {}
            if not ps:
                break
            posts.update(ps)
            if min(p.get("create_at", 0) for p in ps.values()) < floor:
                break
            page += 1
    users = {}

    def uname(uid):
        if uid not in users:
            try:
                users[uid] = mm_common.get(base, tok, "/users/%s" % uid).get("username") or uid
            except Exception:
                users[uid] = uid
        return users[uid]
    rows = []
    for p in posts.values():
        lt = time.localtime(p.get("create_at", 0) / 1000)
        rows.append({"id": p["id"], "date": time.strftime("%Y-%m-%d", lt), "time": time.strftime("%H:%M", lt),
                     "sender": uname(p.get("user_id", "")), "text": p.get("message", ""), "kind": "text",
                     "create_at": p.get("create_at", 0), "update_at": p.get("update_at", 0),
                     "deleted": bool(p.get("delete_at")),
                     "files": [{k: f.get(k) for k in ("id", "name", "size", "mime_type", "extension")}
                               for f in ((p.get("metadata") or {}).get("files") or [])]})
    return rows


def _mm_conn():
    import mm_common
    mm = (policy.load(HOME).get("mattermost") or {})
    base, tok = mm_common.conf(mm.get("token_file"), mm.get("url"))
    return mm_common, base, tok


def _mm_channel_title(mmc, base, tok, ch):
    """頻道給人看的名稱：私訊（D）寫「與〈對方〉的私訊」，兩邊同一人寫「給自己的私訊」；其他用顯示名稱。"""
    if ch.get("type") == "D":
        try:
            me = mmc.get(base, tok, "/users/me").get("id")
            ids = [x for x in (ch.get("name") or "").split("__") if x]
            other = next((x for x in ids if x != me), None)
            if not other:
                return "給自己的私訊"
            return "與 %s 的私訊" % mmc._user_label(mmc.get(base, tok, "/users/%s" % other))
        except Exception:
            return "Mattermost 私訊"
    return ch.get("display_name") or ch.get("name") or ""


def _mm_extras(t):
    """頻道標題／用途與釘選貼文（GET）。拿不到就照實回錯誤種類，不影響訊息同步。"""
    mmc, base, tok = _mm_conn()
    ann = {}
    try:
        ch = mmc.get(base, tok, "/channels/%s" % t["chat"])
        ann["header"], ann["purpose"] = ch.get("header") or "", ch.get("purpose") or ""
        ann["display_name"] = _mm_channel_title(mmc, base, tok, ch)
    except Exception as e:
        ann["header_error"] = str(e)[:80]
    try:
        pin = mmc.get(base, tok, "/channels/%s/pinned" % t["chat"])
        posts = (pin or {}).get("posts") or {}
        ann["pinned"] = [{"id": p["id"], "date": time.strftime("%Y-%m-%d", time.localtime(p.get("create_at", 0) / 1000)),
                          "time": time.strftime("%H:%M", time.localtime(p.get("create_at", 0) / 1000)),
                          "user_id": p.get("user_id"), "text": p.get("message", "")}
                         for p in sorted(posts.values(), key=lambda p: p.get("create_at", 0))]
    except Exception as e:
        ann["pinned_error"] = str(e)[:80]
    return ann


def _mm_fetcher():
    mmc, base, tok = _mm_conn()
    return lambda fid: mmc.get_bytes(base, tok, "/files/%s" % fid, gi.MAX_BYTES)


def update_items(tid, t, d, msgs, offline=False):
    """訊息同步完之後：附件進索引（看到的每一筆都留紀錄）、Mattermost 下載該收的、寫未收清單。"""
    items = gi.load_items(d)
    new = gi.from_line_messages(t, msgs) if t["service"] == "line" else gi.from_mm_files(t, msgs)
    if t["service"] == "line":
        # 訊息 id 會因修正發話者而變：舊索引裡對不到現存訊息的佔位項目（未收的）直接換成新的，不留孤兒
        live = {"m:" + m["id"] for m in msgs}
        items = [it for it in items if it["id"] in live or it["status"] == "received" or not it["id"].startswith("m:")]
    items, added = gi.merge_items(items, new)
    dl = None
    if t["service"] == "mattermost" and not offline:
        if any(it["status"] == "pending" for it in items):
            dl = gi.download_mm(d, items, _mm_fetcher())
        gi.save_announcements(d, _mm_extras(t))
    gi.write_outputs(d, t, items, target_title(t, tid), msgs, tid)
    return dict(gi.summary(items), new_items=added, **({"downloaded": dl} if dl else {}))


def sync_one(tid, t, load_hist=False):
    d = tdir(tid, t)
    st = load_state(d)
    stored = load_msgs(d)
    if t["service"] == "line":
        text, info = _line_export(t["chat"], load_hist)
        if text is None:
            return {"target": tid, "ok": False, "error": str(info.get("error", ""))[:200]}
        raw = parse_line_export(text)
        if raw:
            st["export_earliest"] = "%s %s" % (raw[0]["date"], raw[0]["time"])
        new = _apply_names(since_filter(raw, t["since"]), known_names(stored))
        merged, mi = merge(stored, new)
        merged = assign_ids(learn_split_names(merged)[0])
        if mi.get("gap"):
            st.setdefault("gaps", []).append(dict(mi["gap"], found=time.strftime("%Y-%m-%dT%H:%M:%S")))
        res = {"mode": mi["mode"], "added": mi["added"]}
    else:
        since_ms = st.get("cursor_ms") if st.get("version", 1) >= STATE_VERSION else None   # 舊版狀態：回補一次
        all_rows = _mm_posts(t, since_ms)
        rows = since_filter(all_rows, t["since"])   # 先套起始時間再計數（第一次會抓回起始時間之前的頁）
        by_id = {m["id"]: m for m in stored}
        added = updated = 0
        for r in rows:
            if r["id"] in by_id:
                if r["update_at"] != by_id[r["id"]].get("update_at") or r["deleted"] != by_id[r["id"]].get("deleted"):
                    by_id[r["id"]].update(r); updated += 1
            else:
                by_id[r["id"]] = r; added += 1
        merged = since_filter(sorted(by_id.values(), key=lambda m: m.get("create_at", 0)), t["since"])
        if all_rows:   # 游標用全部抓回的列：即使都早於起始時間也要前進，下次才不會從頭翻頁
            st["cursor_ms"] = max(max(r["update_at"], r["create_at"]) for r in all_rows)
        res = {"mode": "api", "added": added, "updated": updated}
    save_msgs(d, merged)
    if t["service"] == "mattermost" and st.get("version", 1) < STATE_VERSION:
        res["backfilled"] = True
    st["version"] = STATE_VERSION
    res["items"] = update_items(tid, t, d, merged)
    st["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    st.setdefault("syncs", []).append(dict(res, at=st["last_sync"], total=len(merged)))
    st["syncs"] = st["syncs"][-50:]
    save_state(d, st)
    days = write_dest(tid, t, merged)
    ledger(cmd="sync", target=tid, service=t["service"], total=len(merged), **res)
    return dict(res, target=tid, ok=True, total=len(merged), dest_days=days)


def cmd_sync(a):
    ts = load_targets()
    ids = list(ts) if "--all" in a else [arg(a, "--target")]
    if not ids or ids == [None] or any(i not in ts for i in ids):
        out({"ok": False, "error": "需要 --target <id> 或 --all（gather.py targets 列出 id）"}, 2)
    if "--now" in a:   # 只在使用者當下說「現在做」時用
        GATE["args"] = GATE["close"] = ["--user-ok"]
    results = [sync_one(i, ts[i], "--load-history" in a) for i in ids]
    write_overview()
    out({"ok": True, "results": results})


ALL_SINCE = "2000-01-01 00:00"   # 「拿得到的全部」
STATE_VERSION = 2   # 2：附件中繼資料（Sprint E）。比這舊的目標，下一次同步自動回補一次


def coverage(t, st, earliest):
    """回 (warning, note)。每次都出現的警告等於沒有警告：只在「無法證明起始時間之後是完整的」時才警告。
    - Mattermost 依 API 抓，拿得到就是全部 → 都沒有。
    - LINE 匯出檔本身就有早於起始時間的訊息 → 涵蓋完整 → 都沒有。
    - LINE 起始時間是「全部」→ 已知限制，只給一句說明。"""
    if t["service"] != "line" or not earliest or t["since"] >= earliest:
        return None, None
    ee = st.get("export_earliest")
    if ee and ee < t["since"]:
        return None, None
    if t["since"] <= ALL_SINCE:
        return None, "LINE 桌面版最早拿得到 %s；更早的要在手機匯出一次聊天記錄" % earliest
    return ("無法確認 %s～%s 之間有沒有訊息（桌面版只拿得到登入後同步的歷史）；要確認請在手機匯出一次聊天記錄。"
            % (t["since"], earliest[11:] if earliest[:10] == t["since"][:10] else earliest)), None


def target_title(t, tid):
    """給人看的名稱：LINE 用聊天室名稱（label 可能是測試時留下的說法）；Mattermost 用頻道的顯示名稱。"""
    if t["service"] == "line":
        return t.get("chat") or t.get("label") or tid
    dn = gi.load_announcements(tdir(tid, t)).get("display_name")
    return dn or t.get("label") or "Mattermost 頻道"


def write_overview():
    """收集根目錄的「收集總覽.md」（只在本機；可以有聊天室名稱）：每個聊天室一段，連到它的未收清單。"""
    ts = load_targets()
    order = sorted(ts.items(), key=lambda kv: (kv[1]["service"], target_title(kv[1], kv[0])))
    first = next((target_title(t, tid) for tid, t in order), "聊天室")
    has_line = any(t["service"] == "line" for _, t in order)
    L = ["# 聊天收集總覽", "", "更新於 %s。每個聊天室收了什麼、還缺什麼，都在下面；點「未收清單」看細項。" % time.strftime("%Y-%m-%d %H:%M"), "",
         "想補沒收下的東西，對 agent 說，例如：「把〈%s〉未收清單裡的照片補回來」。" % first]
    if has_line:
        L += ["", "LINE 的範圍：桌面版只拿得到這台電腦登入後同步下來的歷史（各聊天室的「最早拿得到」）；更早的要在手機上匯出聊天記錄給 agent 匯入。"
              "LINE 的記事本、公告、檔案還沒開始收，目前只記下訊息裡看得到的照片、影片、檔案。"]
    L.append("")
    for tid, t in order:
        d = tdir(tid, t); ms = load_msgs(d); st = load_state(d); items = gi.load_items(d); s = gi.summary(items)
        ann = gi.load_announcements(d)
        earliest = "%s %s" % (ms[0]["date"], ms[0]["time"]) if ms else None
        warn, note = coverage(t, st, earliest)
        L.append("## %s（%s）" % (target_title(t, tid), "LINE" if t["service"] == "line" else "Mattermost"))
        L.append("")
        L.append("- 訊息：%d 則%s；上次同步：%s" % (len(ms), ("，%s ～ %s %s" % (earliest, ms[-1]["date"], ms[-1]["time"])) if ms else "",
                                              (st.get("last_sync") or "還沒同步").replace("T", " ")[:16]))
        gaps = st.get("gaps") or []
        L.append("- 缺口：%s" % ("無" if not gaps else "%d 處（中間有訊息沒接上，見 gather.py status）" % len(gaps)))
        if t["service"] == "line":
            nn = gi.count_notes(d)
            L.append("- 記事本、公告：%s" % ("記事本 %d" % nn if nn else "還沒收（收集尚未完成）"))
        else:
            L.append("- 公告：頻道標題%s、釘選 %d 則" % ("有" if ann.get("header") else "無", len(ann.get("pinned") or [])))
        bykind = gi.missing_by_kind(items)
        rel = "%s/%s/files/未收清單.md" % (t["service"], tid)
        L.append("- 附件：已收 %d／未收 %d%s%s" % (s["received"], s["missing"], ("：%s" % bykind) if bykind else "",
                                               (" → [未收清單](%s)" % rel) if items else ""))
        if s["download_failed"]:
            L.append("- ⚠ 有 %d 個附件下載失敗，請 agent 重試補收" % s["download_failed"])
        if warn:
            L.append("- ⚠ %s" % warn)
        if note:
            L.append("- 最早拿得到：%s" % (earliest or ""))
        L.append("- 代號（給 agent 用）：`%s`" % tid)
        L.append("")
    os.makedirs(GROOT, exist_ok=True)
    open(os.path.join(GROOT, "收集總覽.md"), "w", encoding="utf-8").write("\n".join(L))


def cmd_status(a):
    ts, rows = load_targets(), []
    for tid, t in ts.items():
        d = tdir(tid, t); ms = load_msgs(d); st = load_state(d)
        earliest = "%s %s" % (ms[0]["date"], ms[0]["time"]) if ms else None
        row = {"target": tid, "service": t["service"], "since": t["since"], "count": len(ms), "earliest": earliest,
               "latest": "%s %s" % (ms[-1]["date"], ms[-1]["time"]) if ms else None, "last_sync": st.get("last_sync"),
               "gaps": st.get("gaps", [])}
        items = gi.load_items(d)
        if items:
            row["items"] = gi.summary(items)
            if row["items"]["download_failed"]:
                row["attention"] = "有 %d 個附件下載失敗：gather.py fetch --target %s 重試；清單在 files/未收清單.md" % (row["items"]["download_failed"], tid)
        ann = gi.load_announcements(d)
        row["notes"] = gi.count_notes(d)
        row["announcements"] = len(ann.get("announcements") or []) + len(ann.get("pinned") or [])
        warn, note = coverage(t, st, earliest)
        if warn:
            row["warning"] = warn
        if note:
            row["note"] = note
        if st.get("last_sync") and time.time() - time.mktime(time.strptime(st["last_sync"], "%Y-%m-%dT%H:%M:%S")) > 14 * 86400:
            # 門檻依 C0 實測：LINE 桌面版的匯出範圍＝已同步到這台電腦的歷史（登入時約回補前三週），捲動不會變多。
            # 隔太久沒同步，中間的訊息可能超出桌面版保有的範圍 → 形成缺口；14 天留一半餘裕。
            row["stale"] = "超過 14 天沒同步：中間的訊息可能超出桌面版保有的範圍，會形成缺口"
        rows.append(row)
    out({"ok": True, "targets": rows})


def cmd_read(a):
    ts = load_targets(); tid = arg(a, "--target")
    if tid not in ts:
        out({"ok": False, "error": "沒有這個目標"}, 2)
    d = tdir(tid, ts[tid])
    ms = load_msgs(d)
    lo, hi = arg(a, "--from", "0000"), arg(a, "--to", "9999")
    notes = []
    nd = os.path.join(d, "notes")
    if os.path.isdir(nd):
        notes = [open(os.path.join(nd, f), encoding="utf-8").read() for f in sorted(os.listdir(nd)) if f.endswith(".md")]
    out({"ok": True, "messages": [m for m in ms if lo <= m["date"] <= hi], "announcements": gi.load_announcements(d),
         "notes": notes})


def cmd_rebuild(a):
    """離線重建（不連網、不動 LINE 畫面）：LINE 修正已存資料的發話者名字（含空白的名字）並重算 id、改寫 --dest；
    所有目標從已存訊息重建附件索引與未收清單。"""
    ts = load_targets()
    ids = list(ts) if "--all" in a else [arg(a, "--target")]
    if not ids or ids == [None] or any(i not in ts for i in ids):
        out({"ok": False, "error": "需要 --target <id> 或 --all"}, 2)
    res = []
    for tid in ids:
        t = ts[tid]; d = tdir(tid, t)
        ms = load_msgs(d)
        row = {"target": tid, "before": len(ms)}
        if t["service"] == "line":
            old_senders = {m["sender"] for m in ms}
            ms, names = learn_split_names(ms)
            ms = assign_ids(ms)
            row.update(renamed_senders=len(names), senders_before=len(old_senders), senders_after=len({m["sender"] for m in ms}),
                       ids_unique=len({m["id"] for m in ms}) == len(ms))
            save_msgs(d, ms)
            write_dest(tid, t, ms)
        row["after"] = len(ms)
        row["items"] = update_items(tid, t, d, ms, offline=True)
        res.append(row)
    write_overview()
    ledger(cmd="rebuild", targets=len(res))
    out({"ok": True, "results": res})


def cmd_config(a):
    """每個目標的收集設定：--photos on|off、--videos on|off（預設都 off：量大）。"""
    ts = load_targets(); tid = arg(a, "--target")
    if tid not in ts:
        out({"ok": False, "error": "沒有這個目標"}, 2)
    changed = {}
    for k in ("photos", "videos"):
        v = arg(a, "--" + k)
        if v in ("on", "off"):
            ts[tid][k] = v == "on"; changed[k] = v
    save_targets(ts)
    write_overview()
    ledger(cmd="config", target=tid, **changed)
    out({"ok": True, "target": tid, "photos": bool(ts[tid].get("photos")), "videos": bool(ts[tid].get("videos")),
         "note": "只影響之後看到的項目；已列在未收清單的用 fetch 補"})


def cmd_fetch(a):
    """依未收清單補收：--kind photo|video|file|audio、--from/--to 日期、--item <編號>。使用者點名的就算「依設定不收」也補。"""
    ts = load_targets(); tid = arg(a, "--target")
    if tid not in ts:
        out({"ok": False, "error": "需要 --target <id>"}, 2)
    t = ts[tid]; d = tdir(tid, t)
    items = gi.load_items(d)
    sel = gi.select_fn(arg(a, "--kind"), arg(a, "--from"), arg(a, "--to"), arg(a, "--item"))
    chosen = [it for it in items if sel(it)]
    if not chosen:
        out({"ok": True, "target": tid, "matched": 0, "items": gi.summary(items)})
    if t["service"] == "mattermost":
        expired = sum(1 for it in chosen if it["reason"] == "已過期")
        res = gi.download_mm(d, items, _mm_fetcher(), select=lambda it: sel(it) and it["reason"] != "已過期", force=True)
        gi.write_outputs(d, t, items, target_title(t, tid), load_msgs(d), tid)
        write_overview()
        ledger(cmd="fetch", target=tid, matched=len(chosen), **res)
        out({"ok": True, "target": tid, "matched": len(chosen), "still_expired": expired, **res, "items": gi.summary(items)})
    # LINE：要從聊天室的檔案分頁另存（畫面操作），這一步尚未完成——照實回報，不假裝補到
    out({"ok": False, "target": tid, "matched": len(chosen), "error": "LINE 的補收要從聊天室的檔案分頁另存，這一步還沒做好；清單照舊保留",
         "items": gi.summary(items)}, 3)


def cmd_import(a):
    ts = load_targets(); tid = arg(a, "--target")
    files = [x for x in a[1:] if not x.startswith("--") and x != tid]
    if tid not in ts or not files or ts[tid]["service"] != "line":
        out({"ok": False, "error": "用法：gather.py import --target <LINE 目標 id> <手機匯出的 txt>"}, 2)
    t = ts[tid]; d = tdir(tid, t); st = load_state(d)
    stored = load_msgs(d)
    new = _apply_names(since_filter(parse_line_export(open(files[0], encoding="utf-8-sig").read()), t["since"]), known_names(stored))
    merged, mi = merge(stored, new)
    merged = assign_ids(learn_split_names(merged)[0])
    if mi.get("gap"):
        st.setdefault("gaps", []).append(dict(mi["gap"], found=time.strftime("%Y-%m-%dT%H:%M:%S")))
    save_msgs(d, merged); save_state(d, st); write_dest(tid, t, merged)
    items = update_items(tid, t, d, merged)
    write_overview()
    ledger(cmd="import", target=tid, added=mi["added"], total=len(merged))
    out({"ok": True, "mode": mi["mode"], "added": mi["added"], "total": len(merged), "items": items})


def cmd_targets(a):
    out({"ok": True, "targets": [{"target": k, "service": v["service"], "since": v["since"], "dest": bool(v.get("dest"))}
                                 for k, v in load_targets().items()]})


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    {"add": cmd_add, "sync": cmd_sync, "status": cmd_status, "read": cmd_read, "import": cmd_import,
     "targets": cmd_targets, "config": cmd_config, "fetch": cmd_fetch, "rebuild": cmd_rebuild}.get(a[0], lambda _: out({"ok": False, "error": "沒有這個子指令"}, 2))(a)


if __name__ == "__main__":
    main()
