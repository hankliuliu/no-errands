#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LINE 記事本轉寫端（純檔案、三平台；不讀圖、不開視窗）。用合成的 manifest 與轉寫 JSON：
  1. 日期時間：完整日期、沒有年份、今天／昨天、只有時間 → 正規化值＋保留原字串＋是否用推的
  2. add：欄位檢查、則數核對、通過就刪圖、整張已收過 → all_known
  3. finish：去重、相鄰兩張重疊的全文核對（不一致 → 待人看）、只有圖的記事也有一筆、穩定編號、run 資料夾刪掉
  4. 停在上限 → 「只收到某天以後的」＋接續要先捲過幾格；到底 → 清掉
  5. 沒轉完的圖超過 24 小時清掉、記進狀態
  6. 總覽那一行、未收清單的記事本一節（AI 轉寫來源、留言不收、內文未完整、附圖、待人看）
  7. CLI：gather.py notes pending／add／finish／status；未收清單與總覽有記事本
"""
import datetime, json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SC = os.path.join(os.path.dirname(HERE), "skills", "no-errands", "scripts")
sys.path.insert(0, SC)
import gather_notes as gn  # noqa: E402

R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


def make_run(tdir, run, n, stop="bottom", created=None, notches_total=40):
    rdir = os.path.join(tdir, "_notes_shots", run)
    os.makedirs(rdir, exist_ok=True)
    shots = []
    for i in range(1, n + 1):
        fn = "%02d.png" % i
        open(os.path.join(rdir, fn), "wb").write(b"png")
        shots.append({"n": i, "file": fn, "ts": "2026-11-23T10:00:00", "sha256": "x", "status": "pending"})
    man = {"run": run, "created": created or time.strftime("%Y-%m-%dT%H:%M:%S"), "shots": shots, "stop": stop,
           "notches_total": notches_total}
    json.dump(man, open(os.path.join(rdir, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return rdir


def N(author, when, text, truncated=False, images=0, **k):
    return dict({"author": author, "posted_at": when, "text": text, "truncated": truncated, "images": images}, **k)


cap = datetime.datetime(2026, 11, 23, 10, 0)
# 1 日期時間
v = gn.norm_time("2026.11.20 14:03", cap)
check("1 完整日期 → 正規化、不是推的", v == ("2026-11-20 14:03", False), v)
v = gn.norm_time("11/20（五） 下午 2:03", cap)
check("1 沒年份＋下午 → 取擷取當年、標推的", v == ("2026-11-20 14:03", True), v)
v = gn.norm_time("12/30 09:00", cap)
check("1 沒年份但比擷取時間晚 → 算前一年", v == ("2025-12-30 09:00", True), v)
v = gn.norm_time("昨天 08:15", cap)
check("1 昨天 → 相對擷取日、標推的", v == ("2026-11-22 08:15", True), v)
check("1 看不懂的寫法 → None（原字串照留）", gn.norm_time("上週", cap) == (None, False))

td = tempfile.mkdtemp(prefix="ne-notes-")
try:
    tdir = os.path.join(td, "line", "t1"); os.makedirs(tdir)
    # 2 add
    rdir = make_run(tdir, "20261123-100000", 3, stop="cap", notches_total=40)
    pend = gn.pending(tdir)
    check("2 pending：依編號列出三張", [p["shot"] for p in pend] == ["20261123-100000/01", "20261123-100000/02", "20261123-100000/03"], pend)
    for bad, why in (([{"author": "A"}], "缺欄位"), ([N("A", "11/20 10:00", "", images=0)], "沒文字也沒圖"),
                     ([N("A", "11/20 10:00", "x", images=-1)], "負數"), ("{}", "不是陣列"), ([dict(N("A", "x", "y"), extra=1)], "多欄位")):
        try:
            gn.add(tdir, "20261123-100000/01", bad, 1); ok = False
        except gn.Bad:
            ok = True
        check("2 add 拒絕：%s（圖還在）" % why, ok and os.path.exists(os.path.join(rdir, "01.png")))
    s1 = [N("Ann", "11/22 09:00", "第一則：明天的會議改到下午兩點，請大家準時"),
          N("Bob", "11/21 18:30", "第二則記事", truncated=True),
          N("Ann", "11/20 12:00", "", images=2),
          {"cut_bottom": True, "author": "Cat", "text": "第三則的上半", "truncated": False, "images": 0}]
    r = gn.add(tdir, "20261123-100000/01", s1, 4)   # 被下緣切到的那則開頭看得到，也算
    check("2 add 通過：3 則完整＋1 則被下緣切到（開頭可見，算進 visible）、則數對、圖刪掉", r["ok"] and r["notes"] == 3 and r["cut"] == 1 and r["count_ok"]
          and not os.path.exists(os.path.join(rdir, "01.png")), r)
    try:
        gn.add(tdir, "20261123-100000/01", s1, 4); again = False
    except gn.Bad:
        again = True
    check("2 同一張不能交兩次", again)
    s2 = [N("Ann", "11/20 12:00", "", images=2),   # 重疊：和上一張最後一則相同
          N("Cat", "11/19 08:00", "第三則的全文，上一張被切到了"),
          N("Dan", "11/18 07:00", "第四則")]
    r = gn.add(tdir, "20261123-100000/02", s2, 2)
    check("2 則數對不上（看到 3 則開頭卻說 2）→ count_ok false，照樣收", r["ok"] and r["count_ok"] is False, r)
    check("2 還沒收過任何記事 → all_known false", r["all_known"] is False)

    # 3 finish（第 3 張沒轉：前一張不是整張已收過，但這裡模擬 agent 決定停 → finish 把它標成重複）
    f = gn.finish(tdir)
    notes = gn.load_notes(tdir)
    check("3 finish：去重後 5 則（重疊的只算一次、被切的不算）", f["new_notes"] == 5 and len(notes) == 5, (f, len(notes)))
    check("3 只有圖的記事也有一筆（內文空、附圖 2）", any(n["text"] == "" and n["images"] == 2 for n in notes))
    check("3 每則有來源欄位（AI 看圖轉寫）與原字串", all(n["source"] == gn.SOURCE and n["posted_at_raw"] for n in notes))
    check("3 編號 1..5 不重複", sorted(n["no"] for n in notes) == [1, 2, 3, 4, 5])
    check("3 則數對不上那張的記事標待人看", any(n["needs_review"] for n in notes if n["author"] == "Dan"))
    check("3 沒轉的第 3 張算重複、run 資料夾整個刪掉", f["shots_dup"] == 1 and not os.path.exists(rdir), f)
    check("3 重疊比對 1 組、不一致 0", f["overlap_pairs"] == 1 and f["overlap_mismatch"] == 0, f)
    # 4 停在上限
    st = gn.load_state(tdir)
    check("4 停在上限 → 更舊的還沒擷取、收到 11-18 以後、接續先捲 39 格", st.get("older_not_captured") is True
          and st.get("oldest_captured") == "2026-11-18" and st.get("resume_skip_notches") == 39, st)

    # 3b 第二輪：重疊同一則但轉出的字不一樣 → 待人看；整張已收過 → all_known
    rdir2 = make_run(tdir, "20261123-110000", 3, stop="bottom")
    r = gn.add(tdir, "20261123-110000/01", [N("Eve", "11/23 09:30", "新的一則"), N("Fay", "11/23 08:00", "又一則，等一下比對")], 2)
    r = gn.add(tdir, "20261123-110000/02", [N("Fay", "11/23 08:00", "又一則，等一下比對"[:3] + "X")], 1)
    check("3b 內文開頭不同 → 鍵不同（不會被當同一則）", r["notes"] == 1)
    rdir3 = make_run(tdir, "20261123-120000", 2, stop="bottom")
    r = gn.add(tdir, "20261123-120000/01", [N("Ann", "2026.11.22 09:00", "第一則：明天的會議改到下午兩點，請大家準時")], 1)
    check("3b 整張都是已收過的（跨輪、日期寫法不同也認得）→ all_known、提示直接 finish", r["all_known"] and "finish" in r["next"], r)
    f = gn.finish(tdir)
    check("3b 第二輪到底 → 不再標「更舊的還沒擷取」", gn.load_state(tdir).get("older_not_captured") is False, gn.load_state(tdir))

    # 重疊不一致
    rdir4 = make_run(tdir, "20261123-130000", 2, stop="bottom")
    gn.add(tdir, "20261123-130000/01", [N("Gus", "11/23 07:00", "這一則在兩張圖上轉得不一樣喔喔")], 1)
    gn.add(tdir, "20261123-130000/02", [dict(N("Gus", "11/23 07:00", "這一則在兩張圖上轉得不一樣喔喔"), text="這一則在兩張圖上轉得不一樣喔喔")], 1)
    k1 = gn.note_key("Gus", "2026-11-23 07:00", "這一則在兩張圖上轉得不一樣喔喔", 0)
    # 直接改第二張的暫存讓全文不同（開頭 40 字相同、後面不同）
    p2 = os.path.join(rdir4, "02.items.json"); d2 = json.load(open(p2, encoding="utf-8"))
    d2["rows"][0]["text"] += "（多轉了一段）"; json.dump(d2, open(p2, "w", encoding="utf-8"), ensure_ascii=False)
    f = gn.finish(tdir)
    gus = next(n for n in gn.load_notes(tdir) if n["author"] == "Gus")
    check("3c 相鄰兩張同一則全文不一致 → 待人看、兩個版本都留", f["overlap_mismatch"] == 1 and gus["needs_review"] and gus.get("alt_text"), f)

    # 5 過期
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 25 * 3600))
    rdir5 = make_run(tdir, "20261122-090000", 2, created=old)
    check("5 超過 24 小時沒轉的圖：pending 時清掉、記進狀態", gn.pending(tdir) == [] and not os.path.exists(rdir5)
          and gn.load_state(tdir).get("expired_shots") == 2, gn.load_state(tdir))

    # 6 給人看的
    line = gn.overview_line(tdir)
    check("6 總覽那一行：AI 轉寫、內文未完整、待人看、過期那段、留言不收",
          all(x in line for x in ("由 AI 看圖轉寫；原文以 LINE 為準", "內文未完整 1 則", "待人看", "未收", "留言不收")), line)
    sec = "\n".join(gn.missing_section(tdir))
    check("6 未收清單記事本一節：來源說明、留言不收、內文未完整、附圖照片規則、待人看",
          all(x in sec for x in ("AI 看圖轉寫", "留言不收", "### 內文未完整（1）", "記事本內的附圖", "照片預設不收", "### 待人看")), sec[:400])
finally:
    shutil.rmtree(td, ignore_errors=True)

# 7 CLI
home = tempfile.mkdtemp(prefix="ne-notes-cli-")
try:
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    json.dump({"chat": {"whitelist": ["測試群組"]}}, open(os.path.join(home, "policy.json"), "w", encoding="utf-8"), ensure_ascii=False)

    def g(*a):
        p = subprocess.run([sys.executable, os.path.join(SC, "gather.py")] + list(a), capture_output=True, text=True, encoding="utf-8", env=env)
        try:
            return json.loads(p.stdout), p.returncode
        except Exception:
            return {"raw": (p.stdout + p.stderr)[-300:]}, p.returncode
    o, rc = g("add", "--service", "line", "--chat", "測試群組", "--since", "2026-10-01")
    tid = o.get("target")
    exp = os.path.join(home, "e.txt")
    open(exp, "w", encoding="utf-8").write("2026.11.01 星期日\n09:00 Ann 早\n09:01 Ann 圖片\n")
    g("import", "--target", tid, exp)
    tdir = os.path.join(home, "gather", "line", tid)
    make_run(tdir, "20261123-100000", 1, stop="bottom")
    o, rc = g("notes", "pending", "--target", tid)
    check("7 notes pending：1 張、附看圖規則", rc == 0 and o["pending"] == 1 and "不寫進回覆" in o["rule"] and o["first"]["path"].endswith("01.png"), o)
    o, rc = g("notes", "capture", "--target", tid)
    check("7 還有沒轉的圖時不准再擷取", rc == 3 and "沒轉寫" in o.get("error", ""), o)
    items7 = [N("Ann", "11/20 10:00", "記事內容只進收集資料夾：全形冒號……", truncated=True, images=1)]
    outside = os.path.join(home, "items.json")
    json.dump(items7, open(outside, "w", encoding="utf-8"), ensure_ascii=False)
    o, rc = g("notes", "add", "--target", tid, "--shot", "20261123-100000/01", "--visible", "1", "--file", outside)
    check("7 轉寫檔放在擷取資料夾以外 → 拒收、圖還在", rc == 2 and "擷取資料夾" in o.get("error", "")
          and os.path.exists(os.path.join(tdir, "_notes_shots", "20261123-100000", "01.png")), o)
    o, rc = g("notes", "pending", "--target", tid)
    jf = o["first"]["write_items_to"]
    json.dump(items7, open(jf, "w", encoding="utf-8"), ensure_ascii=False)
    o, rc = g("notes", "add", "--target", tid, "--shot", "20261123-100000/01", "--visible", "1", "--file", jf)
    check("7 轉寫檔放在 write_items_to → 收下，工具自己把它刪掉", rc == 0 and not os.path.exists(jf), (o, os.path.exists(jf)))
    check("7 notes add：通過、輸出只有數字（不含內文）", rc == 0 and o["notes"] == 1 and "記事內容" not in json.dumps(o, ensure_ascii=False), o)
    o, rc = g("notes", "finish", "--target", tid)
    check("7 notes finish：1 則、輸出不含內文", rc == 0 and o["notes_total"] == 1 and "記事內容" not in json.dumps(o, ensure_ascii=False), o)
    o, rc = g("notes", "status", "--target", tid)
    check("7 notes status：則數、內文未完整", rc == 0 and o["notes"] == 1 and o["truncated"] == 1 and o["pending"] == 0, o)
    md = open(os.path.join(tdir, "files", "未收清單.md"), encoding="utf-8").read()
    check("7 未收清單有記事本一節（放在附件之後）", "## 記事本" in md and "內文未完整" in md, md[:300])
    check("7 記事本用「記事 N」編號（不和附件的 #N 混）、顯示原文（不轉半形）", "| 記事 1 |" in md and "| #1 |" not in md.split("## 記事本")[1]
          and "全形冒號……" in md, md[-400:])
    check("7 轉完後連空的暫存資料夾也拿掉", not os.path.exists(os.path.join(tdir, "_notes_shots")))
    check("7 清單開頭：附件數字之後才說最後一節是記事本（數字不會被看成記事本的）", "）；最後一節是記事本。" in md and "還沒收下來的照片、影片和檔案（已收 0、未收 1）" in md and chr(1) not in md, md[:300])
    ov = open(os.path.join(home, "gather", "收集總覽.md"), encoding="utf-8").read()
    check("7 總覽：記事本一行、公告還沒收", "- 記事本：1 則（由 AI 看圖轉寫；原文以 LINE 為準）" in ov and "- 公告：還沒收" in ov, ov[-500:])
    lg = open(os.path.join(home, "gather-ledger.jsonl"), encoding="utf-8").read()
    check("7 ledger 只有數字（不含內文）", "notes-add" in lg and "記事內容" not in lg)
    o, rc = g("read", "--target", tid)
    check("7 read 回傳記事本（給要讀的 agent）", rc == 0 and len(o.get("notes") or []) == 1, o.get("notes"))
    make_run(tdir, "20261123-120000", 2, stop="bottom")
    o, rc = g("notes", "discard", "--target", tid)
    st = json.load(open(os.path.join(tdir, "notes_state.json"), encoding="utf-8"))
    check("7 notes discard（不是記事本分頁）：兩張圖刪掉、記下原因、已收的記事不動",
          rc == 0 and o["discarded_shots"] == 2 and not os.path.exists(os.path.join(tdir, "_notes_shots"))
          and st["last_run"]["stop"] == "not-notes-tab" and len([l for l in open(os.path.join(tdir, "notes.jsonl"), encoding="utf-8") if l.strip()]) == 1, (o, st.get("last_run")))
finally:
    shutil.rmtree(home, ignore_errors=True)

print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
