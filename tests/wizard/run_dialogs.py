#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B3(ii) 常見對話框（CI Windows only；會開視窗）：訊息框按 OK、開檔/存檔按取消、覆寫確認按「否」、列印按取消。
只用 agent-desktop 語意點擊；比對 dialogs.ps1 自己記下的回傳值。"""
import json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
AD = sys.argv[sys.argv.index("--ad") + 1]
EXPECT = {"msgbox": "OK", "open": "Cancel", "save": "Cancel", "overwrite": "No", "print": "Cancel"}
# 每種對話框要按的鈕（名稱候選）；永遠不按確認/覆寫/列印
ANSWER = [("NE Info", ("OK", "確定")), ("NE Open", ("Cancel", "取消")), ("NE Save", ("Cancel", "取消")),
          ("NE Confirm Replace", ("No", "否(N)", "否")), ("Print", ("Cancel", "取消")), ("列印", ("Cancel", "取消"))]


def ad(*a):
    try:
        p = subprocess.run([AD] + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False}


def walk(n):
    yield n
    for c in n.get("children") or []:
        yield from walk(c)


def main():
    if "--i-am-ci" not in sys.argv:
        print("refusing: opens dialogs; CI only (--i-am-ci)"); sys.exit(2)
    out = os.path.join(tempfile.mkdtemp(), "dialogs.jsonl")
    proc = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File",
                             os.path.join(HERE, "dialogs.ps1"), "-Out", out])
    clicks, t0 = [], time.time()
    while proc.poll() is None and time.time() - t0 < 120:
        time.sleep(0.8)
        for w in ad("list-windows").get("data") or []:
            if w.get("pid") != proc.pid:
                continue
            for title, names in ANSWER:
                if title in (w.get("title") or ""):
                    snap = ad("snapshot", "--window-id", w["id"], "-i", "--compact")
                    btn = next((n for n in walk(snap.get("data", {}).get("tree", {}))
                                if n.get("role") == "button" and n.get("name") in names), None)
                    if btn:
                        r = ad("click", btn["ref_id"])
                        clicks.append({"dialog": title, "clicked": btn["name"], "ok": r.get("ok")})
                        time.sleep(0.6)
                    break
    if proc.poll() is None:
        proc.kill()
    got = {}
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            d = json.loads(line); got[d["kind"]] = d["result"]
    res = {"clicks": clicks, "results": got,
           "per_dialog": {k: got.get(k) == v for k, v in EXPECT.items()}}
    res["ok"] = all(res["per_dialog"].values())
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print("ALL PASS (%d)" % len(EXPECT) if res["ok"] else "FAILED %d/%d" % (sum(1 for v in res["per_dialog"].values() if not v), len(EXPECT)))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
