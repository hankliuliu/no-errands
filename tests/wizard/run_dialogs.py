#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B3(ii) 常見對話框（CI Windows only；會開視窗）：訊息框按 OK、開檔/存檔按取消、覆寫確認按「否」、列印按取消。
只用 agent-desktop 語意點擊；比對 dialogs.ps1 自己記下的回傳值。
列印對話框先用新版（PrintDlgEx）；有些機器（使用者的 Win11 Home zh-TW）新版會卡住、連視窗都不出現——
前四種都做完、等了 20 秒還看不到列印視窗時，收掉重跑一次只開傳統版列印對話框（-PrintEx 0）。"""
import json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
AD = sys.argv[sys.argv.index("--ad") + 1]
EXPECT = {"msgbox": "OK", "open": "Cancel", "save": "Cancel", "overwrite": "No", "print": "Cancel"}
# 每種對話框要按的鈕（名稱候選）；永遠不按確認/覆寫/列印
ANSWER = [("NE Info", ("OK", "確定")), ("NE Open", ("Cancel", "取消")), ("NE Save", ("Cancel", "取消")),
          ("NE Confirm Replace", ("No", "否(N)", "否")), ("Print", ("Cancel", "取消")), ("列印", ("Cancel", "取消"))]
PRINT_TITLES = ("Print", "列印")


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


def read_results(out):
    got = {}
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            d = json.loads(line); got[d["kind"]] = d["result"]
    return got


def run_pass(out, clicks, extra=(), budget=120):
    """跑一輪 dialogs.ps1、照 ANSWER 點掉出現的對話框。回傳 'print_stuck' 或 None。"""
    proc = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File",
                             os.path.join(HERE, "dialogs.ps1"), "-Out", out] + list(extra))
    t0, waiting_print_since = time.time(), None
    try:
        while proc.poll() is None and time.time() - t0 < budget:
            time.sleep(0.8)
            seen_print = False
            for w in ad("list-windows").get("data") or []:
                if w.get("pid") != proc.pid:
                    continue
                for title, names in ANSWER:
                    if title in (w.get("title") or ""):
                        if title in PRINT_TITLES:
                            seen_print = True
                        snap = ad("snapshot", "--window-id", w["id"], "-i", "--compact")
                        btn = next((n for n in walk(snap.get("data", {}).get("tree", {}))
                                    if n.get("role") == "button" and n.get("name") in names), None)
                        if btn:
                            r = ad("click", btn["ref_id"])
                            clicks.append({"dialog": title, "clicked": btn["name"], "ok": r.get("ok")})
                            time.sleep(0.6)
                        break
            # 前四種都記完、剩列印：看不到列印視窗超過 20 秒＝PrintDlgEx 卡住（這台機器開不出來）
            got = read_results(out)
            if "print" not in got and all(k in got for k in ("msgbox", "open", "save", "overwrite")) and not seen_print:
                waiting_print_since = waiting_print_since or time.time()
                if time.time() - waiting_print_since > 20:
                    return "print_stuck"
            else:
                waiting_print_since = None
    finally:
        if proc.poll() is None:
            proc.kill()
    return None


def main():
    if "--i-am-ci" not in sys.argv:
        print("refusing: opens dialogs; CI only (--i-am-ci)"); sys.exit(2)
    out = os.path.join(tempfile.mkdtemp(), "dialogs.jsonl")
    clicks = []
    stuck = run_pass(out, clicks)
    retried = False
    if stuck == "print_stuck" or "print" not in read_results(out):
        retried = True
        run_pass(out, clicks, extra=["-Only", "print", "-PrintEx", "0"], budget=60)
    got = read_results(out)
    res = {"clicks": clicks, "results": got, "print_classic_retry": retried,
           "per_dialog": {k: got.get(k) == v for k, v in EXPECT.items()}}
    res["ok"] = all(res["per_dialog"].values())
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print("ALL PASS (%d)" % len(EXPECT) if res["ok"] else "FAILED %d/%d" % (sum(1 for v in res["per_dialog"].values() if not v), len(EXPECT)))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
