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
v = gn.norm_time("5月15日 上午 12:23", cap)
check("1 上午 12 點 → 00 點（不是中午）", v == ("2026-05-15 00:23", True), v)

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
          {"cut_bottom": True, "author": "Cat", "text": "第三則的全文", "truncated": False, "images": 0}]
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

    # 3d 被切在兩張之間的同一則：合成一則（作者與開頭取前一張、日期取後一張、重疊只留一次）；中段碎片標被切開
    rdir6 = make_run(tdir, "20261123-140000", 3, stop="bottom")
    gn.add(tdir, "20261123-140000/01", [N("Hal", "11/23 06:00", "完整的一則"),
                                         {"author": "Ivy", "text": "附很多照片的一則，開頭在這裡", "truncated": False, "images": 3, "cut_bottom": True}], 2)
    gn.add(tdir, "20261123-140000/02", [{"posted_at": "11/22 21:00", "text": "開頭在這裡", "truncated": False, "images": 6, "cut_top": True},
                                         {"author": "Jo", "text": "中段被切開又沒有下一段", "truncated": False, "images": 0, "cut_bottom": True}], 1)
    gn.add(tdir, "20261123-140000/03", [N("Kay", "11/22 20:00", "第三張的完整一則")], 1)
    f = gn.finish(tdir)
    ns = gn.load_notes(tdir)
    ivy = next((n for n in ns if n["author"] == "Ivy"), None)
    check("3d 被切開的同一則合成一則：作者取前一張、日期取後一張、重疊只留一次、附圖取多的那張",
          ivy and ivy["text"] == "附很多照片的一則，開頭在這裡" and ivy["posted_at"] == "2026-11-22 21:00" and ivy["images"] == 6
          and "兩張拼起來" in ivy["source"] and f["split_merged"] == 1, (ivy, f))
    jo = next((n for n in ns if n["author"] == "Jo"), None)
    check("3d 中段拼不起來的碎片：照實留下、標被切開未完整（不默默丟掉）", jo and jo["truncated"] and jo.get("split_incomplete")
          and f["split_incomplete"] == 1, (jo, f))
    check("3d 未收清單把拼不起來的那則寫成「被切開、未完整」", "被切開、未完整" in "\n".join(gn.missing_section(tdir)))

    # 3e 真機型態：每張頂端是前一則（已完整轉過）的尾巴；不可錯接到前一張最後那則
    make_run(tdir, "20261123-150000", 4, stop="bottom")
    gn.add(tdir, "20261123-150000/01", [N("Lu", "11/21 06:53", "完整的第一則，後面接很長的說明", truncated=True),
                                         {"author": "Lu", "text": "超市採買", "truncated": False, "images": 2, "cut_bottom": True}], 2)
    gn.add(tdir, "20261123-150000/02", [{"posted_at": "11/21 06:53", "text": "後面接很長的說明", "truncated": True, "images": 0, "cut_top": True},
                                         {"author": "Lu", "text": "超市採買", "truncated": False, "images": 7, "cut_bottom": True}], 1)
    gn.add(tdir, "20261123-150000/03", [{"posted_at": "11/21 06:53", "text": "", "truncated": False, "images": 0, "cut_top": True},
                                         N("Lu", "11/20 18:50", "超市採買", images=7),
                                         {"author": "Lu", "text": "課程說明", "truncated": False, "images": 2, "cut_bottom": True}], 2)
    gn.add(tdir, "20261123-150000/04", [{"posted_at": "11/19 12:55", "text": "", "truncated": False, "images": 2, "cut_top": True},
                                         N("Lu", "11/19 12:53", "週末行程", images=6)], 1)
    before = len(gn.load_notes(tdir))
    f = gn.finish(tdir)
    ns = [n for n in gn.load_notes(tdir) if n.get("run") == "20261123-150000"]
    texts = sorted(n["text"] for n in ns)
    doc = next((n for n in ns if n["text"] == "課程說明"), None)
    check("3e 尾巴不錯接：四則（第一則、超市採買、課程、週末），超市採買只有一則、日期正確",
          texts == sorted(["完整的第一則，後面接很長的說明", "超市採買", "課程說明", "週末行程"])
          and next(n for n in ns if n["text"] == "超市採買")["posted_at"] == "2026-11-20 18:50", (texts, f))
    check("3e 被切開的那則接上後一張的日期（尾巴只有日期也能接）", doc and doc["posted_at"] == "2026-11-19 12:55" and doc.get("split"), doc)
    check("3e 只有日期的尾巴碎片被丟掉、已有完整版的碎片被涵蓋", f.get("tail_dropped", 0) >= 1 and f.get("split_covered", 0) >= 2
          and f["split_incomplete"] == 0, f)

    # 3f 只有圖的記事：開頭在前一張被切到、下一張整則完整 → 前一張的碎片算已涵蓋，不另記一則
    make_run(tdir, "20261123-160000", 2, stop="bottom")
    gn.add(tdir, "20261123-160000/01", [N("Mo", "11/18 10:00", "有字的一則"), {"author": "Mo", "text": "", "truncated": False, "images": 1, "cut_bottom": True}], 2)
    gn.add(tdir, "20261123-160000/02", [N("Mo", "11/17 06:33", "", images=1)], 1)
    f = gn.finish(tdir)
    ns = [n for n in gn.load_notes(tdir) if n.get("run") == "20261123-160000"]
    check("3f 只有圖的記事不會多出一則「被切開」", len(ns) == 2 and f["split_incomplete"] == 0 and f.get("split_covered", 0) >= 1, (len(ns), f))

    # 3g 規則修正後，已收的記事日期由原字串重算（舊資料一起更正）
    ns = gn.load_notes(tdir)
    ns[0]["posted_at_raw"], ns[0]["posted_at"], ns[0]["first_seen"] = "5月15日 上午 12:23", "2026-05-15 12:23", "2026-11-23"
    gn.save_notes(tdir, ns)
    gn.finish(tdir)
    fixed = next(n for n in gn.load_notes(tdir) if n.get("posted_at_raw") == "5月15日 上午 12:23")
    check("3g finish 由原字串重算已收記事的日期（上午 12:23 → 00:23）", fixed["posted_at"] == "2026-05-15 00:23", fixed.get("posted_at"))

    # 5 過期
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 25 * 3600))
    rdir5 = make_run(tdir, "20261122-090000", 2, created=old)
    check("5 超過 24 小時沒轉的圖：pending 時清掉、記進狀態", gn.pending(tdir) == [] and not os.path.exists(rdir5)
          and gn.load_state(tdir).get("expired_shots") == 2, gn.load_state(tdir))

    # 6 給人看的
    line = gn.overview_line(tdir)
    check("6 總覽那一行：AI 轉寫、內文未完整、待人看、過期那段、留言不收",
          all(x in line for x in ("由 AI 看圖轉寫；原文以 LINE 為準", "內文未完整 3 則", "附圖", "照片預設不收", "待人看", "未收", "留言不收")), line)
    sec = "\n".join(gn.missing_section(tdir))
    check("6 未收清單記事本一節：來源說明、留言不收、內文未完整、附圖照片規則、待人看",
          all(x in sec for x in ("AI 看圖轉寫", "留言不收", "### 內文未完整（3）", "記事本內的附圖", "照片預設不收", "### 待人看")), sec[:400])
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
    rd = os.path.join(tdir, "files", "記事本.md")
    rmd = open(rd, encoding="utf-8").read() if os.path.exists(rd) else ""
    check("7 人讀的記事本.md：說明、則數、每則時間作者全文、未完整與附圖的註記", "AI 看圖轉寫" in rmd and "共 1 則" in rmd
          and "## 2026-11-20 10:00 · Ann" in rmd and "記事內容只進收集資料夾：全形冒號……" in rmd
          and "內文未完整" in rmd and "附圖 1 張" in rmd, rmd[:400])
    check("7 記事本.md 標題不帶「記事 N」序號、沒有「拼起來」的過程註記", "## 記事 " not in rmd and "拼起來" not in rmd, rmd[:400])
    check("7 記事本.md 第一行連回收集總覽（相對路徑）", rmd.splitlines()[0] == "[← 回收集總覽](<../../../收集總覽.md>)", rmd[:120])
    check("7 總覽的記事本那一行連到記事本.md", "[記事本](line/%s/files/記事本.md)" % tid in ov, ov[-400:])
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

# 8 記事本.md 的範圍說明與只有照片的記事（合成資料；不借真實內容）
rd8 = tempfile.mkdtemp(prefix="ne-notes-render-")
try:
    t8 = os.path.join(rd8, "line", "abc123")
    os.makedirs(t8)
    gn.save_notes(t8, [
        {"no": 1, "author": "Ann", "posted_at": "2026-03-02 09:00", "text": "測試甲", "images": 0},
        {"no": 2, "author": "Bob", "posted_at": "2026-05-06 18:30", "text": "", "images": 3},
        {"no": 3, "author": "Ann", "text": "被切開的測試乙", "split_incomplete": True, "truncated": True},
        {"no": 4, "author": "Bob", "posted_at": "2026-04-01 12:00", "text": "測試丙", "images": 2, "split": True},
    ])
    gn.save_state(t8, {"older_not_captured": True, "oldest_captured": "2026-03-02", "notes": 4})
    r8 = gn.render_notes(t8, "測試聊天室")
    check("8 範圍講事實：有日期的最舊～最新、最下面那則被切到看不到時間、更舊的還沒擷取；不寫「只收到某日以後的」",
          "有日期的從 2026-03-02 到 2026-05-06" in r8 and "最舊的一則（最下面）被切在畫面邊緣，時間看不到" in r8
          and "更舊的還沒擷取，下次同步會接著往下補" in r8 and "以後的" not in r8, r8[:600])
    heads = [l for l in r8.splitlines() if l.startswith("## ")]
    check("8 標題是「時間 · 作者」、由新到舊、時間不明的排最後",
          heads == ["## 2026-05-06 18:30 · Bob", "## 2026-04-01 12:00 · Bob", "## 2026-03-02 09:00 · Ann", "## 時間不明 · Ann"], heads)
    check("8 只有照片：一行「只有 3 張照片」，不寫「（沒有文字）」也不再另列附圖", "只有 3 張照片（照片預設不收）" in r8
          and "（沒有文字）" not in r8 and r8.count("附圖") == 1 and "附圖 2 張" in r8, r8)
    check("8 拼起來的不註記；沒拼完整的照樣註記", "拼起來" not in r8 and "沒能拼完整" in r8, r8)
    ms8 = "\n".join(gn.missing_section(t8))
    check("8 未收清單的範圍句同一套事實；仍用穩定編號「記事 N」引用", "最舊的一則（最下面）被切在畫面邊緣" in ms8 and "以後的" not in ms8
          and "| 記事 3 |" in ms8, ms8[:500])
    ov8 = gn.overview_line(t8)
    check("8 總覽一行：短版事實（不寫日期範圍與補抓說明）", "最舊的一則（最下面）被切在畫面邊緣，時間看不到" in ov8 and "更舊的還沒擷取" in ov8
          and "以後的" not in ov8 and "下次同步" not in ov8, ov8)
    dst8 = os.path.join(rd8, "elsewhere", "dest")
    gn.write_readable(t8, "測試聊天室", dst8)
    dmd = open(os.path.join(dst8, "files", "記事本.md"), encoding="utf-8").read()
    check("8 dest 那份的回總覽連結指得到真的總覽（依它自己的位置算）",
          os.path.normpath(os.path.join(dst8, "files", dmd.splitlines()[0].split("(<")[1].rstrip(">)"))) == os.path.normpath(os.path.join(rd8, "收集總覽.md")),
          dmd.splitlines()[0])
    t9 = os.path.join(rd8, "line", "empty1")
    os.makedirs(t9)
    gn.save_notes(t9, [])
    gn.save_state(t9, {"last_run": {"run": "r", "stop": "bottom", "shots": 1}, "older_not_captured": False, "notes": 0, "updated": "2026-11-23 11:09"})
    r9 = gn.render_notes(t9, "空的聊天室")
    check("9 空的記事本：記事本.md 直接說是空的，不寫「共 0 則」、不寫 AI 轉寫的說明", "記事本目前是空的" in r9 and "共 0 則" not in r9
          and "AI 看圖轉寫" not in r9 and r9.splitlines()[0].startswith("[← 回收集總覽]"), r9)
    check("9 空的記事本：總覽一行寫「空的」、不寫「0 則（由 AI 看圖轉寫）」", gn.overview_line(t9) == "空的（2026-11-23 看過，還沒有人新增記事）", gn.overview_line(t9))
    check("9 空的記事本：未收清單不另開記事本一節", gn.missing_section(t9) == [], gn.missing_section(t9))
    gn.save_state(t9, {"last_run": {"run": "r", "stop": "cap", "shots": 15}, "older_not_captured": True, "notes": 0})
    check("9 停在上限、0 則（不是確定空的）：不說「空的」", "空的" not in (gn.overview_line(t9) or "") and "空的" not in gn.render_notes(t9, "x"), gn.overview_line(t9))
    # 10 真機型態（2026-11-23 第三個聊天室）：只有圖的長記事連續三張都看得到開頭、下一則有字的也連續兩張看得到開頭；
    #    每張頂端是上一則只剩日期的尾巴 → 不可拼出假的記事
    t10 = os.path.join(rd8, "line", "long1")
    os.makedirs(t10)
    make_run(t10, "20261123-170000", 6, stop="cap")
    cb = lambda a, t, im=0, tr=False: {"author": a, "text": t, "truncated": tr, "images": im, "cut_bottom": True}
    ct = lambda w, t, im=0, tr=False: {"author": "", "posted_at": w, "text": t, "truncated": tr, "images": im, "cut_top": True}
    gn.add(t10, "20261123-170000/01", [N("Xu", "3月9日 下午 7:41", "很長的說明甲乙", truncated=True), cb("Xu", "", 1)], 2)
    gn.add(t10, "20261123-170000/02", [ct("3月9日 下午 7:41", "說明甲乙", tr=True), cb("Xu", "", 1)], 1)
    gn.add(t10, "20261123-170000/03", [ct("3月9日 下午 7:41", "", tr=True), cb("Xu", "", 1)], 1)
    gn.add(t10, "20261123-170000/04", [ct("2月6日 上午 11:05", "", 1), cb("Xu", "第三則的開頭")], 1)
    gn.add(t10, "20261123-170000/05", [ct("2月6日 上午 11:05", "", 1), cb("Xu", "第三則的開頭，後面更多", 2, True)], 1)
    gn.add(t10, "20261123-170000/06", [ct("2025.11.30 上午 8:12", "第三則的開頭，後面更多", 2, True)], 0)
    f10 = gn.finish(t10)
    n10 = sorted(((n.get("posted_at") or "")[:10], n.get("text"), n.get("images")) for n in gn.load_notes(t10))
    check("10 跨三張以上的記事不拼出假的：三則（3/9 有字、2/6 只有圖、2025-11-30 有字兩張圖），沒有被切開的碎片",
          n10 == [("2025-11-30", "第三則的開頭，後面更多", 2), ("2026-02-06", "", 1), ("2026-03-09", "很長的說明甲乙", 0)]
          and f10["split_incomplete"] == 0, (n10, f10))
finally:
    shutil.rmtree(rd8, ignore_errors=True)

print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
