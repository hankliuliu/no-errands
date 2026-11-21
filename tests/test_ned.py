#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ned 拒絕測試（純 stdlib、無 GUI；三平台 CI 都跑）。用假的 agent-desktop（NE_FAKE_AD_PY）。

  - 未授權敏感 app → 拒；非正規 app 名 → 拒
  - 外來 ref（不是 ned 拍的 snapshot）→ 拒；跨 app ref → 拒
  - 背景模式 focus/type → 拒
  - snapshot 輸出含 NE-OUTBOX → 該 snapshot 的 ref 一律拒（發件匣禁區）
  - 一般 app 的 snapshot → click 放行
  - 從 <home>/bin 執行、沒有 NO_ERRANDS_HOME → 用該 home（不會寫進 ~/.no-errands）
"""
import json, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
RESULTS = []

FAKE_AD = r'''
import json, sys
sub = sys.argv[1]
if sub == "snapshot":
    page = "NE-OUTBOX" if "--outbox" in sys.argv else "Plain"
    sid = "soutbox1" if "--outbox" in sys.argv else "splain1"
    print(json.dumps({"ok": True, "data": {"snapshot_id": sid, "tree": {"name": page}}}, separators=(",", ":")))
else:
    print(json.dumps({"ok": True, "data": {"did": sub}}))
'''


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -- " + str(detail)[:300]))


def main():
    home = tempfile.mkdtemp(prefix="ne-ned-test-")
    fake = os.path.join(home, "fake_ad.py")
    open(fake, "w", encoding="utf-8").write(FAKE_AD)
    env = dict(os.environ, NO_ERRANDS_HOME=home, NE_FAKE_AD_PY=fake, PYTHONIOENCODING="utf-8")

    def ned(*args, e=env, script=os.path.join(SC, "ned.py")):
        p = subprocess.run([sys.executable, script] + list(args), capture_output=True, text=True,
                           encoding="utf-8", env=e)
        try:
            return json.loads(p.stdout), p.returncode
        except Exception:
            return {"raw": p.stdout, "err": p.stderr}, p.returncode

    try:
        # 升級守門：放行清單是對某個版本的 --help 全文逐項對過的；versions.json 換版本而沒重對 → 失敗
        vj = json.load(open(os.path.join(REPO, "versions.json"), encoding="utf-8"))
        doc = open(os.path.join(REPO, "docs", "agent-desktop-command-audit.md"), encoding="utf-8").read()
        check("agent-desktop 的指令對照表是對 versions.json 釘的版本做的（%s）" % (vj["agent-desktop"]["version"]),
              ("audited version: %s" % vj["agent-desktop"]["version"]) in doc, "versions.json=%s" % vj["agent-desktop"]["version"])
        o, rc = ned("LINE.exe", "snapshot", "-i")
        check("未授權敏感 app（LINE）→ 拒", rc == 3 and o.get("ok") is False, o)
        o, rc = ned("Outlook.exe", "screenshot", "x.png")
        check("未授權郵件 app → 拒", rc == 3, o)
        o, rc = ned("not a/valid;name", "snapshot")
        check("非正規 app 名 → 拒", rc == 3, o)
        # 三平台名稱：macOS 顯示名稱 / bundle id、Linux 名稱都要被正規化成敏感類別
        for nm in ("Microsoft Outlook", "com.apple.MobileSMS", "Messages", "Mail", "FaceTime", "jp.naver.line.mac",
                   "LINE.app", "telegram-desktop", "signal-desktop", "Thunderbird", "org.gnome.Evolution", "Slack"):
            o, rc = ned(nm, "snapshot", "-i")
            check("三平台敏感名稱拒絕：" + nm, rc == 3 and "同意閘門" in o.get("error", ""), o)
        for nm in ("Google Chrome", "gnome-calculator", "com.apple.TextEdit"):
            o, rc = ned(nm, "snapshot", "-i")
            check("一般 app（含空白/bundle id）放行：" + nm, rc == 0, o)
        for nm in (r"C:\x\a.exe", "../evil", "a\"b", " Notepad.exe"):
            o, rc = ned(nm, "snapshot")
            check("路徑/引號/前後空白 → 拒：" + repr(nm), rc == 3, o)
        subprocess.run([sys.executable, os.path.join(SC, "policy.py"), "grant", "line", "--reason", "test"],
                       env=env, capture_output=True)
        o, rc = ned("jp.naver.line.mac", "snapshot", "-i")
        check("grant line 也認得 macOS bundle id（仍受未讀保護）", rc == 3 and "未讀保護" in o.get("error", ""), o)
        o, rc = ned("Notepad.exe", "click", "@sforeign9:e1")
        check("外來 ref → 拒", rc == 3 and "不是由 ned 產生" in o.get("error", ""), o)
        o, rc = ned("Notepad.exe", "focus", "@splain1:e1")
        check("背景模式 focus → 拒", rc == 3, o)
        o, rc = ned("Notepad.exe", "type", "@splain1:e1", "hi")
        check("背景模式 type → 拒", rc == 3, o)

        o, rc = ned("Notepad.exe", "snapshot", "-i")
        check("一般 app snapshot 放行", rc == 0 and o.get("ok"), o)
        o, rc = ned("Notepad.exe", "click", "@splain1:e1")
        check("同一 app 的 ref click 放行", rc == 0 and o.get("data", {}).get("did") == "click", o)
        o, rc = ned("Calc.exe", "click", "@splain1:e1")
        check("跨 app ref → 拒", rc == 3 and "跨 app" in o.get("error", ""), o)

        o, rc = ned("chrome.exe", "snapshot", "-i", "--outbox")
        check("含 NE-OUTBOX 的 snapshot 本身可拍", rc == 0, o)
        st = json.load(open(os.path.join(home, "ned-state.json"), encoding="utf-8"))
        check("snapshot 歸屬記下發件匣標記", st.get("soutbox1", {}).get("outbox") is True, st)
        o, rc = ned("chrome.exe", "click", "@soutbox1:e3")
        check("發件匣 snapshot 的 ref click → 拒", rc == 3 and "發件匣" in o.get("error", ""), o)

        # home 解析：從 <home>/bin 執行、沒有 NO_ERRANDS_HOME
        h2 = tempfile.mkdtemp(prefix="ne-ned-home2-")
        os.makedirs(os.path.join(h2, "bin"))
        for f in ("ned.py", "policy.py", "nowin.py"):
            shutil.copyfile(os.path.join(SC, f), os.path.join(h2, "bin", f))
        open(os.path.join(h2, "config.json"), "w").write("{}")
        e2 = {k: v for k, v in env.items() if k != "NO_ERRANDS_HOME"}
        o, rc = ned("LINE.exe", "snapshot", e=e2, script=os.path.join(h2, "bin", "ned.py"))
        check("從 <home>/bin 執行 → ledger 寫進該 home", os.path.exists(os.path.join(h2, "ned-ledger.jsonl")), os.listdir(h2))
        shutil.rmtree(h2, ignore_errors=True)

        # ---- agent-desktop 指令對照後補的反例（2026-11-22）----
        o, rc = ned("Notepad.exe", "launch", r"C:\Users\x\AppData\Local\LINE\bin\current\LINE.exe")
        check("借一般 app 名義 launch 別的程式（事故一的模式）→ 拒", rc == 3 and "launch" in o.get("error", ""), o)
        o, rc = ned("Notepad.exe", "launch", "notepad.exe")
        check("launch 宣告的 app 本身 → 放行", rc == 0, o)
        ned("Notepad.exe", "snapshot")
        o, rc = ned("Notepad.exe", "click", "@splain1:e1", "--headed")
        check("背景模式 --headed（實體輸入）→ 拒", rc == 3 and "headed" in o.get("error", ""), o)
        for sv in ("notification-center", "focused", "desktop", "taskbar", "start-menu"):
            o, rc = ned("Notepad.exe", "snapshot", "--surface", sv)
            check("snapshot --surface %s（讀到別的 app）→ 拒" % sv, rc == 3, o)
        o, rc = ned("Notepad.exe", "snapshot", "--surface", "menu")
        check("snapshot --surface menu（自己的選單）→ 放行", rc == 0, o)
        o, rc = ned("Notepad.exe", "wait", "--notification")
        check("wait --notification（讀所有通知）→ 拒", rc == 3, o)
        o, rc = ned("Notepad.exe", "wait", "--element", "@sforeign9:e1")
        check("wait 用不是 ned 拍的 ref → 拒", rc == 3, o)
        for cmd in (["batch", "[]"], ["list-notifications"], ["notification-action", "0", "Reply"], ["clipboard-get"],
                    ["clipboard-set", "x"], ["mcp"], ["sandbox", "exec", "x"], ["press", "return"],
                    ["mouse-click", "10", "10"], ["focus-window"], ["open-system-surface", "start-menu"]):
            o, rc = ned("Notepad.exe", *cmd)
            check("ned %s → 拒（不在允許清單）" % cmd[0], rc == 3, o)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    bad = RESULTS.count(False)
    print("ALL PASS (%d)" % len(RESULTS) if not bad else "FAILED %d/%d" % (bad, len(RESULTS)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
