#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gather 的附件索引與未收清單（Sprint E）：合成資料、純 stdlib、三平台。

  1 分類：LINE 桌面版的佔位是不帶括號的單詞（圖片／影片／貼圖，真機格式）、手機的 [照片]、單行檔名；貼圖不列
  2 預設：照片影片不收、語音不收、附件黑名單（壓縮檔等）不收但都留紀錄；LINE 的檔案「工具做不到」（檔案分頁另存尚未完成）
  3 索引：重複同步不重複記、已收的不會變回未收；未收清單的筆數與分段
  4 Mattermost 下載（假的 GET）：收到、404→已過期、500→下載失敗、太大→超過大小上限；fetch 點名的就算依設定不收也補
  5 CLI：import 合成匯出 → status 有已收／未收（各原因）、files/未收清單.md、--dest 也放一份；再 import 一次不重複
"""
import json, os, re, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
sys.path.insert(0, SC)
import gather_items as gi  # noqa: E402

R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


M = lambda text, i=0: {"id": "m%02d" % i, "date": "2026-11-0%d" % (1 + i % 3), "time": "10:%02d" % i, "sender": "Ann", "text": text}
# 1 分類
check("1 圖片 → photo", gi.classify_line(M("圖片")) == {"kind": "photo", "name": None})
check("1 影片 → video", gi.classify_line(M("影片"))["kind"] == "video")
check("1 貼圖 → 不列（不是資訊）", gi.classify_line(M("貼圖")) is None)
check("1 手機匯出 [照片] → photo", gi.classify_line(M("[照片]"))["kind"] == "photo")
check("1 單行檔名 report.pdf → file 並記檔名", gi.classify_line(M("report.pdf")) == {"kind": "file", "name": "report.pdf"})
check("1 一般文字（提到 pdf 但不是檔名）→ 不列", gi.classify_line(M("請看附件\n明天開會")) is None
      and gi.classify_line(M("我把它存成 pdf 了")) is None)
check("1 單行 xx.jpg → photo", gi.classify_line(M("scan.jpg"))["kind"] == "photo")

# 2 預設狀態
t = {"service": "line", "label": "L"}
msgs = [M("圖片", 0), M("影片", 1), M("語音訊息", 2), M("report.pdf", 3), M("src.zip", 4), M("貼圖", 5), M("hello", 6)]
items = gi.from_line_messages(t, msgs)
by = {it["message_id"]: it for it in items}
check("2 只列附件（貼圖與文字不列）：5 筆", len(items) == 5, len(items))
check("2 照片、影片、語音：依設定不收", all(by[k]["reason"] == "依設定不收" for k in ("m00", "m01", "m02")))
check("2 壓縮檔：依設定不收、說明寫附件黑名單分類", by["m04"]["reason"] == "依設定不收" and "壓縮檔" in by["m04"]["note"], by["m04"])
check("2 LINE 文件檔：尚未收集（檔案分頁另存還在做；不是「工具做不到」，也不假裝收到）", by["m03"]["status"] == "missing" and by["m03"]["reason"] == "尚未收集")
check("2 每筆都有日期時間、傳送者、位置、穩定編號", all(it["date"] and it["time"] and it["sender"] and it["location"] == "message"
                                          and it["id"] == "m:" + it["message_id"] for it in items))
t2 = dict(t, photos=True)
check("2 開了 photos：照片不再是依設定不收", gi.from_line_messages(t2, [M("圖片")])[0]["reason"] != "依設定不收")

# 3 索引與清單
merged, added = gi.merge_items([], items)
merged[0]["status"], merged[0]["reason"] = "received", None
again, added2 = gi.merge_items(merged, gi.from_line_messages(t, msgs + [M("圖片", 7)]))
check("3 重複同步不重複記，只加新的那筆", added == 5 and added2 == 1 and len(again) == 6, (added, added2, len(again)))
check("3 已收的不會因重新同步變回未收", any(it["status"] == "received" for it in again))
s = gi.summary(again)
check("3 summary：已收 1／未收 5、各原因筆數", s["received"] == 1 and s["missing"] == 5 and s["missing_by_reason"].get("依設定不收") == 4
      and s["missing_by_reason"].get("尚未收集") == 1, s)
ctx_msgs = [dict(M("早安大家", 20), id="t1", date="2026-11-01", time="09:59", kind="text")] +     [dict(m, id=m["id"], kind="text") for m in msgs + [M("圖片", 7)]] + [dict(M("這是工地現場", 21), id="t2", date="2026-11-03", time="23:00", kind="text")]
md = gi.render_missing(again, "測試群", ctx_msgs, "abc123")
check("3 未收清單：標題是聊天室名稱、寫涵蓋範圍、開頭是白話（對 agent 怎麼說）", md.startswith("# 未收清單：測試群") and "涵蓋 2026-11-01" in md
      and "對 agent 說" in md and "<id>" not in md.split("---")[0], md[:300])
check("3 未收清單：檔案段在最前、照片影片在後；給人看的是「還沒收（檔案收集尚未完成）」", md.index("## 檔案") < md.index("## 照片與影片")
      and "還沒收（檔案收集尚未完成）" in md and "工具做不到" not in md, md[:600])
check("3 未收清單：短流水號、檔尾給 agent 用的指令填好 id", re.search(r"| #d+ |", md) and "--target abc123" in md.split("---")[-1])
check("3 未收清單：照片影片附前後文", "早安大家" in md or "這是工地現場" in md, md[-600:])
check("3 沒有未收的東西：只寫一句", "沒有未收的東西。" in gi.render_missing([], "空群", [], "x") and "對 agent" not in gi.render_missing([], "空群", [], "x"))
burst = [{"id": "m:b%d" % k, "message_id": "b%d" % k, "date": "2026-11-17", "time": "14:%02d" % (45 + k // 3), "sender": "Ann", "kind": "photo",
          "status": "missing", "reason": "依設定不收", "note": "x", "location": "message"} for k in range(7)]
bm = gi.render_missing(gi.number(burst), "B", [], "t")
check("3 同一個人幾分鐘內連續的 7 張照片 → 合成一列（照片 ×7）", "照片 ×7" in bm and bm.count("| Ann |") == 1 and "#1–#7" in bm, bm)
check("3 fetch 接受流水號", gi.select_fn(item="#3")(dict(burst[2])) and not gi.select_fn(item="#3")(dict(burst[1])))

# 4 Mattermost 下載（假 GET）
rows = [{"id": "p1", "date": "2026-11-02", "time": "09:00", "sender": "bob", "files": [
    {"id": "fa", "name": "spec.pdf", "size": 10, "mime_type": "application/pdf", "extension": "pdf"},
    {"id": "fb", "name": "gone.docx", "size": 10, "mime_type": "", "extension": "docx"},
    {"id": "fc", "name": "err.xlsx", "size": 10, "mime_type": "", "extension": "xlsx"},
    {"id": "fd", "name": "pic.png", "size": 10, "mime_type": "image/png", "extension": "png"},
    {"id": "fe", "name": "huge.pdf", "size": 10, "mime_type": "", "extension": "pdf"}]}]
td = tempfile.mkdtemp(prefix="ne-gi-")
try:
    mt = {"service": "mattermost", "label": "M"}
    its = gi.from_mm_files(mt, rows)
    resp = {"fa": (b"%PDF-1", None), "fb": (None, "http_404"), "fc": (None, "http_500"), "fd": (b"\x89PNG", None), "fe": (None, "too_large")}
    calls = []
    fetch = lambda fid: (calls.append(fid), resp[fid])[1]
    res = gi.download_mm(td, its, fetch)
    st = {it["file_id"]: it for it in its}
    check("4 文件檔收到：存成 <file_id>__<檔名>、記 sha256 與大小", st["fa"]["status"] == "received" and st["fa"]["path"] == "fa__spec.pdf"
          and os.path.exists(os.path.join(td, "files", "fa__spec.pdf")) and len(st["fa"]["sha256"]) == 64, st["fa"])
    check("4 404 → 已過期（不算失敗）", st["fb"]["reason"] == "已過期")
    check("4 500 → 下載失敗", st["fc"]["reason"] == "下載失敗" and res["failed"] == 1)
    check("4 太大 → 超過大小上限", st["fe"]["reason"] == "超過大小上限")
    check("4 照片預設不下載（沒發 GET）、記依設定不收", "fd" not in calls and st["fd"]["reason"] == "依設定不收", calls)
    res2 = gi.download_mm(td, its, fetch, select=gi.select_fn(kind="photo"), force=True)
    check("4 fetch --kind photo：點名的照片補到、轉成已收", st["fd"]["status"] == "received" and res2["received"] == 1)
    res3 = gi.download_mm(td, its, fetch, select=gi.select_fn(item="fb"), force=True)
    check("4 已過期的再補一次仍是已過期、寫明", st["fb"]["reason"] == "已過期" and "伺服器" in st["fb"]["note"])
    dest = os.path.join(td, "cg", "cache", "mattermost", "X")
    gi.write_outputs(td, dict(mt, dest=dest), its)
    check("4 --dest：files/未收清單.md、index.jsonl、已收原檔都放一份", all(os.path.exists(os.path.join(dest, "files", f))
          for f in ("未收清單.md", "index.jsonl", "fa__spec.pdf", "fd__pic.png")))
finally:
    shutil.rmtree(td, ignore_errors=True)

# 5 CLI：LINE 合成匯出
home = tempfile.mkdtemp(prefix="ne-gi-cli-")
try:
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    json.dump({"chat": {"whitelist": ["測試群組"]}}, open(os.path.join(home, "policy.json"), "w", encoding="utf-8"), ensure_ascii=False)

    def g(*a):
        p = subprocess.run([sys.executable, os.path.join(SC, "gather.py")] + list(a), capture_output=True, text=True, encoding="utf-8", env=env)
        try:
            return json.loads(p.stdout), p.returncode
        except Exception:
            return {"raw": (p.stdout + p.stderr)[-300:]}, p.returncode
    dest = os.path.join(home, "cg", "cache", "line", "G1")
    o, rc = g("add", "--service", "line", "--chat", "測試群組", "--since", "2026-10-01", "--dest", dest, "--label", "測試")
    tid = o.get("target")
    exp = os.path.join(home, "e.txt")
    open(exp, "w", encoding="utf-8").write("2026.11.01 星期日\n09:00 Ann 早\n09:01 Ann 圖片\n09:02 Bob 影片\n09:03 Bob 貼圖\n"
                                           "09:04 Ann 會議紀錄.docx\n09:05 Bob 素材.zip\n")
    o, rc = g("import", "--target", tid, exp)
    check("5 import：訊息存入、附件 4 筆進索引（貼圖不列）", rc == 0 and o.get("total") == 6 and o["items"]["missing"] == 4, o)
    o2, _ = g("import", "--target", tid, exp)
    check("5 再 import 一次：不重複記", o2["items"]["missing"] == 4 and o2["items"]["new_items"] == 0, o2.get("items"))
    st, _ = g("status")
    row = next(r for r in st["targets"] if r["target"] == tid)
    check("5 status：已收 0／未收 4（各原因）、記事本數與公告數欄位在", row["items"]["missing"] == 4
          and row["items"]["missing_by_reason"] == {"依設定不收": 3, "尚未收集": 1} and row["notes"] == 0 and row["announcements"] == 0, row)
    d = os.path.join(home, "gather", "line", tid, "files")
    check("5 本機與 --dest 都有 未收清單.md", os.path.exists(os.path.join(d, "未收清單.md")) and os.path.exists(os.path.join(dest, "files", "未收清單.md")))
    o, rc = g("config", "--target", tid, "--photos", "on")
    check("5 config --photos on", rc == 0 and o.get("photos") is True, o)
    o, rc = g("fetch", "--target", tid, "--kind", "file")
    check("5 LINE fetch：照實說還做不到、清單保留（不假裝補到）", rc == 3 and "檔案分頁" in o.get("error", "") and o.get("matched") == 2, o)
    o, rc = g("read", "--target", tid)
    check("5 read 一併回傳公告與記事本欄位", rc == 0 and "announcements" in o and o.get("notes") == [], list(o)[:5])
    # 6 收集總覽（本機、可以有名稱）與範圍說明
    ov = os.path.join(home, "gather", "收集總覽.md")
    txt = open(ov, encoding="utf-8").read() if os.path.exists(ov) else ""
    check("6 收集總覽：每個聊天室一段、用聊天室名稱當標題、連到未收清單、最上面寫怎麼補", "## 測試（LINE）" in txt
          and "未收清單.md" in txt and "對 agent 說" in txt.split("##")[0] and "已收 0／未收 4" in txt, txt[:300])
    sys.path.insert(0, SC)
    import gather as G  # noqa: E402
    tl = {"service": "line", "since": "2026-11-01 12:00"}
    check("6 LINE：匯出本身有早於起始時間的訊息 → 不警告", G.coverage(tl, {"export_earliest": "2026-10-20 09:00"}, "2026-11-01 12:30") == (None, None))
    w, n = G.coverage(tl, {"export_earliest": "2026-11-01 12:30"}, "2026-11-01 12:30")
    check("6 LINE：證明不了完整 → 警告", w and not n, (w, n))
    w, n = G.coverage(dict(tl, since="2000-01-01 00:00"), {}, "2026-11-03 16:49")
    check("6 LINE：起始時間是「全部」→ 只是說明、不是警告", not w and n and "2026-11-03 16:49" in n, (w, n))
    check("6 Mattermost：不出現 LINE 的範圍警告", G.coverage({"service": "mattermost", "since": "2026-11-22 00:00"}, {}, "2026-11-22 00:13") == (None, None))
finally:
    shutil.rmtree(home, ignore_errors=True)

print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
