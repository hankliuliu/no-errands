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

REASONS = ("依設定不收", "已過期", "下載失敗", "超過大小上限", "工具做不到")
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
        return "missing", "工具做不到", "LINE 的檔案要從檔案分頁另存，這一步尚未完成"
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
    for it in new:
        if it["id"] in by:
            cur = by[it["id"]]
            for k in ("name", "size", "sender", "date", "time"):   # 補上之前拿不到的欄位
                if not cur.get(k) and it.get(k):
                    cur[k] = it[k]
            continue
        by[it["id"]] = it; added += 1
    return list(by.values()), added


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


def summary(items):
    rec = sum(1 for it in items if it["status"] == "received")
    miss = [it for it in items if it["status"] == "missing"]
    by = {r: sum(1 for it in miss if it["reason"] == r) for r in REASONS}
    return {"received": rec, "missing": len(miss), "missing_by_reason": {k: v for k, v in by.items() if v},
            "download_failed": by["下載失敗"]}


def render_missing(items, label):
    miss = sorted((it for it in items if it["status"] == "missing"), key=lambda x: (x.get("date") or "", x.get("time") or ""))
    s = summary(items)
    lines = ["# 未收清單：%s" % label, "",
             "看到了、但沒有收下的東西。要補哪一筆：`gather.py fetch --target <id> --item <編號>`；"
             "整類補：`--kind photo|video|file`、日期範圍：`--from YYYY-MM-DD --to YYYY-MM-DD`。", "",
             "已收 %d／未收 %d" % (s["received"], s["missing"]), ""]
    for r in REASONS:
        n = s["missing_by_reason"].get(r, 0)
        if n:
            lines.append("- %s：%d%s" % (r, n, "  ⚠" if r == "下載失敗" else ""))
    for r in REASONS:
        group = [it for it in miss if it["reason"] == r]
        if not group:
            continue
        lines += ["", "## %s（%d）" % (r, len(group)), "", "| 日期時間 | 傳送者 | 種類 | 檔名 | 大小 | 位置 | 說明 | 編號 |", "|---|---|---|---|---|---|---|---|"]
        for it in group:
            lines.append("| %s %s | %s | %s | %s | %s | %s | %s | `%s` |" % (
                it.get("date") or "", it.get("time") or "", it.get("sender") or "", KIND_LABEL.get(it["kind"], it["kind"]),
                (it.get("name") or "").replace("|", "／"), _size(it.get("size")), LOC_LABEL.get(it.get("location"), it.get("location") or ""),
                (it.get("note") or "").replace("|", "／"), it["id"]))
    return "\n".join(lines) + "\n"


def _size(n):
    if not n:
        return ""
    return "%.1f MB" % (n / 1048576) if n >= 1048576 else "%.0f KB" % (n / 1024) if n >= 1024 else "%d B" % n


def write_outputs(tdir, t, items):
    save_items(tdir, items)
    md = render_missing(items, t.get("label") or t.get("service"))
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
        if item and it["id"] != item and not it["id"].endswith(":" + item):
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
    d = os.path.join(tdir, "notes")
    return len([f for f in os.listdir(d) if f.endswith(".md")]) if os.path.isdir(d) else 0
