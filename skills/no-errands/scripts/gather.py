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
        st.update(last_message_id=last["id"], last_date=last["date"], last_time=last["time"])
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
        wl = (pol.get("chat") or {}).get("whitelist") or []
        if chat not in wl:
            out({"ok": False, "refused": True, "error": "LINE 的聊天室必須在白名單（匯出要打開對話，會變已讀）。"
                 "請使用者說「把〈聊天室〉加進白名單」，由 policy.py whitelist add 加入後再登記"}, 3)
    tid = hashlib.sha256(("%s|%s" % (service, chat)).encode("utf-8")).hexdigest()[:12]
    ts = load_targets()
    ts[tid] = {"service": service, "chat": chat, "since": since, "dest": arg(a, "--dest"), "label": arg(a, "--label"),
               "slug": arg(a, "--slug"), "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_targets(ts)
    ledger(cmd="add", target=tid, service=service)
    out({"ok": True, "target": tid, "service": service, "since": since, "next": "gather.py sync --target %s" % tid})


def _line_export(chat, load_hist):
    tmp = os.path.join(GROOT, "_tmp_export")
    os.makedirs(tmp, exist_ok=True)
    cmd = [sys.executable, os.path.join(HERE, "chat_guard.py"), "line", "open", "--chat", chat, "--wait-idle", "--wait-timeout", "7200"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3 * 3600)
    o = json.loads(r.stdout or "{}") if r.stdout.strip().startswith("{") else {"ok": False, "error": "chat_guard 沒有回應"}
    if not o.get("ok"):
        return None, o
    cmd = [sys.executable, os.path.join(HERE, "chat_guard.py"), "line", "export", "--chat", chat, "--to", tmp,
           "--wait-idle", "--wait-timeout", "7200"] + (["--load-history"] if load_hist else [])
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3 * 3600)
    o = json.loads(r.stdout or "{}") if r.stdout.strip().startswith("{") else {"ok": False, "error": "chat_guard 沒有回應"}
    subprocess.run([sys.executable, os.path.join(HERE, "chat_guard.py"), "line", "close", "--wait-idle", "--wait-timeout", "600"],
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
                     "deleted": bool(p.get("delete_at"))})
    return rows


def sync_one(tid, t, load_hist=False):
    d = tdir(tid, t)
    st = load_state(d)
    stored = load_msgs(d)
    if t["service"] == "line":
        text, info = _line_export(t["chat"], load_hist)
        if text is None:
            return {"target": tid, "ok": False, "error": str(info.get("error", ""))[:200]}
        new = since_filter(parse_line_export(text), t["since"])
        merged, mi = merge(stored, new)
        merged = assign_ids(merged)
        if mi.get("gap"):
            st.setdefault("gaps", []).append(dict(mi["gap"], found=time.strftime("%Y-%m-%dT%H:%M:%S")))
        res = {"mode": mi["mode"], "added": mi["added"]}
    else:
        since_ms = st.get("cursor_ms")
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
    out({"ok": True, "results": [sync_one(i, ts[i], "--load-history" in a) for i in ids]})


def cmd_status(a):
    ts, rows = load_targets(), []
    for tid, t in ts.items():
        d = tdir(tid, t); ms = load_msgs(d); st = load_state(d)
        earliest = "%s %s" % (ms[0]["date"], ms[0]["time"]) if ms else None
        row = {"target": tid, "service": t["service"], "since": t["since"], "count": len(ms), "earliest": earliest,
               "latest": "%s %s" % (ms[-1]["date"], ms[-1]["time"]) if ms else None, "last_sync": st.get("last_sync"),
               "gaps": st.get("gaps", [])}
        if earliest and t["since"] < earliest:
            row["warning"] = "起始時間早於目前拿得到的最早訊息：%s 之前的還沒收到（桌面版可能拿不到；可用手機匯出 import 補齊）" % earliest
        if st.get("last_sync") and time.time() - time.mktime(time.strptime(st["last_sync"], "%Y-%m-%dT%H:%M:%S")) > 7 * 86400:
            row["stale"] = "超過 7 天沒同步：中間的訊息若超出桌面版能載入的範圍，會形成缺口"
        rows.append(row)
    out({"ok": True, "targets": rows})


def cmd_read(a):
    ts = load_targets(); tid = arg(a, "--target")
    if tid not in ts:
        out({"ok": False, "error": "沒有這個目標"}, 2)
    ms = load_msgs(tdir(tid, ts[tid]))
    lo, hi = arg(a, "--from", "0000"), arg(a, "--to", "9999")
    out({"ok": True, "messages": [m for m in ms if lo <= m["date"] <= hi]})


def cmd_import(a):
    ts = load_targets(); tid = arg(a, "--target")
    files = [x for x in a[1:] if not x.startswith("--") and x != tid]
    if tid not in ts or not files or ts[tid]["service"] != "line":
        out({"ok": False, "error": "用法：gather.py import --target <LINE 目標 id> <手機匯出的 txt>"}, 2)
    t = ts[tid]; d = tdir(tid, t); st = load_state(d)
    new = since_filter(parse_line_export(open(files[0], encoding="utf-8-sig").read()), t["since"])
    merged, mi = merge(load_msgs(d), new)
    merged = assign_ids(merged)
    if mi.get("gap"):
        st.setdefault("gaps", []).append(dict(mi["gap"], found=time.strftime("%Y-%m-%dT%H:%M:%S")))
    save_msgs(d, merged); save_state(d, st); write_dest(tid, t, merged)
    ledger(cmd="import", target=tid, added=mi["added"], total=len(merged))
    out({"ok": True, "mode": mi["mode"], "added": mi["added"], "total": len(merged)})


def cmd_targets(a):
    out({"ok": True, "targets": [{"target": k, "service": v["service"], "since": v["since"], "dest": bool(v.get("dest"))}
                                 for k, v in load_targets().items()]})


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    {"add": cmd_add, "sync": cmd_sync, "status": cmd_status, "read": cmd_read, "import": cmd_import,
     "targets": cmd_targets}.get(a[0], lambda _: out({"ok": False, "error": "沒有這個子指令"}, 2))(a)


if __name__ == "__main__":
    main()
