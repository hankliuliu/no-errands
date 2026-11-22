#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_guard LINE profile 離線測試（純 stdlib、無視窗、三平台）：用仿 LINE 結構的合成 snapshot。"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "no-errands", "scripts"))
import chat_guard as cg  # noqa: E402

R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:200]))


def btn(name, ref):
    return {"role": "button", "name": name, "ref_id": ref, "available_actions": ["Click"]}


def snap(open_header=None, dup=False):
    items = [btn("Group A, 4人, 5, someone sent a sticker, 2026年11月21日", "@s:e1"),
             btn("Group A Ops, 3人, hi, 2026年11月20日", "@s:e2"),
             btn("Friend B, ok, 2026年11月19日", "@s:e3")] + [btn("Other %d, x, 2026年11月1日" % i, "@s:e%d" % (10 + i)) for i in range(5)]
    if dup:
        items.append(btn("Group A, 2人, hey, 2026年11月2日", "@s:e99"))
    right = {"role": "group", "children": [
        {"role": "group", "children": ([btn(open_header, "@s:e50")] if open_header else [btn("x", "@s:e50")]) +
         [btn(n, "@s:e%d" % (51 + i)) for i, n in enumerate(["關閉提醒", "以個別視窗開啟", "搜尋聊天", "開始通話", "記事本", "其他功能"])]},
        {"role": "textfield", "name": "輸入訊息", "ref_id": "@s:e60"},
        btn("貼圖", "@s:e61")]}
    return {"tree": {"role": "window", "name": "LINE", "children": [
        {"role": "group", "children": [{"role": "listbox", "children": items}]}, right]}}


P = cg.PROFILES["line"]
s = snap()
check("名稱前綴 + 逗號分隔：Group A 只配到 1 個（不含 Group A Ops）", len(cg.match_chat(P, s, "Group A")) == 1)
check("找不到 → 0", len(cg.match_chat(P, s, "Nobody")) == 0)
check("名稱重複 → 2（呼叫端會拒絕）", len(cg.match_chat(P, snap(dup=True), "Group A")) == 2)
check("未開任何對話 → header_ok 為假", not cg.header_ok(P, s, "Group A"))
check("標頭「Group A, 4人」→ header_ok 為真", cg.header_ok(P, snap("Group A, 4人"), "Group A"))
check("標頭是別的對話 → header_ok 為假", not cg.header_ok(P, snap("Group A Ops, 3人"), "Group A"))
# 存檔後的身分檢查要用標頭人數（真機：LINE 匯出檔裡沒有對話名稱）
check("header_members：「Group A, 4人」→ 4", cg.header_members(P, snap("Group A, 4人"), "Group A") == 4)
check("header_members：一對一（沒有人數）→ 2", cg.header_members(P, snap("Group A"), "Group A") == 2)
check("header_members：沒開對話 → None", cg.header_members(P, s, "Group A") is None)
check("header_members：別的對話的標頭不算 → None", cg.header_members(P, snap("Group A Ops, 3人"), "Group A") is None)
check("LINE profile 不再要求首行含名稱、改用發言者數", not P.get("verify_export_first_line_contains_chat")
      and P.get("verify_export_senders_within_members"))

# gather 的「往上載入歷史」：只能捲目前對話的訊息區（聊天清單含其他對話預覽，不可捲）
_chat = {"role": "listbox", "name": "", "children": [{"role": "button", "name": "a, 4人"} for _ in range(5)]}
_msgs = {"role": "listbox", "name": "", "children": [{"role": "listitem", "name": "m"} for _ in range(5)]}
def _scroll(node, allow):
    try:
        cg.assert_allowed(P, "scroll", node, (), {"zone": "main", "allow": allow}); return True
    except cg.Refused:
        return False
check("訊息區＋history-scroll → 可以捲", _scroll(_msgs, "history-scroll"))
check("聊天清單（子項是按鈕）→ 不可捲", not _scroll(_chat, "history-scroll"))
check("沒有明確允許 → 不可捲", not _scroll(_msgs, None))

# 靜態檢查：chat_guard 用 prof["k"]（缺了會 KeyError）讀的鍵，每個 profile 都要有，或前 8 行內有 prof.get("k") 守著。
# 為什麼：真機 LINE 匯出存檔後因 prof["dialog_title"]（假聊天才有的鍵）當掉；CI 只跑假聊天所以抓不到。
import inspect, re as _re
_src = inspect.getsource(cg).splitlines()
_ALLOWED = {"menu_path": "只在 export_via 不是 popup 時才走到"}
_bad = []
for _i, _line in enumerate(_src):
    for _k in _re.findall(r'prof\["(\w+)"\]', _line):
        if all(_k in _p for _p in cg.PROFILES.values()) or _k in _ALLOWED:
            continue
        if any('prof.get("%s")' % _k in _l for _l in _src[max(0, _i - 8): _i + 1]):
            continue
        _bad.append((_i + 1, _k, [n for n, _p in cg.PROFILES.items() if _k not in _p]))
check("chat_guard 沒有未守護的 profile 鍵（所有 profile 都走得到的路徑不會 KeyError）", not _bad, _bad)
check("has_unread 解析「名稱, N人, 未讀數, …」", P["has_unread"]("Group A, 4人, 5, x, d") and not P["has_unread"]("Group A, 4人, hi, d"))
s2 = snap("Group A, 4人")


def refused(action, name, allow=None, target=None):
    node, parents = cg.find_node(s2, lambda n: n.get("name") == name)
    try:
        cg.assert_allowed(P, action, node, parents, {"zone": "main", "allow": allow, "target_chat": target})
        return False
    except cg.Refused:
        return True


for nm in ("開始通話", "關閉提醒", "貼圖", "以個別視窗開啟"):
    check("禁區按鈕「%s」→ 擋下" % nm, refused("click", nm, allow="menu-button"))
check("訊息輸入框 set-value → 擋下", refused("set-value", "輸入訊息"))
check("訊息輸入框 click → 擋下", refused("click", "輸入訊息", allow="menu-button"))
check("清單中非目標項目 → 擋下", refused("click", "Friend B, ok, 2026年11月19日", allow="list-target", target="Group A"))
check("清單中目標項目 → 放行", not refused("click", "Group A, 4人, 5, someone sent a sticker, 2026年11月21日", allow="list-target", target="Group A"))
check("選單按鈕「其他功能」（allow=menu-button）→ 放行", not refused("click", "其他功能", allow="menu-button"))
check("選單按鈕在沒有 allow 時 → 擋下", refused("click", "其他功能"))
T1 = "Group A, 4人, 5, someone sent a sticker, 2026年11月21日"
check("scroll-to：目標對話的清單項目（allow=list-target）→ 放行", not refused("scroll-to", T1, allow="list-target", target="Group A"))
check("scroll-to：清單中非目標項目 → 擋下", refused("scroll-to", "Group A Ops, 3人, hi, 2026年11月20日", allow="list-target", target="Group A"))
check("scroll-to：沒有 allow → 擋下", refused("scroll-to", T1, target="Group A"))
check("scroll-to：清單外的元素 → 擋下", refused("scroll-to", "其他功能", allow="list-target", target="其他功能"))

# ---- 記事本（只讀檢視，Sprint E 探索）
check("記事本：沒有 allow（act）→ 擋下（仍是禁區）", refused("click", "記事本"))
check("記事本：allow=view-button、標頭是指名對話 → 放行", not refused("click", "記事本", allow="view-button", target="Group A"))
check("記事本：allow=view-button、但標頭第一顆不是指名對話 → 擋下", refused("click", "記事本", allow="view-button", target="Group A Ops"))
check("記事本：allow=view-button、沒帶 target_chat → 擋下", refused("click", "記事本", allow="view-button"))
stray = {"tree": {"role": "window", "children": [{"role": "group", "children": [btn("記事本", "@s:z1"), btn("x", "@s:z2")]}]}}
node, parents = cg.find_node(stray, lambda n: n.get("name") == "記事本")
try:
    cg.assert_allowed(P, "click", node, parents, {"zone": "main", "allow": "view-button", "target_chat": "Group A"}); ok_ = False
except cg.Refused:
    ok_ = True
check("記事本：不在標頭工具列裡的同名按鈕 → 擋下", ok_)
check("其他按鈕不能借 view-button 通過（開始通話）", refused("click", "開始通話", allow="view-button", target="Group A"))
check("探索遮罩：短名稱只給形狀（日期看得出格式）", cg._mask("2026.11.19") == {"len": 10, "shape": "9999.99.99"})
check("探索遮罩：中文與英文名字只剩字形，不含任何原字", cg._mask("王小明") == {"len": 3, "shape": "字字字"}
      and "Ann" not in str(cg._mask("Ann Lee")))
check("探索遮罩：介面用語照留、超過 12 字只給字數", cg._mask("記事本") == "記事本" and cg._mask("一" * 13) == 13)
import subprocess, tempfile, json as _json, shutil as _sh  # noqa: E402
_h = tempfile.mkdtemp(prefix="ne-probe-")
try:
    _json.dump({"granted_apps": ["line"], "chat": {"whitelist": ["Group A"]}, "idle_min_s": 0},
               open(os.path.join(_h, "policy.json"), "w", encoding="utf-8"))
    _p = subprocess.run([sys.executable, os.path.join(os.path.dirname(cg.__file__), "chat_guard.py"), "line", "probe",
                         "--chat", "Not Listed", "--view", "notes"], capture_output=True, text=True, encoding="utf-8",
                        env=dict(os.environ, NO_ERRANDS_HOME=_h, PYTHONIOENCODING="utf-8"), timeout=60)
    _o = _json.loads(_p.stdout)
    check("probe：不在白名單 → 拒絕（在閒置閘門與任何視窗操作之前）", _o.get("refused") and "白名單" in _o.get("error", ""), _o)
finally:
    _sh.rmtree(_h, ignore_errors=True)

# ---- 名稱只看群組名、忽略結尾人數標示（2026-11-22；名稱全是虛構的）
import policy  # noqa: E402
sc = policy.strip_count
check("strip_count：半形括號、前有空白", sc("Field Crew (21)") == "Field Crew")
check("strip_count：半形括號前沒有空白 → 當成名稱本身、不削（某某(2024)）", sc("Field Crew(21)") == "Field Crew(21)")
check("strip_count：全形括號", sc("Kilo（8）") == "Kilo" and sc("Kilo （8）") == "Kilo")
check("strip_count：標頭格式「, N人」", sc("Field Crew, 21人") == "Field Crew")
check("strip_count：只去一次（名稱本身以括號數字結尾）", sc("Report(2024) (5)") == "Report(2024)")
check("strip_count：沒有人數 → 原樣", sc("Field Crew") == "Field Crew" and sc("11/30 Buoy Test") == "11/30 Buoy Test")


def snap2(names, open_header=None):
    items = [btn(n + ", hi, 2026年11月21日", "@s:e%d" % (1 + i)) for i, n in enumerate(names)]
    right = {"role": "group", "children": [btn(open_header or "x", "@s:e50")] +
             [btn(n, "@s:e%d" % (51 + i)) for i, n in enumerate(["關閉提醒", "以個別視窗開啟", "搜尋聊天", "開始通話", "記事本", "其他功能"])]}
    return {"tree": {"role": "window", "name": "LINE", "children": [
        {"role": "group", "children": [{"role": "listbox", "children": items}]}, right]}}


L = ["Field Crew (21)", "Report(2024) (5)", "Report (2024)", "11/30 Buoy Test (6)", "Kilo（8）", "Other (3)"]
m = lambda names, w: [x["name"].split(", ")[0] for x in cg.match_chat(P, snap2(names), w)]
check("清單「名稱 (21)」用純名稱找得到", m(L, "Field Crew") == ["Field Crew (21)"])
check("人數變了（21→22）還是同一個", m(["Field Crew (22)"] + L[1:], "Field Crew") == ["Field Crew (22)"])
check("全形括號人數", m(L, "Kilo") == ["Kilo（8）"])
check("名稱以括號數字結尾不被誤削：Report(2024) 配到 Report(2024) (5)、不配 Report (2024)", m(L, "Report(2024)") == ["Report(2024) (5)"])
check("反過來：Report 配到人數 2024 的 Report (2024)", m(L, "Report") == ["Report (2024)"])
check("名稱含斜線", m(L, "11/30 Buoy Test") == ["11/30 Buoy Test (6)"])
check("帶人數整串貼進來（舊人數）也對得上：Field Crew (21) → 清單上的 Field Crew (22)",
      m(["Field Crew (22)"] + L[1:], "Field Crew (21)") == ["Field Crew (22)"])
check("帶人數整串貼進來、人數沒變 → 對得上", m(L, "Field Crew (21)") == ["Field Crew (21)"])
check("前綴不算：Field 找不到", m(L, "Field") == [])
try:
    cg.match_chat(P, snap2(["Echo (3)", "Echo (7)"] + L), "Echo"); amb = False
except cg.Ambiguous:
    amb = True
check("去掉人數後同名的兩個群組 → 拒絕（Ambiguous），不猜", amb)
try:
    cg.match_chat(P, snap2(["Echo (3)", "Echo (7)"]), "Echo (3)"); amb2 = False
except cg.Ambiguous:
    amb2 = True
check("就算帶人數整串指名，去掉人數後同名 → 一樣拒絕（人數會變，不能拿來分辨）", amb2)
check("標頭「名稱, 22人」配純名稱", cg.header_ok(P, snap2(L, "Field Crew, 22人"), "Field Crew"))
check("標頭「名稱 (22)」配純名稱", cg.header_ok(P, snap2(L, "Field Crew (22)"), "Field Crew"))
check("標頭是別的群組 → 不符", not cg.header_ok(P, snap2(L, "Field Crew Ops, 22人"), "Field Crew"))
check("標頭 Report (2024) 不配 Report(2024)", not cg.header_ok(P, snap2(L, "Report, 2024人"), "Report(2024)"))
check("人數取當下標頭：22人 → 22", cg.header_members(P, snap2(L, "Field Crew, 22人"), "Field Crew") == 22)
check("人數取當下標頭：(22) → 22", cg.header_members(P, snap2(L, "Field Crew (22)"), "Field Crew") == 22)
check("全形 （8） → 8", cg.header_members(P, snap2(L, "Kilo（8）"), "Kilo") == 8)
check("名稱以 (2024) 結尾、標頭沒另外人數 → 不把 2024 當人數（=2）",
      cg.header_members(P, snap2(L, "Report(2024)"), "Report(2024)") == 2)
check("名稱以 (2024) 結尾、標頭 Report(2024), 5人 → 5", cg.header_members(P, snap2(L, "Report(2024), 5人"), "Report(2024)") == 5)
fk = cg.file_key
check("預設檔名：斜線被換成底線也對得上", fk("11/30 Buoy Test") in fk("[LINE] 11_30 Buoy Test (6).txt"))
check("預設檔名：斜線被刪掉也對得上", fk("11/30 Buoy Test") in fk("[LINE] 1130 Buoy Test.txt"))
check("預設檔名：全形斜線也對得上", fk("11/30 Buoy Test") in fk("[LINE] 11／30 Buoy Test.txt"))
check("預設檔名：別的群組 → 不符", fk("11/30 Buoy Test") not in fk("[LINE] 12_01 Buoy Test.txt"))
pol = {"chat": {"whitelist": ["Field Crew", "Report(2024)", "Kilo（8）"]}}
check("白名單：純名稱 → 本身", policy.canonical_chat(pol, "Field Crew") == "Field Crew")
check("白名單：帶人數的 --chat → 對到純名稱那筆", policy.canonical_chat(pol, "Field Crew (22)") == "Field Crew")
check("白名單：存的是帶人數的舊值、用純名稱指名 → 對到那筆", policy.canonical_chat(pol, "Kilo") == "Kilo（8）")
check("白名單：Report(2024) 不被當成 Report", policy.canonical_chat(pol, "Report") == "Report"
      and not policy.in_whitelist(pol, "Report"))
check("白名單：去掉人數後兩筆都對得上 → None（不猜）",
      policy.canonical_chat({"chat": {"whitelist": ["Echo (3)", "Echo (7)"]}}, "Echo") is None)
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
