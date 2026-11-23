#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_guard LINE profile 離線測試（純 stdlib、無視窗、三平台）：用仿 LINE 結構的合成 snapshot。"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "no-errands", "scripts"))
import chat_guard as cg  # noqa: E402
import tempfile as _tf, json as _js, hashlib as _hl  # noqa: E402

# 記事本／peek 的目標資料夾：名稱帶不帶人數都落在 gather 那個目標
_h = _tf.mkdtemp(prefix="ne-tid-")
_old = cg.HOME
try:
    cg.HOME = _h
    import os as _os
    _os.makedirs(_os.path.join(_h, "gather"))
    _js.dump({"abc123def456": {"service": "line", "chat": "週末登山隊 (21)"}}, open(_os.path.join(_h, "gather", "targets.json"), "w", encoding="utf-8"), ensure_ascii=False)
    _tid_ok = cg.target_id("line", "週末登山隊") == "abc123def456" and cg.target_id("line", "週末登山隊, 22人") == "abc123def456"
    _tid_fb = cg.target_id("line", "別的群組") == _hl.sha256("line|別的群組".encode("utf-8")).hexdigest()[:12]
finally:
    cg.HOME = _old
    import shutil as _sh
    _sh.rmtree(_h, ignore_errors=True)

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
import time as _t, tempfile, shutil as _sh  # noqa: E402
_ph = tempfile.mkdtemp(prefix="ne-peek-")
try:
    _old_home = cg.HOME; cg.HOME = _ph
    _d = os.path.join(_ph, "gather", "line", "abc", "_peek"); os.makedirs(_d)
    _old, _new = os.path.join(_d, "old.png"), os.path.join(_d, "new.png")
    open(_old, "wb").write(b"x"); open(_new, "wb").write(b"x")
    os.utime(_old, (_t.time() - 3600, _t.time() - 3600))
    _n = cg.purge_old_peeks()
    check("看一眼的兜底：超過 30 分鐘的截圖自動刪、新的留著", _n == 1 and not os.path.exists(_old) and os.path.exists(_new))
finally:
    cg.HOME = _old_home; _sh.rmtree(_ph, ignore_errors=True)
emo = "Amy😀★"
check("探索遮罩：表情符號與符號換成佔位，原字不出現", "😀" not in str(cg._mask(emo)) and "★" not in str(cg._mask(emo))
      and cg._mask(emo)["shape"] == "aaa??", cg._mask(emo))
ext = "𠮷㐀x"
check("探索遮罩：擴充區漢字只剩字形，原字不出現", "𠮷" not in str(cg._mask(ext)) and "㐀" not in str(cg._mask(ext))
      and cg._mask(ext)["shape"] == "字字a", cg._mask(ext))
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
# 輸入監看：只忽略落在「我們自己的動作執行期間」的那筆輸入（假時鐘，三平台）
import idle as _idle  # noqa: E402


class _Clock:
    t = 1000.0
    last = 900.0   # 最後一筆輸入的時間點

    @classmethod
    def time(cls):
        return cls.t


_orig = (_idle.time, _idle.idle_seconds)
_idle.time = _Clock
_idle.idle_seconds = lambda: _Clock.t - _Clock.last
try:
    w_ = _idle.IdleWatch()                        # 開始時已閒置 100 秒
    _Clock.t = 1001.0; k_ = w_.own_begin()        # 我們的點擊開始
    _Clock.t = 1001.9; _Clock.last = 1001.9       # 點擊中途，系統記到一筆輸入（我們自己的合成輸入）
    _Clock.t = 1002.5
    own_open_ok = not w_.interrupted(slack=0.3)   # 點擊還在跑：不算使用者
    _Clock.t = 1004.0; w_.own_end(k_)             # 點擊結束（多算 0.3 秒）
    _Clock.t = 1010.0
    own_done_ok = not w_.interrupted(slack=0.3)   # 之後沒人碰：還是不算
    _Clock.last = 1011.0; _Clock.t = 1011.5       # 使用者真的動了（在我們的動作以外）
    user_ok = w_.interrupted(slack=0.3)
    w2 = _idle.IdleWatch()
    _Clock.last = _Clock.t + 0.5; _Clock.t += 1.0
    plain_ok = w2.interrupted(slack=0.3)          # 沒有任何我們的動作：照舊一動就算
finally:
    _idle.time, _idle.idle_seconds = _orig
check("輸入監看：我們的點擊執行中送出的系統輸入不算使用者回來", own_open_ok)
check("輸入監看：點擊結束後沒人碰，仍不算", own_done_ok)
check("輸入監看：我們的動作以外的輸入照常算使用者回來", user_ok)
check("輸入監看：沒有我們的動作時，照舊一動就算（沒有放寬）", plain_ok)
# 電腦鎖定或螢幕保護程式時：閒置閘門與操作中的檢查都要中止並講明原因
_orig_su = _idle.screen_unavailable
try:
    _orig_sig = _idle.screen_signals
    _idle.screen_signals = lambda: {}
    _idle.screen_unavailable = lambda sig=None: "電腦目前鎖定中（測試）"
    try:
        cg.idle_gate({"raises_foreground": True}, {"busy_behavior": "wait_idle", "idle_min_s": 0}, [])
        _gate_ok = False
    except cg.Refused as _e:
        _gate_ok = "鎖定" in str(_e) and "解鎖前不操作" in str(_e)
    cg.WATCH.update({"watch": _idle.IdleWatch(), "win": None, "tripped": False})
    try:
        cg.check_interrupt(); _ci_ok = False
    except cg.Refused as _e:
        _ci_ok = "鎖定" in str(_e)
finally:
    _idle.screen_unavailable = _orig_su
    _idle.screen_signals = _orig_sig
    cg.WATCH.update({"watch": None, "win": None, "tripped": False})
check("電腦鎖定時：閒置閘門拒絕並講明原因（不把鎖定當成閒置）", _gate_ok)
_S = dict(wts_locked=None, lockapp_foreground=None, logonui=None, secure_desktop=False, screensaver=False)
check("鎖定判斷：只有工作階段旗標一個訊號 → 不擋（避免沒鎖卻說鎖著）", _idle.screen_unavailable(dict(_S, wts_locked=True)) is None
      and _idle.single_lock_signal(dict(_S, wts_locked=True)))
check("鎖定判斷：旗標＋鎖定畫面在前景 → 擋", "鎖定" in (_idle.screen_unavailable(dict(_S, wts_locked=True, lockapp_foreground=True)) or ""))
check("鎖定判斷：旗標讀不到，但 LockApp 在前景＋LogonUI 在跑 → 擋", "鎖定" in (_idle.screen_unavailable(dict(_S, lockapp_foreground=True, logonui=True)) or ""))
check("鎖定判斷：安全桌面、螢幕保護程式單一訊號就擋", _idle.screen_unavailable(dict(_S, secure_desktop=True)) and _idle.screen_unavailable(dict(_S, screensaver=True)))
check("鎖定判斷：都沒有 → 可以用", _idle.screen_unavailable(_S) is None)
if os.name == "nt":   # 診斷：這台機器（含 CI 測試機）當下各訊號的值與判斷結果，只有布林值
    print("screen signals:", _idle.screen_signals(), "->", _idle.screen_unavailable())
check("電腦鎖定時：操作中的檢查中止並講明原因", _ci_ok)
import inspect as _ins2  # noqa: E402
check("記事本擷取中途被鎖定：stop=locked、圖全丟（在丟圖的清單裡）", '"wrong_chat", "no_overlap", "locked"' in _ins2.getsource(cg.cmd_notes))
check("記事本目標資料夾：名稱帶不帶人數都對到 gather 那個目標", _tid_ok)

# wishes 第 3 項：什麼時候動手（立刻／等你離開）。立刻與觀看都不等閒置，但使用者一動就讓開的監看照樣開著
_saved = (_idle.screen_signals, _idle.screen_unavailable, _idle.single_lock_signal, _idle.idle_seconds)
try:
    _idle.screen_signals = lambda: {}
    _idle.screen_unavailable = lambda sig=None: None
    _idle.single_lock_signal = lambda sig: False
    _idle.idle_seconds = lambda: 2.0          # 使用者兩秒前才動過
    _res = {}
    for _name, _pol in (("預設（等離開）", {"busy_behavior": "wait_idle", "idle_min_s": 180}),
                        ("立刻", {"busy_behavior": "wait_idle", "idle_min_s": 180, "start_when": "now"}),
                        ("觀看", {"busy_behavior": "wait_idle", "idle_min_s": 180, "visibility": "watch"})):
        cg.WATCH.update({"watch": None, "win": None, "tripped": False})
        try:
            cg.idle_gate({"raises_foreground": True}, _pol, [])
            _res[_name] = ("過", cg.WATCH.get("watch") is not None)
        except cg.Refused as _e:
            _res[_name] = ("擋", str(_e)[:30])
        cg.WATCH.update({"watch": None, "win": None, "tripped": False})
    _idle.screen_unavailable = lambda sig=None: "電腦目前鎖定中（測試）"
    try:
        cg.idle_gate({"raises_foreground": True}, {"start_when": "now"}, []); _lock_now = False
    except cg.Refused as _e:
        _lock_now = "鎖定" in str(_e)
finally:
    _idle.screen_signals, _idle.screen_unavailable, _idle.single_lock_signal, _idle.idle_seconds = _saved
    cg.WATCH.update({"watch": None, "win": None, "tripped": False})
check("預設仍是等使用者離開（他兩秒前還在動 → 擋）", _res["預設（等離開）"][0] == "擋", _res)
check("設成立刻：不等閒置就開始，而且「他一動就讓開」的監看開著", _res["立刻"] == ("過", True), _res)
check("觀看模式：不等閒置，監看也開著（以前觀看模式會連監看一起跳過）", _res["觀看"] == ("過", True), _res)
check("設成立刻也不在鎖定時操作", _lock_now)
_ph = tempfile.mkdtemp(prefix="ne-pol-sw-")
try:
    _env = dict(os.environ, NO_ERRANDS_HOME=_ph, PYTHONIOENCODING="utf-8")
    _pp = os.path.join(os.path.dirname(cg.__file__), "policy.py")
    _r1 = subprocess.run([sys.executable, _pp, "set", "start_when", "now", "--reason", "測試"], capture_output=True, text=True, env=_env)
    _r2 = subprocess.run([sys.executable, _pp, "set", "start_when", "always", "--reason", "測試"], capture_output=True, text=True, env=_env)
    _pj = _js.load(open(os.path.join(_ph, "policy.json"), encoding="utf-8"))
    check("policy.py set start_when now 寫進政策檔；不認得的值拒絕", _r1.returncode == 0 and _pj.get("start_when") == "now"
          and _r2.returncode != 0, (_r1.stdout[-200:], _r2.stdout[-200:], _pj.get("start_when")))
    check("政策摘要說明會跳到前面的操作什麼時候動手", "立刻（你一動就讓開）" in policy.summary(_pj), policy.summary(_pj))
finally:
    _sh.rmtree(_ph, ignore_errors=True)
check("記事本目標資料夾：gather 沒有這個目標 → 和 gather 同一個算法", _tid_fb)
import inspect as _ins  # noqa: E402
check("記事本擷取的逾時有硬上限 180 秒（參數繞不過）、張數硬上限 40", cg.NOTES_TIMEOUT_MAX_S == 180 and cg.NOTES_HARD_MAX == 40
      and "min(float(timeout_s), NOTES_TIMEOUT_MAX_S)" in _ins.getsource(cg.cmd_notes) and "min(int(max_shots), NOTES_HARD_MAX)" in _ins.getsource(cg.cmd_notes))
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
