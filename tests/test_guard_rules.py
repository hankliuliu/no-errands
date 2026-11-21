#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup.py --guard-rules 離線測試：只增刪自己的規則、既有內容保留、冪等、Windows 路徑寫法。
（規則真的擋得住，已用巢狀 claude -p 實測：Read 與 Bash cat 讀 token 檔都被拒絕，見 LOG。）"""
import json, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import setup as ne  # noqa: E402

R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


td = tempfile.mkdtemp(prefix="ne-gr-")
try:
    check("Windows 路徑 → //c/... 小寫無冒號", ne.rule_path(r"C:\Users\x\t.txt") == "//c/Users/x/t.txt" if os.name == "nt"
          else ne.rule_path("/home/x/t.txt") == "//home/x/t.txt")
    sp = os.path.join(td, ".claude", "settings.json")
    os.makedirs(os.path.dirname(sp))
    json.dump({"permissions": {"allow": ["Bash(npm test:*)"], "deny": ["Read(./.env)"]}, "hooks": {"Stop": []}},
              open(sp, "w", encoding="utf-8"))
    rules = {"deny": ["Read(//x/token.txt)"], "ask": ["Bash(py chat_guard.py line:*)"]}
    ne.apply_rules(sp, rules)
    ne.apply_rules(sp, rules)   # 冪等
    c = json.load(open(sp, encoding="utf-8"))
    check("新增自己的規則", "Read(//x/token.txt)" in c["permissions"]["deny"] and c["permissions"]["ask"] == rules["ask"])
    check("冪等：不重複", c["permissions"]["deny"].count("Read(//x/token.txt)") == 1)
    check("既有規則與 hooks 保留", "Read(./.env)" in c["permissions"]["deny"] and c["permissions"]["allow"] == ["Bash(npm test:*)"]
          and "hooks" in c)
    ne.apply_rules(sp, rules, remove=True)
    c = json.load(open(sp, encoding="utf-8"))
    check("移除只拿掉自己的", c["permissions"].get("deny") == ["Read(./.env)"] and "ask" not in c["permissions"]
          and c["permissions"]["allow"] == ["Bash(npm test:*)"] and "hooks" in c, c)
finally:
    shutil.rmtree(td, ignore_errors=True)
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
