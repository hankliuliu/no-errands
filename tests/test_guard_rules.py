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
    for sub in ("grant", "set", "whitelist", "domain", "token-allow"):
        check("policy.py %s 要詢問" % sub, any(("policy.py\" %s:*)" % sub) in r for r in gr["ask"]), gr["ask"])
    check("chat_guard 不在詢問規則裡", not any("chat_guard" in r for r in gr["ask"] + gr["deny"]), gr["ask"])
    check("詢問規則只有 policy.py 的 5 條", len(gr["ask"]) == 5 and all("policy.py" in r for r in gr["ask"]), gr["ask"])
    check("revoke／show 不詢問（縮小範圍、唯讀）", not any(" revoke:" in r or " show:" in r for r in gr["ask"]), gr["ask"])
    # ---- 三組可分開開關
    g_tc = ne.guard_rules(h2, ("token", "consent"))
    check("只開 token+consent：沒有 direct-tools 的 deny", g_tc["deny"] == [], g_tc["deny"])
    check("只開 token+consent：有 token hook 與 5 條 ask", len(g_tc["hooks"]["PreToolUse"]) == 1 and len(g_tc["ask"]) == 5)
    g_d = ne.guard_rules(h2, ("direct-tools",))
    check("只開 direct-tools：沒有 hook、沒有 ask", not g_d["hooks"]["PreToolUse"] and not g_d["ask"] and len(g_d["deny"]) == 4)
    check("token 組不再用 deny 規則（deny 無法被專案層級蓋掉）", not any(r.startswith("Read(") for r in gr["deny"]))
    # ---- hooks 只增刪自己的，使用者自己的 hook 保留
    sp2 = os.path.join(td, "s2.json")
    user_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
    json.dump({"hooks": {"PreToolUse": [user_hook], "Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}, open(sp2, "w"))
    r1 = ne.apply_rules(sp2, g_tc); r2 = ne.apply_rules(sp2, g_tc)
    c2 = json.load(open(sp2, encoding="utf-8"))
    pre = c2["hooks"]["PreToolUse"]
    check("hook 加入、使用者自己的 hook 保留", user_hook in pre and sum(1 for e in pre if ne._ours(e)) == 1 and "Stop" in c2["hooks"], pre)
    check("hook 冪等：第二次 changed=0", r2["changed"] == 0, (r1, r2))
    ne.apply_rules(sp2, ne.guard_rules(h2), remove=True)
    c2 = json.load(open(sp2, encoding="utf-8"))
    check("移除：只拿掉自己的 hook，使用者的與 Stop 保留", c2["hooks"]["PreToolUse"] == [user_hook] and "Stop" in c2["hooks"]
          and "permissions" not in c2, c2)
    # ---- token_guard 的判斷（離線）
    sys.path.insert(0, os.path.join(REPO, "skills", "no-errands", "scripts"))
    import token_guard as tg
    tok = os.path.join(td, "secret", "mattermost.txt")
    projA, projB = os.path.join(td, "projA"), os.path.join(td, "projB")
    pol = {"mattermost": {"token_file": tok, "token_read_allow": [projB]}}
    ev = lambda tool, inp: {"tool_name": tool, "tool_input": inp}
    fwd = tok.replace("\\", "/")
    for label, e in (("Read 完整路徑", ev("Read", {"file_path": tok})),
                     ("Bash cat 正斜線", ev("Bash", {"command": "cat '%s'" % fwd})),
                     ("Bash 只寫檔名（先 cd 過去）", ev("Bash", {"command": "cd secret && type mattermost.txt"})),
                     ("PowerShell Get-Content", ev("PowerShell", {"command": "Get-Content \"%s\"" % tok})),
                     ("Grep 指定路徑", ev("Grep", {"pattern": "x", "path": tok})),
                     ("大小寫不同", ev("Read", {"file_path": tok.upper()}))):
        check("token_guard 預設擋：" + label, tg.decide(e, pol, projA) is not None)
    check("token_guard 白名單專案放行（Read）", tg.decide(ev("Read", {"file_path": tok}), pol, projB) is None)
    check("token_guard 白名單專案的子資料夾也放行", tg.decide(ev("Read", {"file_path": tok}), pol, os.path.join(projB, "sub")) is None)
    check("token_guard 名稱相近的資料夾不算白名單（projB2）", tg.decide(ev("Read", {"file_path": tok}), pol, projB + "2") is not None)
    check("token_guard 不相關的指令不表態", tg.decide(ev("Bash", {"command": "git status"}), pol, projA) is None
          and tg.decide(ev("Read", {"file_path": os.path.join(td, "mattermost.txt.bak")}), pol, projA) is None)
    check("token_guard 政策沒有 token 檔 → 不表態", tg.decide(ev("Read", {"file_path": tok}), {}, projA) is None)
    # ---- CLI：開 token 組時先把 token_guard.py 放進 bin（不在的話 hook 以代碼 2 結束 = 擋下所有工具）
    h3, p3 = os.path.join(td, "h3"), os.path.join(td, "p3")
    os.makedirs(p3)
    cp = subprocess.run([sys.executable, os.path.join(REPO, "setup.py"), "--project", p3, "--home", h3, "--guard-rules",
                         "--guard-groups", "token,consent"], capture_output=True, text=True, encoding="utf-8")
    s3 = json.load(open(os.path.join(p3, ".claude", "settings.json"), encoding="utf-8"))
    check("CLI --guard-groups token,consent：bin/token_guard.py 已就位、hook 與 ask 寫入、沒有 direct-tools deny",
          os.path.exists(os.path.join(h3, "bin", "token_guard.py")) and len(s3["hooks"]["PreToolUse"]) == 1
          and len(s3["permissions"]["ask"]) == 5 and "deny" not in s3["permissions"], (cp.stdout[-300:], s3))
    hp = subprocess.run(s3["hooks"]["PreToolUse"][0]["hooks"][0]["command"], shell=True, input='{"tool_name":"Bash","tool_input":{"command":"ls"}}',
                        capture_output=True, text=True)
    check("寫進設定的 hook 指令真的能跑、不相關的指令回 0 且不表態", hp.returncode == 0 and hp.stdout.strip() == "", (hp.returncode, hp.stdout, hp.stderr[-200:]))
    # 找不到腳本就放行：把 bin/token_guard.py 搬走，同一條 hook 指令必須以 0 結束（2 = 擋下所有工具）
    tgp = os.path.join(h3, "bin", "token_guard.py")
    os.rename(tgp, tgp + ".away")
    hp2 = subprocess.run(s3["hooks"]["PreToolUse"][0]["hooks"][0]["command"], shell=True,
                         input='{"tool_name":"Read","tool_input":{"file_path":"x"}}', capture_output=True, text=True)
    os.rename(tgp + ".away", tgp)
    check("腳本檔不在：hook 以 0 結束、不表態（不會擋下所有工具）", hp2.returncode == 0 and hp2.stdout.strip() == "",
          (hp2.returncode, hp2.stderr[-200:]))
    bad_py = '"%s"' % os.path.join(td, "no-such-python.exe")
    hp3 = subprocess.run(ne.token_hook_cmd(bad_py, h3), shell=True, input="{}", capture_output=True, text=True, errors="replace")
    check("python 本身不在：結束代碼不是 2（殼層錯誤，不擋）", hp3.returncode != 2, hp3.returncode)
    # 只看路徑與殼層指令欄位：文件內容、搜尋樣式提到檔名不算
    check("token_guard：Edit／Write 的內容提到檔名 → 不擋", tg.decide(ev("Write", {"file_path": os.path.join(td, "README.md"),
          "content": "token 放在 mattermost.txt"}), pol, projA) is None
          and tg.decide(ev("Edit", {"file_path": os.path.join(td, "a.md"), "old_string": "x", "new_string": "mattermost.txt"}), pol, projA) is None)
    check("token_guard：Grep 的搜尋樣式是檔名 → 不擋", tg.decide(ev("Grep", {"pattern": "mattermost.txt", "path": td}), pol, projA) is None)
    check("token_guard：殼層指令提到檔名 → 擋（已知副作用，寫在 README）",
          tg.decide(ev("Bash", {"command": "grep -rn mattermost.txt docs/"}), pol, projA) is not None)
    cp = subprocess.run([sys.executable, os.path.join(REPO, "setup.py"), "--project", p3, "--home", h3, "--guard-rules",
                         "--guard-groups", "nope"], capture_output=True, text=True, encoding="utf-8")
    check("CLI 不認得的組 → 拒絕", cp.returncode == 2)
    subprocess.run([sys.executable, os.path.join(REPO, "setup.py"), "--project", p3, "--home", h3, "--guard-rules", "--uninstall"],
                   capture_output=True, text=True, encoding="utf-8")
    check("CLI --uninstall：三組都拿掉", json.load(open(os.path.join(p3, ".claude", "settings.json"), encoding="utf-8")) == {})
finally:
    shutil.rmtree(td, ignore_errors=True)
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
