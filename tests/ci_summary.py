#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 彙總：讀各步驟的實際輸出（不是步驟的綠勾），印成 markdown，任何一項實際失敗就 exit 1。

continue-on-error 的步驟在 API 上一律顯示 success；這支腳本是唯一可信的紅綠來源。
"""
import json, os, sys

os_name = os.environ.get("NE_OS", "?")
rows, failed = [], []


def add(name, ok, detail=""):
    rows.append((name, ok, detail))
    if ok is False:
        failed.append(name)


if os.path.exists("doctor.json"):
    try:
        d = json.load(open("doctor.json", encoding="utf-8-sig"))
        for k, v in d.get("checks", {}).items():
            note = ""
            if k == "desktop" and not os_name.startswith("windows"):
                note = "僅驗 status（尚無寫入→讀回）"
            add("doctor:" + k, bool(v.get("ok")), ((v.get("error") or "") + " " + note).strip()[:160])
    except Exception as e:
        add("doctor.json", False, "unreadable: %s" % e)
else:
    add("doctor.json", False, "missing")

for f, label in (("outbox.txt", "outbox tests"), ("ned.txt", "ned tests"), ("mm.txt", "mm_ro tests"), ("office.txt", "office tests"), ("b3.txt", "chat_guard B3"),
                 ("n4pos.txt", "N4 positive control"), ("idle.txt", "idle gate")):
    if not os.path.exists(f):
        if f in ("b3.txt", "n4pos.txt", "idle.txt") and not os_name.startswith("windows"):
            continue
        add(label, False, "output missing")
        continue
    lines = open(f, encoding="utf-8", errors="replace").read().splitlines()
    last = next((l for l in reversed(lines) if l.strip()), "")
    ok = last.startswith("ALL PASS") or last.startswith("POSITIVE CONTROL PASS") or last.startswith("SKIP")
    add(label, ok, last[:120])

if os.path.exists("n4.json"):
    try:
        n = json.load(open("n4.json", encoding="utf-8"))
        ours = [s["step"] for s in n.get("steps", []) if s.get("interference")]
        add("N4 typing identical", bool(n.get("typing", {}).get("identical")))
        add("N4 no interference by our tools", not ours, ", ".join(ours)[:160])
    except Exception as e:
        add("n4.json", False, "unreadable: %s" % e)
elif os_name.startswith("windows"):
    add("n4.json", False, "missing")

print("## %s %s" % (os_name, "❌ %d failed" % len(failed) if failed else "✅ all passed"))
for name, ok, detail in rows:
    print("- %s %s %s" % ("✅" if ok else "❌", name, ("— " + detail) if detail else ""))
sys.exit(1 if failed else 0)
