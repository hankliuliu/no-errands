#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LINE 記事本：擷取下來的圖 → agent 逐張轉寫 → 檢查、去重、建索引（純檔案，不讀圖）。

工具自己讀不了圖。chat_guard line notes 把記事本視窗一段一段擷取到 <目標>/_notes_shots/<run>/（附 manifest.json），
執行同步的 agent 用讀圖工具一次看一張，把畫面上的每一則寫成 JSON 交給 add；這裡負責：
  - 欄位檢查、則數核對（agent 說這張看到幾則開頭，要和交來的完整則數一樣）
  - 去重（作者＋時間＋內文開頭）、相鄰兩張重疊的同一則全文要一致（不一致 → 待人看）
  - 一張通過就刪那張圖；finish 時寫 notes.jsonl、記事本狀態（給總覽與未收清單用），整個 run 的資料夾刪掉
  - 沒轉完的圖超過 24 小時清掉，那一段記成「擷取後沒轉完」

看圖得到的內容只寫進收集資料夾的檔案；回傳給 agent 的只有數字與日期。
"""
import datetime, glob, hashlib, json, os, re, shutil, time, unicodedata

SOURCE = "AI 看圖轉寫（原文以 LINE 為準）"
SHOT_MAX_AGE_S = 24 * 3600
FIELDS = {"author": str, "posted_at": str, "text": str, "truncated": bool, "images": int}
OPTIONAL = {"cut_top": bool, "cut_bottom": bool, "stickers": int}


def shots_root(tdir):
    return os.path.join(tdir, "_notes_shots")


def _load(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def _dump(p, obj):
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def load_notes(tdir):
    p = os.path.join(tdir, "notes.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def save_notes(tdir, notes):
    with open(os.path.join(tdir, "notes.jsonl"), "w", encoding="utf-8") as f:
        for n in notes:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")


def load_state(tdir):
    return _load(os.path.join(tdir, "notes_state.json"), {})


def save_state(tdir, st):
    _dump(os.path.join(tdir, "notes_state.json"), st)


def runs(tdir):
    out = []
    for m in sorted(glob.glob(os.path.join(shots_root(tdir), "*", "manifest.json"))):
        man = _load(m, None)
        if man:
            out.append((os.path.dirname(m), man))
    return out


# ---------------------------------------------------------------- 正規化
def norm_text(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", " ", s).strip()


_FULL = [re.compile(r"^(\d{4})[./\-年](\d{1,2})[./\-月](\d{1,2})日?\s*(?:\([^)]*\)|（[^）]*）|週.|星期.)?\s*(\d{1,2}):(\d{2})$")]
_NOYEAR = re.compile(r"^(\d{1,2})[./\-月](\d{1,2})日?\s*(?:\([^)]*\)|（[^）]*）|週.|星期.)?\s*(\d{1,2}):(\d{2})$")
_REL = re.compile(r"^(今天|昨天|前天|Today|Yesterday)\s*(\d{1,2}):(\d{2})$", re.I)
_TIME = re.compile(r"^(上午|下午|AM|PM)?\s*(\d{1,2}):(\d{2})\s*(AM|PM)?$", re.I)


def norm_time(raw, captured):
    """畫面上的日期時間 → ("YYYY-MM-DD HH:MM" 或 None, 是否用推的)。captured 是擷取時間（datetime）。
    沒有年份：取擷取當年，若比擷取時間還晚就算前一年（推的）。今天／昨天：相對擷取日（推的）。"""
    s = norm_text(raw).replace("：", ":")
    s = re.sub(r"(上午|AM)\s*(\d{1,2}):", lambda m: "%s:" % m.group(2), s, flags=re.I)
    s = re.sub(r"(下午|PM)\s*(\d{1,2}):", lambda m: "%d:" % (int(m.group(2)) % 12 + 12), s, flags=re.I)
    try:
        for rx in _FULL:
            m = rx.match(s)
            if m:
                y, mo, d, hh, mm = map(int, m.groups())
                return datetime.datetime(y, mo, d, hh, mm).strftime("%Y-%m-%d %H:%M"), False
        m = _NOYEAR.match(s)
        if m:
            mo, d, hh, mm = map(int, m.groups())
            dt = datetime.datetime(captured.year, mo, d, hh, mm)
            if dt > captured + datetime.timedelta(days=1):
                dt = dt.replace(year=captured.year - 1)
            return dt.strftime("%Y-%m-%d %H:%M"), True
        m = _REL.match(s)
        if m:
            back = {"今天": 0, "today": 0, "昨天": 1, "yesterday": 1, "前天": 2}[m.group(1).lower() if m.group(1).isascii() else m.group(1)]
            day = captured.date() - datetime.timedelta(days=back)
            return "%s %02d:%02d" % (day.isoformat(), int(m.group(2)), int(m.group(3))), True
        m = _TIME.match(s)
        if m:
            hh = int(m.group(2)) % 24
            if (m.group(4) or "").upper() == "PM" and hh < 12:
                hh += 12
            return "%s %02d:%s" % (captured.date().isoformat(), hh, m.group(3)), True
    except ValueError:
        pass
    return None, False


def note_key(author, when, text, images):
    t = norm_text(text)
    body = t[:40] if t else "（無文字，附圖 %d）" % images
    return hashlib.sha256(("%s|%s|%s" % (norm_text(author), when or "", body)).encode("utf-8")).hexdigest()[:16]


def _same(a, b):
    """跨次擷取的同一則：鍵相同；或作者相同、內文開頭（至少 8 字）相同、時間差一天內（相對日期換算可能差一天）。"""
    if a["key"] == b["key"]:
        return True
    ta, tb = norm_text(a.get("text")), norm_text(b.get("text"))
    if norm_text(a.get("author")) != norm_text(b.get("author")) or len(ta) < 8 or ta[:40] != tb[:40]:
        return False
    try:
        da = datetime.datetime.strptime(a["posted_at"], "%Y-%m-%d %H:%M")
        db = datetime.datetime.strptime(b["posted_at"], "%Y-%m-%d %H:%M")
        return abs((da - db).total_seconds()) <= 86400 + 60
    except Exception:
        return False


# ---------------------------------------------------------------- 指令
def purge_expired(tdir, now=None):
    """沒轉完的圖超過 24 小時：整個 run 清掉，張數記進狀態（那一段記事本未收）。回清掉的張數。"""
    now = now or time.time()
    gone = 0
    for rdir, man in runs(tdir):
        try:
            created = time.mktime(time.strptime(man["created"], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            created = os.path.getmtime(os.path.join(rdir, "manifest.json"))
        if now - created > SHOT_MAX_AGE_S:
            n = sum(1 for s in man.get("shots", []) if s.get("status") == "pending")
            shutil.rmtree(rdir, ignore_errors=True)
            if n:
                st = load_state(tdir)
                st["expired_shots"] = st.get("expired_shots", 0) + n
                st["expired_at"] = time.strftime("%Y-%m-%d")
                save_state(tdir, st)
            gone += n
    return gone


def pending(tdir):
    """還沒轉的張（最舊的 run 先、依編號）：[{shot: "<run>/<NN>", path}]。"""
    purge_expired(tdir)
    out = []
    for rdir, man in runs(tdir):
        for s in man.get("shots", []):
            if s.get("status") == "pending":
                out.append({"shot": "%s/%02d" % (man["run"], s["n"]), "path": os.path.join(rdir, s["file"])})
    return out


class Bad(Exception):
    pass


def _find_shot(tdir, shot):
    try:
        run, n = shot.split("/")
        n = int(n)
    except Exception:
        raise Bad("--shot 要寫成 <run>/<編號>，例如 20261123-010203/01")
    rdir = os.path.join(shots_root(tdir), run)
    man = _load(os.path.join(rdir, "manifest.json"), None)
    if not man:
        raise Bad("找不到這個 run（可能已過期清掉）")
    s = next((x for x in man["shots"] if x["n"] == n), None)
    if not s:
        raise Bad("這個 run 沒有第 %d 張" % n)
    return rdir, man, s


def validate(items):
    if not isinstance(items, list):
        raise Bad("要交一個 JSON 陣列（這張圖上的每一則一筆；沒有就交 []）")
    errs = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            errs.append("第 %d 筆不是物件" % (i + 1)); continue
        cut = it.get("cut_top") or it.get("cut_bottom")
        for k, t in FIELDS.items():
            if k not in it:
                if cut and k in ("posted_at", "author", "text"):
                    continue   # 被圖邊切到的那一則可以缺
                errs.append("第 %d 筆缺 %s" % (i + 1, k)); continue
            if t is int and (not isinstance(it[k], int) or isinstance(it[k], bool) or it[k] < 0):
                errs.append("第 %d 筆 %s 要是 0 以上的整數" % (i + 1, k))
            elif t is not int and not isinstance(it[k], t):
                errs.append("第 %d 筆 %s 型別不對" % (i + 1, k))
        for k, t in OPTIONAL.items():
            if k in it and not isinstance(it[k], t):
                errs.append("第 %d 筆 %s 型別不對" % (i + 1, k))
        extra = set(it) - set(FIELDS) - set(OPTIONAL)
        if extra:
            errs.append("第 %d 筆有不認得的欄位 %s" % (i + 1, sorted(extra)))
        if not cut and not norm_text(it.get("text")) and not it.get("images") and not it.get("stickers"):
            errs.append("第 %d 筆沒有文字也沒有附圖：如果真的是空的，images 或 stickers 至少寫 1" % (i + 1))
    if errs:
        raise Bad("；".join(errs[:8]))


def add(tdir, shot, items, visible, known_notes=None):
    """一張圖的轉寫結果。檢查通過 → 存這張的結果、刪圖。回數字（含 all_known：整張都是已收過的）。"""
    rdir, man, s = _find_shot(tdir, shot)
    if s.get("status") != "pending":
        raise Bad("這張已經處理過（%s）" % s.get("status"))
    validate(items)
    if not isinstance(visible, int) or visible < 0:
        raise Bad("--visible 要是這張圖上看得到開頭的則數（0 以上整數）")
    captured = datetime.datetime.strptime(s["ts"], "%Y-%m-%dT%H:%M:%S")
    rows = []
    for it in items:
        cut = bool(it.get("cut_top") or it.get("cut_bottom"))
        when, guessed = norm_time(it.get("posted_at", ""), captured) if not cut else (None, False)
        r = {"author": norm_text(it.get("author")), "posted_at": when, "posted_at_raw": it.get("posted_at", ""),
             "posted_at_guessed": guessed, "text": (it.get("text") or "").strip(), "truncated": bool(it.get("truncated")),
             "images": int(it.get("images") or 0), "stickers": int(it.get("stickers") or 0),
             "cut_top": bool(it.get("cut_top")), "cut_bottom": bool(it.get("cut_bottom"))}
        if not cut:
            r["key"] = note_key(r["author"], when or r["posted_at_raw"], r["text"], r["images"])
        rows.append(r)
    full = [r for r in rows if not r["cut_top"]]
    count_ok = len(full) == visible
    _dump(os.path.join(rdir, "%02d.items.json" % s["n"]), {"rows": rows, "count_ok": count_ok})
    try:
        os.remove(os.path.join(rdir, s["file"]))
    except OSError:
        pass
    s["status"] = "done"
    s["count_ok"] = count_ok
    _dump(os.path.join(rdir, "manifest.json"), man)
    keyed = [r for r in rows if "key" in r]
    known = known_notes if known_notes is not None else load_notes(tdir)
    all_known = bool(keyed) and all(any(_same(r, k) for k in known) for r in keyed)
    left = sum(1 for x in man["shots"] if x["status"] == "pending")
    return {"ok": True, "shot": shot, "notes": len(keyed), "cut": len(rows) - len(keyed), "count_ok": count_ok,
            "all_known": all_known, "pending_in_run": left,
            "next": ("整張都是已收過的記事：後面的張不用轉了，直接 notes finish" if all_known and left else
                     "notes pending 看下一張" if left else "這一輪轉完了：notes finish")}


SOURCE_SPLIT = SOURCE + "；這一則被切在兩張圖之間，由兩張拼起來"


def _join_overlap(a, b):
    """前一張看到的內文 a、後一張看到的內文 b：相鄰兩張重疊的那段只留一次（找 a 的結尾＝b 的開頭的最長重疊）。"""
    a, b = (a or "").rstrip(), (b or "").lstrip()
    if not a or not b:
        return a or b
    for k in range(min(len(a), len(b)), 0, -1):
        if a.endswith(b[:k]):
            return a + b[k:]
    return a + "\n" + b


def _merge_splits(per, rep):
    """同一輪相鄰兩張：前一張最後一則 cut_bottom、後一張第一則 cut_top → 同一則記事被切開，合成一則
    （作者與開頭取前一張、日期取後一張）。合不起來的中段碎片照實留成「被切開、未完整」，不默默丟掉。
    第一張開頭的 cut_top（上一輪的延續）與最後一張結尾的 cut_bottom（下一輪接續會再拍到）不算。"""
    def contains(x, frag, author=None):   # 完整的一則 x 涵蓋這段碎片（同作者時才算）
        f = norm_text(frag)
        return bool(f) and norm_text(x.get("text")).find(f) >= 0 and (not author or norm_text(x.get("author")) == norm_text(author))

    for i in range(len(per)):
        s, rows = per[i]
        if i + 1 < len(per) and rows and rows[-1].get("cut_bottom"):
            s2, rows2 = per[i + 1]
            if rows2 and rows2[0].get("cut_top"):
                a, b = rows[-1], rows2[0]
                # 真機實跑（2026-11-23）：每張頂端常是「前一則已經完整轉過」的尾巴（只有日期或表情列），
                # 不能拿來接前一張最後那則。所以兩種情況不配對：
                #   a 在後一張已經完整出現（重疊拍到了）；b 在前一張已被某則完整的涵蓋（是那一則的尾巴）
                if a.get("text") and any("key" in x and contains(x, a["text"], a.get("author")) for x in rows2):
                    continue
                if b.get("text") and any("key" in x and contains(x, b["text"]) for x in rows):
                    continue
                cap = datetime.datetime.strptime(s2["ts"], "%Y-%m-%dT%H:%M:%S")
                raw = b.get("posted_at_raw") or a.get("posted_at_raw") or ""
                when, guessed = norm_time(raw, cap) if raw else (None, False)
                m = {"author": a.get("author") or b.get("author") or "", "posted_at": when, "posted_at_raw": raw,
                     "posted_at_guessed": guessed, "text": _join_overlap(a.get("text"), b.get("text")),
                     "truncated": bool(a.get("truncated") or b.get("truncated")),
                     "images": max(a.get("images") or 0, b.get("images") or 0),
                     "stickers": max(a.get("stickers") or 0, b.get("stickers") or 0), "split": True}
                m["key"] = note_key(m["author"], when or raw, m["text"], m["images"])
                rows.pop()
                rows2[0] = m
                rep["split_merged"] += 1
    for i, (s, rows) in enumerate(per):
        for j, r in enumerate(rows):
            if "key" in r:
                continue
            first_top = i == 0 and j == 0 and r.get("cut_top")
            last_bottom = i == len(per) - 1 and j == len(rows) - 1 and r.get("cut_bottom")
            if first_top or last_bottom:
                continue
            # 只剩日期或表情列的尾巴（沒有內文也沒有作者）：那一則在別張已經轉過，不另記
            if not norm_text(r.get("text")) and not norm_text(r.get("author")):
                rep["tail_dropped"] = rep.get("tail_dropped", 0) + 1
                continue
            # 這一輪任何一張已經有這一則的完整版本（重疊拍到了）：碎片已被涵蓋，不另記
            if any("key" in x and contains(x, r.get("text"), r.get("author")) for _, rows_ in per for x in rows_):
                rep["split_covered"] = rep.get("split_covered", 0) + 1
                continue
            # 中段合不起來的碎片：照實留下看得到的部分，標「被切開、未完整」
            r.update({"truncated": True, "split_incomplete": True})
            r["key"] = note_key(r.get("author") or "", r.get("posted_at") or r.get("posted_at_raw") or "", r.get("text") or "",
                                r.get("images") or 0)
            rep["split_incomplete"] += 1


def finish(tdir, title=None):
    """把轉完的 run 併進 notes.jsonl：去重、重疊一致性、穩定編號；還沒轉的張（整張已收過之後的）標成重複、刪掉。
    回數字。run 的資料夾整個刪掉（裡面只剩轉寫暫存）。"""
    purge_expired(tdir)
    notes = load_notes(tdir)
    st = load_state(tdir)
    rep = {"runs": 0, "shots_done": 0, "shots_dup": 0, "new_notes": 0, "overlap_pairs": 0, "overlap_mismatch": 0,
           "count_mismatch_shots": 0, "edge_cut_dropped": 0, "split_merged": 0, "split_incomplete": 0}
    for rdir, man in runs(tdir):
        shots = man.get("shots", [])
        if any(s.get("status") == "pending" for s in shots) and not any(s.get("status") == "done" for s in shots):
            continue   # 一張都還沒轉：留著等轉（或過期）
        rep["runs"] += 1
        per = []
        for s in shots:
            if s.get("status") == "done":
                d = _load(os.path.join(rdir, "%02d.items.json" % s["n"]), {"rows": []})
                per.append((s, d.get("rows") or []))
                rep["shots_done"] += 1
                if not d.get("count_ok", True):
                    rep["count_mismatch_shots"] += 1
            elif s.get("status") == "pending":
                s["status"] = "dup"   # 前面已經整張都是收過的：不轉
                rep["shots_dup"] += 1
        _merge_splits(per, rep)
        flagged = set()
        for (sa, ra), (sb, rb) in zip(per, per[1:]):   # 相鄰兩張重疊：同一個鍵的全文要一致
            ka = {r["key"]: r for r in ra if "key" in r}
            for r in rb:
                if "key" in r and r["key"] in ka:
                    rep["overlap_pairs"] += 1
                    if norm_text(ka[r["key"]]["text"]) != norm_text(r["text"]):
                        rep["overlap_mismatch"] += 1
                        flagged.add(r["key"])
        if per and per[-1][1] and per[-1][1][-1].get("cut_bottom"):
            rep["edge_cut_dropped"] += 1   # 最後一張底部被切到的那一則：下次接續擷取時會收到
        for s, rows in per:
            for r in rows:
                if "key" not in r:
                    continue
                cand = dict(r, run=man["run"], shot=s["n"], source=SOURCE_SPLIT if r.get("split") else SOURCE, needs_review=r["key"] in flagged or not s.get("count_ok", True),
                            first_seen=time.strftime("%Y-%m-%d"))
                for k in ("cut_top", "cut_bottom"):
                    cand.pop(k, None)
                old = next((n for n in notes if _same(n, cand)), None)
                if old is None:
                    notes.append(cand)
                    rep["new_notes"] += 1
                elif old.get("run") == man["run"]:   # 同一輪相鄰兩張的重疊：第二份只用來核對
                    if cand["needs_review"]:
                        old["needs_review"] = True
                    if norm_text(old["text"]) != norm_text(cand["text"]):
                        old["needs_review"] = True
                        old.setdefault("alt_text", cand["text"])
        # 記事本狀態：停止原因是上限 → 更舊的還沒擷取，記下接續要先捲過幾格、目前收到哪一天
        dated = [r["posted_at"] for _, rows in per for r in rows if r.get("posted_at")]
        st["last_run"] = {"run": man["run"], "stop": man.get("stop"), "shots": len(shots), "date": man["created"][:10]}
        if man.get("stop") in ("cap", "timeout", "user_input"):
            st["older_not_captured"] = True
            # 接續時先捲到「最後一張之前一步」的位置，讓新一輪的第一張和上一輪最後一張重疊
            st["resume_skip_notches"] = round(max(0.0, float(man.get("notches_total") or 0) - float(man.get("last_step") or 1)), 3)
            if dated:
                st["oldest_captured"] = min(dated)[:10]
        elif man.get("stop") == "bottom":
            st["older_not_captured"] = False
            st.pop("resume_skip_notches", None)
        shutil.rmtree(rdir, ignore_errors=True)
    try:
        os.rmdir(shots_root(tdir))   # 都轉完了：連空的暫存資料夾也拿掉（還有別的 run 就刪不掉，照留）
    except OSError:
        pass
    number(notes)
    save_notes(tdir, notes)
    st["notes"] = len(notes)
    st["truncated"] = sum(1 for n in notes if n.get("truncated"))
    st["needs_review"] = sum(1 for n in notes if n.get("needs_review"))
    st["note_images"] = sum(n.get("images", 0) for n in notes)
    st["updated"] = time.strftime("%Y-%m-%d %H:%M")
    save_state(tdir, st)
    rep.update({"notes_total": len(notes), "truncated": st["truncated"], "needs_review": st["needs_review"],
                "older_not_captured": bool(st.get("older_not_captured")), "oldest_captured": st.get("oldest_captured"),
                "resume_skip_notches": st.get("resume_skip_notches")})
    return rep


def discard(tdir, reason="not-notes-tab"):
    """還沒轉寫的圖整輪丟掉（例如第一張就不是記事本清單）：刪圖、記下原因；已轉好的不動。回丟掉的張數。"""
    n = 0
    for rdir, man in runs(tdir):
        p = [s for s in man.get("shots", []) if s.get("status") == "pending"]
        if p and not any(s.get("status") == "done" for s in man.get("shots", [])):
            n += len(p)
            st = load_state(tdir)
            st["last_run"] = {"run": man["run"], "stop": reason, "shots": len(man.get("shots", [])), "date": man["created"][:10]}
            save_state(tdir, st)
            shutil.rmtree(rdir, ignore_errors=True)
    try:
        os.rmdir(shots_root(tdir))
    except OSError:
        pass
    return n


def number(notes):
    nxt = max([n.get("no") or 0 for n in notes] + [0]) + 1
    for n in sorted(notes, key=lambda x: (x.get("posted_at") or "", x.get("key"))):
        if not n.get("no"):
            n["no"] = nxt
            nxt += 1


# ---------------------------------------------------------------- 給人看的
def overview_line(tdir):
    """總覽裡記事本那一行（沒有收過 → None）。"""
    st = load_state(tdir)
    if not st.get("notes") and not st.get("last_run") and not st.get("expired_shots"):
        return None
    parts = ["%d 則（由 AI 看圖轉寫；原文以 LINE 為準）" % st.get("notes", 0)]
    if st.get("truncated"):
        parts.append("內文未完整 %d 則" % st["truncated"])
    if st.get("needs_review"):
        parts.append("待人看 %d 則" % st["needs_review"])
    if st.get("older_not_captured"):
        parts.append("只收到 %s 以後的，更舊的還沒擷取" % (st.get("oldest_captured") or "最近幾則"))
    if st.get("expired_shots"):
        parts.append("有一段擷取後沒轉完（%d 張圖已過期清掉），那段未收" % st["expired_shots"])
    parts.append("留言不收")
    return "；".join(parts)


def missing_section(tdir, notes=None):
    """未收清單裡的「記事本」一節（沒有要列的 → []）。"""
    st = load_state(tdir)
    notes = notes if notes is not None else load_notes(tdir)
    if not notes and not st.get("last_run") and not st.get("expired_shots"):
        return []
    L = ["## 記事本", "", "記事本的內文是 AI 看圖轉寫的，可能有錯字，原文以 LINE 為準。記事本底下的留言不收。", ""]
    if st.get("older_not_captured"):
        L += ["- 記事本只收到 %s 以後的，更舊的還沒擷取（下次同步會接著往下補）。" % (st.get("oldest_captured") or "最近幾則"), ""]
    if st.get("expired_shots"):
        L += ["- 有一段記事本擷取後沒轉完（%d 張圖過期清掉），那一段未收。" % st["expired_shots"], ""]
    trunc = [n for n in notes if n.get("truncated")]
    why = lambda n: "被切開、未完整" if n.get("split_incomplete") else "內文未完整"
    review = [n for n in notes if n.get("needs_review")]
    imgs = [n for n in notes if n.get("images")]
    # 顯示用原文（只把換行與連續空白收成一格，不做全形半形轉換）；編號寫成「記事 N」，和附件的 #N 分開
    show = lambda t: re.sub(r"\s+", " ", t or "").strip()
    row = lambda n, extra: "| 記事 %d | %s | %s | %s | %s |" % (n["no"], (n.get("posted_at") or n.get("posted_at_raw") or "")[:16],
                                                             n.get("author") or "", (show(n.get("text"))[:30] or "（無文字）"), extra)
    if trunc:
        L += ["### 內文未完整（%d）" % len(trunc), "", "畫面上只顯示前段（要按「顯示更多」才看得到全文，工具不按）；或這一則被切在兩張圖之間、拼不起來。", "",
              "| 編號 | 時間 | 作者 | 開頭 | 說明 |", "|---|---|---|---|---|"] + [row(n, why(n)) for n in trunc] + [""]
    if imgs:
        L += ["### 記事本內的附圖（%d 則，共 %d 張）" % (len(imgs), sum(n["images"] for n in imgs)), "",
              "照片預設不收（和聊天室的照片同一個規則）。", "",
              "| 編號 | 時間 | 作者 | 開頭 | 附圖 |", "|---|---|---|---|---|"] + [row(n, "%d 張" % n["images"]) for n in imgs] + [""]
    if review:
        L += ["### 待人看（%d）" % len(review), "", "同一則在相鄰兩張圖上轉出來的文字不一樣，或那張圖的則數對不上：請對照 LINE 原文。", "",
              "| 編號 | 時間 | 作者 | 開頭 | 說明 |", "|---|---|---|---|---|"] + [row(n, "轉寫不一致" if n.get("alt_text") else "則數對不上") for n in review] + [""]
    return L
