#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gather（聊天收集）測試——全部用合成資料（純 stdlib、無視窗、無網路）。

  1 解析：日期行、多行訊息、貼圖/照片佔位、收回訊息、同一分鐘的重複訊息、手機匯出（上午/下午、Tab 分隔、檔頭）
  2 合併：把一份合成紀錄隨機切成互相重疊的多份匯出，依任意順序合併，結果必須等於原檔（含同分鐘重複）
  3 缺口：不重疊、較晚的匯出 → append-gap 並記下缺口；重疊卻對不上 → conflict、不亂合
  4 CLI：LINE 不在白名單 → 拒；import → 存檔、--dest 寫 context-gather 格式（每日 md、message_id、_state、_meta）；
         status 在起始時間早於已存最早訊息時警告
"""
import json, os, random, re, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
sys.path.insert(0, SC)
import gather  # noqa: E402
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


DESKTOP = """2026.11.01 星期日
09:00 Ann 早安
09:00 Ann 早安
09:05 Bob [貼圖]
09:07 Cat 第一行
這是續行
最後一行
2026.11.02 星期一
10:00 Bob Ann已收回訊息
10:01 Ann [照片]
"""
ms = gather.parse_line_export(DESKTOP)
check("1 解析出 6 則訊息", len(ms) == 6, len(ms))
check("1 同一分鐘的重複訊息兩則都保留", [m["text"] for m in ms[:2]] == ["早安", "早安"])
check("1 多行訊息併成一則", ms[3]["text"] == "第一行\n這是續行\n最後一行", ms[3]["text"])
check("1 貼圖/照片是附件佔位、收回訊息另外標", ms[2]["kind"] == "attachment" and ms[5]["kind"] == "attachment"
      and ms[4]["kind"] == "unsent", [m["kind"] for m in ms])
check("1 日期換成 YYYY-MM-DD", ms[0]["date"] == "2026-11-01" and ms[4]["date"] == "2026-11-02")
MOBILE = "[LINE] 與測試群組的聊天記錄\n儲存日期：2026/11/03 12:00\n\n2026/10/30（四）\n上午09:15\tAnn\t手機那邊的舊訊息\n下午01:05\tBob\t下午好\n"
mm = gather.parse_line_export(MOBILE)
check("1 手機匯出：略過檔頭、上午/下午換成 24 小時、Tab 分隔", len(mm) == 2 and mm[0]["time"] == "09:15"
      and mm[1]["time"] == "13:05" and mm[0]["sender"] == "Ann" and mm[0]["date"] == "2026-10-30", mm)

# ---- 2 合併性質 ----
rnd = random.Random(7)
full = []
for d in range(1, 9):
    for i in range(rnd.randint(20, 45)):
        hh, mi = 8 + i // 6, (i % 6) * 10
        txt = rnd.choice(["收到", "好", "明天見", "哈哈", "ok", "早安"])       # 大量重複句子：考驗不可集合去重
        full.append({"date": "2026-11-%02d" % d, "time": "%02d:%02d" % (hh, mi), "sender": rnd.choice(["Ann", "Bob", "Cat"]),
                     "text": txt, "kind": "text"})
full += [dict(full[-1]), dict(full[-1])]   # 同一分鐘、同一人、同一句的三連發
keys = [gather.key(m) for m in full]
ok_all = True
for trial in range(30):
    n = len(full); cuts = []
    a = 0
    while a < n:
        b = min(n, a + rnd.randint(40, 120))
        start = max(0, a - rnd.randint(gather.ANCHOR + 5, 60)) if a else 0      # 每段與前一段重疊 ≥ 35 則
        cuts.append(full[start:b]); a = b
    order = cuts[:]
    # 隨機順序：先放一段中間的，再往兩邊長（模擬「先原樣匯出、後來往上捲／再過幾天同步」）
    mid = rnd.randrange(len(order)); seq = [order[mid]]
    left, right = mid - 1, mid + 1
    while left >= 0 or right < len(order):
        if right < len(order) and (left < 0 or rnd.random() < 0.5):
            seq.append(order[right]); right += 1
        else:
            seq.append(order[left]); left -= 1
    stored = []
    for part in seq:
        stored, info = gather.merge(stored, part)
        if info["mode"] in ("conflict", "append-gap", "prepend-gap"):
            ok_all = False; break
    if [gather.key(m) for m in stored] != keys:
        ok_all = False
    if not ok_all:
        break
check("2 30 次隨機重疊切段、任意順序合併 → 結果等於原檔（含同分鐘三連發）", ok_all, "trial %d" % trial)
ids = [m["id"] for m in gather.assign_ids([dict(m) for m in full])]
check("2 穩定 id 不重複（同分鐘重複訊息也分得開）", len(set(ids)) == len(ids))

# ---- 3 缺口與衝突 ----
part1, part3 = full[:50], full[120:170]
merged, info = gather.merge(part1, part3)
check("3 不重疊、較晚的匯出 → append-gap 並記下缺口", info["mode"] == "append-gap" and info["gap"] and len(merged) == 100, info)
alt = [dict(m) for m in full[:80]]
for m in alt[20:60]:
    m["text"] = m["text"] + "（編輯過）"
merged, info = gather.merge(full[:80], alt[10:70])
check("3 重疊卻對不上 → conflict，保留已存、不亂合", info["mode"] == "conflict" and len(merged) == 80, info)

# ---- 4 CLI ----
home = tempfile.mkdtemp(prefix="ne-gather-")
env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
json.dump({"chat": {"whitelist": ["測試群組"]}}, open(os.path.join(home, "policy.json"), "w", encoding="utf-8"), ensure_ascii=False)


def g(*a):
    p = subprocess.run([sys.executable, os.path.join(SC, "gather.py")] + list(a), capture_output=True, text=True,
                       encoding="utf-8", env=env)
    try:
        return json.loads(p.stdout), p.returncode
    except Exception:
        return {"raw": p.stdout + p.stderr}, p.returncode


o, rc = g("add", "--service", "line", "--chat", "別的群組", "--since", "2026-10-01")
check("4 LINE 聊天室不在白名單 → 拒", rc == 3 and o.get("refused"), o)
dest = os.path.join(home, "cg", "data", "projects", "demo", "cache", "line", "G1")
o, rc = g("add", "--service", "line", "--chat", "測試群組", "--since", "2026-10-01", "--dest", dest, "--label", "測試", "--slug", "demo")
tid = o.get("target")
check("4 白名單裡的聊天室 → 登記成功", rc == 0 and tid, o)
src = os.path.join(home, "export.txt"); open(src, "w", encoding="utf-8").write(DESKTOP)
o, rc = g("import", "--target", tid, src)
check("4 import → 6 則存入", rc == 0 and o.get("total") == 6, o)
day = os.path.join(dest, "2026-11-01.md")
txt = open(day, encoding="utf-8").read() if os.path.exists(day) else ""
check("4 --dest 寫成 context-gather 的每日 md（frontmatter、### [時間] **人** [message_id: …]）",
      txt.startswith("---\nproject: demo\nsource: line\n") and re.search(r"### \[09:00\] \*\*Ann\*\* \[message_id: [0-9a-f]{16}\]", txt), txt[:200])
check("4 --dest 有 _state.json 與 _meta.json", os.path.exists(os.path.join(dest, "_state.json")) and
      json.load(open(os.path.join(dest, "_meta.json"), encoding="utf-8")).get("members") == ["Ann", "Bob", "Cat"])
o, rc = g("status")
row = (o.get("targets") or [{}])[0]
check("4 status：起始時間（10-01）早於已存最早（11-01）→ 警告", "warning" in row and row.get("count") == 6, row)
open(src, "w", encoding="utf-8").write(MOBILE)
o, rc = g("import", "--target", tid, src)
check("4 手機匯出補更早的歷史 → 往前接上（記缺口，因為 10-30 與 11-01 之間不連續）", rc == 0 and o.get("total") == 8
      and o.get("mode") == "prepend-gap", o)
o, rc = g("targets")
check("4 targets 不印聊天室名稱", "測試群組" not in json.dumps(o, ensure_ascii=False))
led = open(os.path.join(home, "gather-ledger.jsonl"), encoding="utf-8").read()
check("4 ledger 只記筆數、不含訊息內容與聊天室名稱", "早安" not in led and "測試群組" not in led)
shutil.rmtree(home, ignore_errors=True)
# 6 發話者名字含空白（LINE 桌面版用空白分隔）：學名字、舊資料＋新匯出合併不重複不報缺口
import copy  # noqa: E402
EXP1 = chr(10).join(['2026.11.03 星期二', '09:00 Amy Chen 早安', '09:01 Bob 早', '09:02 Amy Chen 今天開會', '09:03 Amy Chen 貼圖', '09:04 Bob 收到', '09:05 Amy Chen 會議紀錄.docx', '09:06 Li Wei Ming 大家好', '09:07 Li Wei Ming 我是新來的', '09:08 Li Wei Ming 請多指教', '09:09 Li Wei Ming 圖片', '09:10 Bob 歡迎']) + chr(10)
EXP2 = EXP1 + chr(10).join(['09:11 Amy Chen 補充一下', '09:12 Bob 好']) + chr(10)
old = gather.assign_ids(gather.parse_line_export(EXP1))      # 舊解析：名字被切開
check("6 舊解析確實把名字切開（前提）", any(m["sender"] == "Amy" for m in old) and not any(" " in m["sender"] for m in old))
fixed, names = gather.learn_split_names(copy.deepcopy(old))
check("6 學出多段名字（兩段與三段）", names >= {"Amy Chen", "Li Wei Ming"} and {m["sender"] for m in fixed} == {"Amy Chen", "Bob", "Li Wei Ming"}, names)
check("6 修正後內文不再帶名字的後半", next(m for m in fixed if m["time"] == "09:01" or m["time"] == "09:00")["text"] in ("早安", "早"))
check("6 單字名字的人不受影響（Bob 的訊息不動）", [m["text"] for m in fixed if m["sender"] == "Bob"] == ["早", "收到", "歡迎"])
again, n2 = gather.learn_split_names(copy.deepcopy(fixed))
check("6 重複學習不再變動（冪等）", [gather.key(m) for m in again] == [gather.key(m) for m in fixed] and not n2)
for label, stored in (("沒遷移：舊解析的資料", old), ("遷移後的資料", gather.assign_ids(fixed))):
    new = gather._apply_names(gather.parse_line_export(EXP2), gather.known_names(stored))
    merged, mi = gather.merge(copy.deepcopy(stored), new)
    merged = gather.assign_ids(gather.learn_split_names(merged)[0])
    check("6 %s＋新匯出：只加 2 則、不報缺口、不重複、名字正確" % label,
          mi["added"] == 2 and not mi.get("gap") and len(merged) == 13 and len({m["id"] for m in merged}) == 13
          and {m["sender"] for m in merged} == {"Amy Chen", "Bob", "Li Wei Ming"}, (mi, len(merged)))

print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
