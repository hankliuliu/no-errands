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
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
