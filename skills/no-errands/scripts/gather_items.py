#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gather 的「訊息以外的東西」：附件（照片、影片、檔案、語音）的索引與未收清單，公告／釘選、記事本的存放。

規則（使用者 2026-11-22）：照片與影片預設不收，但**凡是看到卻沒收下的都要留一筆**（未收清單），讓使用者判斷要不要補。
- 索引：<目標資料夾>/files/index.jsonl，每筆一個項目，status = received | missing；missing 一定有 reason。
- reason 只用這五種：依設定不收、已過期、下載失敗、超過大小上限、工具做不到。
- 人看的清單：<目標資料夾>/files/未收清單.md（最上面各原因筆數、依原因分段、段內依日期排）。--dest 時也放一份。
- 項目 id 穩定：訊息裡的佔位用訊息 id（LINE 匯出沒有 message_id，訊息 id 是 日期|時間|發話者|內文|第幾次 的雜湊）；
  Mattermost 附件用 file_id。重複同步不會重複記，補收到的轉成 received。
- 內容與檔名只在本機；回報只講數量。
"""
import hashlib, json, os, re, time

REASONS = ("依設定不收", "已過期", "下載失敗", "超過大小上限", "尚未收集", "工具做不到")
# 給人看的說法（「尚未收集」＝這一類的收集還沒做完，是暫時的；「工具做不到」留給真的做不到的情況）
REASON_LABEL = {"尚未收集": "還沒收（檔案收集尚未完成）"}
ATTEMPT_REASONS = ("已過期", "下載失敗", "超過大小上限")   # 真的試過才會有的原因：重新同步不覆蓋
KIND_LABEL = {"photo": "照片", "video": "影片", "file": "檔案", "audio": "語音", "note-attachment": "記事本內的附件"}
LOC_LABEL = {"message": "訊息", "note": "記事本", "files-tab": "檔案分頁"}
# context-gather 的附件黑名單（sync.py blacklist_category）：照樣進未收清單，原因「依設定不收」並寫分類
_BL = {"壓縮檔": {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz"},
       "影片": {"mp4", "mov", "mkv", "avi", "webm", "flv", "wmv", "m4v", "3gp"},
       "音檔": {"mp3", "m4a", "wav", "aac", "flac", "ogg", "opus", "wma", "amr", "aiff"},
       "字體檔": {"ttf", "otf", "woff", "woff2", "eot", "fon", "fnt", "pfb", "pfm", "ttc"}}
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "heic", "webp", "bmp"}
DOC_EXT = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "xlsm", "csv", "txt", "md", "odt", "ods", "odp", "rtf", "json", "xml"}
FILE_EXT = DOC_EXT | IMAGE_EXT | set().union(*_BL.values())
MAX_BYTES = 50 * 1024 * 1024
# LINE 桌面版匯出的佔位是不帶括號的單詞（真機 2026-11-22：圖片、影片、貼圖）；手機匯出是 [照片] 這種
_LINE_EXACT = {"圖片": "photo", "照片": "photo", "Photo": "photo", "Photos": "photo", "影片": "video", "Video": "video",
               "語音訊息": "audio", "Voice message": "audio"}
_LINE_BRACKET = re.compile(r"^\[(照片|圖片|影片|檔案|語音訊息|Photo|Video|File|Voice message)\]\s*(.*)$", re.S)
_FILE_NAME = re.compile(r"^[^\n/\\]{1,180}\.([A-Za-z0-9]{1,5})$")


def blacklist_category(ext):
    ext = (ext or "").lower().lstrip(".")
    return next((cat for cat, exts in _BL.items() if ext in exts), None)


def ext_of(name):
    return os.path.splitext(name or "")[1].lower().lstrip(".")


def classify_line(m):
    """LINE 匯出的一則訊息若是附件佔位 → {"kind", "name"}；否則 None。貼圖不算資訊，不列。"""
    body = (m.get("text") or "").strip()
    if body in _LINE_EXACT:
        return {"kind": _LINE_EXACT[body], "name": None}
    b = _LINE_BRACKET.match(body)
    if b:
        tag, rest = b.groups()
        kind = {"照片": "photo", "圖片": "photo", "Photo": "photo", "影片": "video", "Video": "video",
                "語音訊息": "audio", "Voice message": "audio"}.get(tag, "file")
        return {"kind": kind, "name": rest.strip() or None}
    f = _FILE_NAME.match(body)
    if f and f.group(1).lower() in FILE_EXT:
        e = f.group(1).lower()
        return {"kind": "photo" if e in IMAGE_EXT else ("video" if e in _BL["影片"] else "file"), "name": body}
    return None


def default_status(t, kind, name, service):
    """新看到的項目該收還是不收（還沒真的下載之前的判斷）。回 (status, reason, note)。"""
    if kind == "photo" and not t.get("photos"):
        return "missing", "依設定不收", "照片預設不收（gather config --photos on 可開）"
    if kind == "video" and not t.get("videos"):
        return "missing", "依設定不收", "影片預設不收（gather config --videos on 可開）"
    if kind == "audio":
        return "missing", "依設定不收", "語音預設不收"
    cat = blacklist_category(ext_of(name))
    if cat and kind == "file":
        return "missing", "依設定不收", "附件黑名單：%s" % cat
    if service == "line":
        # LINE 的檔案要從聊天室的檔案分頁逐一另存（畫面操作）；還沒走那一步之前照實記「工具做不到」
        return "missing", "尚未收集", "LINE 的檔案要從聊天室的檔案分頁另存，這一步還在做"
    return "pending", None, None


def items_dir(tdir):
    return os.path.join(tdir, "files")


def load_items(tdir):
    p = os.path.join(items_dir(tdir), "index.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def save_items(tdir, items):
    os.makedirs(items_dir(tdir), exist_ok=True)
    with open(os.path.join(items_dir(tdir), "index.jsonl"), "w", encoding="utf-8") as f:
        for it in sorted(items, key=lambda x: (x.get("date") or "", x.get("time") or "", x["id"])):
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def merge_items(old, new):
    """新看到的併進索引：同 id 不重複；已收的不會因為重新同步變回未收。回 (合併後, 新增數)。"""
    by = {it["id"]: it for it in old}
    added = 0
    for it in sorted(new, key=lambda x: (x.get("date") or "", x.get("time") or "")):
        if it["id"] in by:
            cur = by[it["id"]]
            for k in ("name", "size", "sender", "date", "time"):   # 補上之前拿不到的欄位
                if not cur.get(k) and it.get(k):
                    cur[k] = it[k]
            if cur["status"] == "missing" and cur.get("reason") not in ATTEMPT_REASONS and it["status"] in ("missing", "pending"):
                cur.update(status=it["status"], reason=it["reason"], note=it["note"])   # 設定或說法改了：跟著目前的判斷
            continue
        by[it["id"]] = it; added += 1
    return number(list(by.values())), added


def number(items):
    """短流水號（#1、#2…）：只發一次，之後不變；新項目接在目前最大號之後。"""
    nxt = max([it.get("no") or 0 for it in items] + [0]) + 1
    for it in sorted(items, key=lambda x: (x.get("date") or "", x.get("time") or "", x["id"])):
        if not it.get("no"):
            it["no"] = nxt; nxt += 1
    return items


def from_line_messages(t, msgs):
    out = []
    for m in msgs:
        c = classify_line(m)
        if not c:
            continue
        st, reason, note = default_status(t, c["kind"], c["name"], "line")
        out.append({"id": "m:" + m["id"], "message_id": m["id"], "date": m["date"], "time": m["time"], "sender": m["sender"],
                    "kind": c["kind"], "name": c["name"], "size": None, "location": "message",
                    "status": st, "reason": reason, "note": note, "seen_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return out


def from_mm_files(t, post_rows):
    """Mattermost 貼文的附件（metadata.files）→ 項目；之後由 download_mm 實際下載。"""
    out = []
    for r in post_rows:
        for f in r.get("files") or []:
            e = (f.get("extension") or ext_of(f.get("name"))).lower()
            mime = f.get("mime_type") or ""
            kind = "photo" if mime.startswith("image/") or e in IMAGE_EXT else (
                "video" if mime.startswith("video/") or e in _BL["影片"] else ("audio" if mime.startswith("audio/") or e in _BL["音檔"] else "file"))
            st, reason, note = default_status(t, kind, f.get("name"), "mattermost")
            if st == "pending" and (f.get("size") or 0) > MAX_BYTES:
                st, reason, note = "missing", "超過大小上限", "大於 %d MB" % (MAX_BYTES // (1024 * 1024))
            out.append({"id": "f:" + f["id"], "file_id": f["id"], "post_id": r["id"], "date": r["date"], "time": r["time"],
                        "sender": r["sender"], "kind": kind, "name": f.get("name"), "size": f.get("size"), "location": "message",
                        "status": st, "reason": reason, "note": note, "seen_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return out


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "file")[:150]


def download_mm(tdir, items, fetch_bytes, select=None, force=False):
    """下載待收（pending）或被選中的 Mattermost 附件。fetch_bytes(file_id) → (bytes|None, err)。回各結果筆數。"""
    res = {"received": 0, "failed": 0, "too_large": 0}
    os.makedirs(items_dir(tdir), exist_ok=True)
    for it in items:
        if not it.get("file_id") or it["status"] == "received":
            continue
        chosen = select(it) if select else (it["status"] == "pending")
        if not chosen:
            continue
        if it["status"] == "missing" and it["reason"] == "依設定不收" and not force:
            continue
        data, err = fetch_bytes(it["file_id"])
        if data is None:
            if err == "too_large":
                it.update(status="missing", reason="超過大小上限", note="大於 %d MB" % (MAX_BYTES // (1024 * 1024))); res["too_large"] += 1
            elif err in ("http_404", "http_410"):
                it.update(status="missing", reason="已過期", note="伺服器已沒有這個檔案（%s）" % err)
            else:
                it.update(status="missing", reason="下載失敗", note=err); res["failed"] += 1
            continue
        fn = "%s__%s" % (it["file_id"], safe_name(it.get("name")))
        open(os.path.join(items_dir(tdir), fn), "wb").write(data)
        it.update(status="received", reason=None, note=None, path=fn, size=len(data),
                  sha256=hashlib.sha256(data).hexdigest(), received_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        res["received"] += 1
    for it in items:   # 還在 pending 的（例如這次沒選到）照實記
        if it["status"] == "pending":
            it.update(status="missing", reason="下載失敗", note="這次同步沒有下載到")
    return res


def missing_by_kind(items):
    """「未收 4：照片與影片 3（依設定不收）、檔案 1（還不能收）」——給人看的附件一行。"""
    short = {"依設定不收": "依設定不收", "尚未收集": "還不能收", "下載失敗": "下載失敗", "已過期": "已過期",
             "超過大小上限": "太大", "工具做不到": "做不到"}
    groups = (("照片與影片", ("photo", "video")), ("檔案", ("file",)), ("語音", ("audio",)), ("記事本附件", ("note-attachment",)))
    parts = []
    for label, kinds in groups:
        g = [it for it in items if it["status"] == "missing" and it["kind"] in kinds]
        if not g:
            continue
        rs = {}
        for it in g:
            rs[short.get(it["reason"], it["reason"])] = rs.get(short.get(it["reason"], it["reason"]), 0) + 1
        why = "、".join(k if len(rs) == 1 else "%s %d" % (k, v) for k, v in rs.items())
        parts.append("%s %d（%s）" % (label, len(g), why))
    return "、".join(parts)


def summary(items):
    rec = sum(1 for it in items if it["status"] == "received")
    miss = [it for it in items if it["status"] == "missing"]
    by = {r: sum(1 for it in miss if it["reason"] == r) for r in REASONS}
    return {"received": rec, "missing": len(miss), "missing_by_reason": {k: v for k, v in by.items() if v},
            "download_failed": by["下載失敗"]}


def _lead(text, n=40):
    t = " ".join((text or "").split())
    return (t[:n] + "…") if len(t) > n else t


def _context(msgs, mid, day=None):
    """這則訊息前後最接近的文字訊息開頭（各約 40 字）——讓人看得出那批照片在講什麼。只寫在本機清單裡。"""
    if not msgs or not mid:
        return "", ""
    idx = next((k for k, m in enumerate(msgs) if m.get("id") == mid), None)
    if idx is None:
        return "", ""
    is_text = lambda m: m.get("kind", "text") == "text" and m.get("text") and classify_line(m) is None and m["text"].strip() != "貼圖"
    before = next((msgs[k] for k in range(idx - 1, -1, -1) if is_text(msgs[k])), None)
    after = next((msgs[k] for k in range(idx + 1, len(msgs)) if is_text(msgs[k])), None)
    # 和項目不同天時寫出日期（跨日只寫時間會像時間倒流）
    fmt = lambda m: ("%s %s%s：%s" % (m["sender"], (m["date"][5:] + " ") if day and m.get("date") != day else "", m["time"],
                                     _lead(m["text"]))) if m else ""
    return fmt(before), fmt(after)


def _mins(it):
    try:
        h, m = (it.get("time") or "0:0").split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return 0


def _bursts(items, gap=5):
    """同一個人、同一天、幾分鐘內連續傳的照片／影片合成一批。"""
    out = []
    for it in sorted(items, key=lambda x: (x.get("date") or "", x.get("time") or "", x.get("no") or 0)):
        last = out[-1] if out else None
        if last and last[-1]["sender"] == it["sender"] and last[-1]["date"] == it["date"] and _mins(it) - _mins(last[-1]) <= gap:
            last.append(it)
        else:
            out.append([it])
    return out


def _cols(rows, header):
    """拿掉整欄都空的欄位。rows: list of dict；header: [(key, 標題)]。"""
    keep = [(k, h) for k, h in header if any(str(r.get(k) or "").strip() for r in rows)]
    lines = ["| " + " | ".join(h for _, h in keep) + " |", "|" + "---|" * len(keep)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(k) or "").replace("|", "／").replace("\n", " ") for k, _ in keep) + " |")
    return lines


def render_missing(items, title, msgs=None, tid=None, service=None):
    """給人看的未收清單：用來判斷要不要去補。有檔名的檔案最前、照片影片合併成批並附前後文、空欄位拿掉、短流水號。"""
    miss = [it for it in items if it["status"] == "missing"]
    dates = sorted(m["date"] for m in (msgs or []) if m.get("date"))
    rng = ("涵蓋 %s ～ %s。" % (dates[0], dates[-1])) if dates else ""
    L = ["# 未收清單：%s" % title, "", "%s產生於 %s。" % (rng, time.strftime("%Y-%m-%d %H:%M")), ""]
    if not miss:
        return "\n".join(L + ["沒有未收的東西。", ""])
    s = summary(items)
    L.append("這裡列出聊天室裡出現過、但還沒收下來的照片、影片和檔案（已收 %d、未收 %d）。" % (s["received"], s["missing"]))
    if service == "line":
        L.append("目前 LINE 的照片、影片、檔案還不能補收（要從聊天室的分頁另存，這部分還在做）。可以先看這份清單決定哪些想要，做好之後跟 agent 說一聲就會補。")
    else:
        ex = next((it for it in sorted(miss, key=lambda x: x.get("no") or 0) if it["reason"] != "已過期"), miss[0])
        L.append("想補的話，直接對 agent 說，例如：「補收〈%s〉%s 的照片」或「把〈%s〉清單裡的 #%d 補回來」。" % (
            title, (miss[-1].get("date") or "")[5:], title, ex.get("no") or 0))
    L.append("")
    files = [it for it in miss if it["kind"] == "file"]
    media = [it for it in miss if it["kind"] in ("photo", "video")]
    others = [it for it in miss if it not in files and it not in media]
    label = lambda it: REASON_LABEL.get(it["reason"], it["reason"])
    if files:
        L += ["## 檔案（%d）" % len(files), ""]
        notes = {it.get("note") for it in files}
        if len(notes) == 1 and next(iter(notes)):
            L += [next(iter(notes)), ""]
        same_why = len({label(it) for it in files}) == 1
        if same_why and not (len(notes) == 1 and next(iter(notes))):
            L += ["狀態：%s" % label(files[0]), ""]
        rows = [{"no": "#%d" % it["no"], "when": "%s %s" % ((it.get("date") or "")[5:], it.get("time") or ""), "who": it.get("sender"),
                 "name": it.get("name"), "size": _size(it.get("size")), "why": "" if same_why else label(it),
                 "note": it.get("note") if len(notes) > 1 else ""} for it in sorted(files, key=lambda x: x.get("no") or 0)]
        L += _cols(rows, [("no", "#"), ("when", "時間"), ("who", "傳送者"), ("name", "檔名"), ("size", "大小"), ("why", "狀態"), ("note", "說明")]) + [""]
    for reason in ("下載失敗", "已過期", "超過大小上限"):
        grp = [it for it in others + media if it["reason"] == reason]
        if grp:
            L += ["## %s（%d）" % (reason, len(grp)), ""]
            rows = [{"no": "#%d" % it["no"], "when": "%s %s" % ((it.get("date") or "")[5:], it.get("time") or ""), "who": it.get("sender"),
                     "kind": KIND_LABEL.get(it["kind"], it["kind"]), "note": it.get("note")} for it in grp]
            L += _cols(rows, [("no", "#"), ("when", "時間"), ("who", "傳送者"), ("kind", "種類"), ("note", "說明")]) + [""]
    rest_media = [it for it in media if it["reason"] not in ("下載失敗", "已過期", "超過大小上限")]
    if rest_media:
        L += ["## 照片與影片（%d）" % len(rest_media), ""]
        notes = {label(it) for it in rest_media}
        L += ["%s。同一個人幾分鐘內連續傳的合成一列，附上前後最接近的文字訊息，方便判斷這批在講什麼。" % "、".join(sorted(notes)), ""]
        rows = []
        for b in _bursts(rest_media):
            kinds = {}
            for it in b:
                kinds[KIND_LABEL[it["kind"]]] = kinds.get(KIND_LABEL[it["kind"]], 0) + 1
            t0, t1 = b[0].get("time") or "", b[-1].get("time") or ""
            pre, post = _context(msgs, b[0].get("message_id") or b[0].get("post_id"), b[0].get("date"))
            if len(b) > 1:
                _, post = _context(msgs, b[-1].get("message_id") or b[-1].get("post_id"), b[-1].get("date"))
            nos = sorted(it["no"] for it in b)
            rows.append({"no": "#%d" % nos[0] if len(nos) == 1 else "#%d–#%d" % (nos[0], nos[-1]) if nos[-1] - nos[0] == len(nos) - 1
                         else " ".join("#%d" % n for n in nos),
                         "when": "%s %s" % ((b[0].get("date") or "")[5:], t0 if t0 == t1 else "%s–%s" % (t0, t1)),
                         "who": b[0].get("sender"), "what": "、".join("%s ×%d" % kv for kv in kinds.items()),
                         "pre": pre, "post": post})
        L += _cols(rows, [("no", "#"), ("when", "時間"), ("who", "傳送者"), ("what", "內容"), ("pre", "前一則"), ("post", "後一則")]) + [""]
    rest = [it for it in others if it["reason"] not in ("下載失敗", "已過期", "超過大小上限")]
    if rest:
        L += ["## 其他（%d）" % len(rest), ""]
        rows = [{"no": "#%d" % it["no"], "when": "%s %s" % ((it.get("date") or "")[5:], it.get("time") or ""), "who": it.get("sender"),
                 "kind": KIND_LABEL.get(it["kind"], it["kind"]), "why": label(it)} for it in rest]
        L += _cols(rows, [("no", "#"), ("when", "時間"), ("who", "傳送者"), ("kind", "種類"), ("why", "狀態")]) + [""]
    L += ["---", "", "給 agent 用：`gather.py fetch --target %s --item <#號>`；整類 `--kind photo|video|file|audio`；"
          "日期 `--from YYYY-MM-DD --to YYYY-MM-DD`；照片影片之後都要收：`gather.py config --target %s --photos on --videos on`。"
          % (tid or "<id>", tid or "<id>"), ""]
    return "\n".join(L)


def _size(n):
    if not n:
        return ""
    return "%.1f MB" % (n / 1048576) if n >= 1048576 else "%.0f KB" % (n / 1024) if n >= 1024 else "%d B" % n


def write_outputs(tdir, t, items, title=None, msgs=None, tid=None):
    number(items)
    save_items(tdir, items)
    md = render_missing(items, title or t.get("label") or t.get("chat") or t.get("service"), msgs, tid, t.get("service"))
    if t.get("service") == "line":
        import gather_notes
        ns = gather_notes.missing_section(tdir)
        if ns:
            md = md.replace("沒有未收的東西。", "附件沒有未收的東西。")
            cut = md.find("\n---\n")
            md = (md[:cut + 1] + "\n".join(ns) + "\n" + md[cut + 1:]) if cut >= 0 else md.rstrip("\n") + "\n\n" + "\n".join(ns) + "\n"
    open(os.path.join(items_dir(tdir), "未收清單.md"), "w", encoding="utf-8").write(md)
    if t.get("dest"):
        d = os.path.join(t["dest"], "files")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "未收清單.md"), "w", encoding="utf-8").write(md)
        with open(os.path.join(d, "index.jsonl"), "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        for it in items:   # 已收的原檔照 context-gather 慣例放 cache/<source>/<id>/files/<鍵>__<檔名>
            if it["status"] == "received" and it.get("path"):
                src, dst = os.path.join(items_dir(tdir), it["path"]), os.path.join(d, it["path"])
                if os.path.exists(src) and not os.path.exists(dst):
                    open(dst, "wb").write(open(src, "rb").read())


def select_fn(kind=None, date_from=None, date_to=None, item=None):
    def sel(it):
        if it["status"] == "received":
            return False
        if item and it["id"] != item and not it["id"].endswith(":" + item) and str(it.get("no")) != str(item).lstrip("#"):
            return False
        if kind and it["kind"] != kind:
            return False
        if date_from and (it.get("date") or "") < date_from:
            return False
        if date_to and (it.get("date") or "") > date_to:
            return False
        return True
    return sel


# ---------------------------------------------------------------- 公告／釘選、記事本
def save_announcements(tdir, ann):
    """ann = {"header", "purpose", "pinned": [...], "announcements": [...]}（拿得到哪些放哪些）。"""
    p = os.path.join(tdir, "announcements.json")
    json.dump(dict(ann, fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S")), open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def load_announcements(tdir):
    p = os.path.join(tdir, "announcements.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def count_notes(tdir):
    p = os.path.join(tdir, "notes.jsonl")
    n = sum(1 for l in open(p, encoding="utf-8") if l.strip()) if os.path.exists(p) else 0
    d = os.path.join(tdir, "notes")
    return n + (len([f for f in os.listdir(d) if f.endswith(".md")]) if os.path.isdir(d) else 0)
