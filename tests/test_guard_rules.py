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
    # 守門從「建議」變成「只有這條路」：直接呼叫兩個執行檔的規則（語法經巢狀 claude 實測，見 setup.guard_rules 註解）
    h2 = os.path.join(td, "h2"); os.makedirs(h2)
    gr = ne.guard_rules(h2)
    for tool in ("agent-browser", "agent-desktop"):
        check("規則擋直接呼叫 %s（Bash 與 PowerShell）" % tool,
              "Bash(*%s*)" % tool in gr["deny"] and "PowerShell(*%s*)" % tool in gr["deny"], gr["deny"])
    check("neb／ned 本身不在擋的規則裡", not any("neb.py" in r or "ned.py" in r for r in gr["deny"]), gr["deny"])
    # 「要詢問」只給改變同意範圍的指令；chat_guard 不詢問（白名單內的同步要能無人在場跑完）
    for sub in ("grant", "set", "whitelist", "domain"):
        check("policy.py %s 要詢問" % sub, any(("policy.py\" %s:*)" % sub) in r for r in gr["ask"]), gr["ask"])
    check("chat_guard 不在詢問規則裡", not any("chat_guard" in r for r in gr["ask"] + gr["deny"]), gr["ask"])
    check("詢問規則只有 policy.py 的 4 條", len(gr["ask"]) == 4 and all("policy.py" in r for r in gr["ask"]), gr["ask"])
    check("revoke／show 不詢問（縮小範圍、唯讀）", not any(" revoke:" in r or " show:" in r for r in gr["ask"]), gr["ask"])
finally:
    shutil.rmtree(td, ignore_errors=True)
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
