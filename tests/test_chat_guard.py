#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_guard 守門驗收 B3（Windows；需要 agent-desktop 與 PowerShell）。

對假聊天視窗（scenarios/fake-chat/fakechat.ps1，以 -NoActivate 啟動、不搶焦點）跑：
  B3-1 白名單項目（有未讀）→ locate/open/read/export/close 成功，違規偵測器為空
  B3-2 非白名單、未讀保護 on：無指紋 → 拒；locate 後狀態改變 → 拒；有未讀 → 拒；未變且無未讀 → 開
  B3-3 對輸入框 set-value、對送出/表情 click、對非目標項目 click → 全被擋，違規偵測器仍為空
  B3-4 名稱找不到、前綴、重複 → 拒
  另：未授權的 app → 拒；沒有送出子指令
每一步前後記錄前景視窗 handle 與游標位置（N4 不干擾量測），結果寫 JSON。

用法：python tests/test_chat_guard.py --ad <agent-desktop.exe> [--out result.json]
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
FAKE = os.path.join(REPO, "scenarios", "fake-chat", "fakechat.ps1")
FG = os.path.join(REPO, "scenarios", "fake-chat", "fg.ps1")
RESULTS, STEPS, HANDBACKS = [], [], []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "ok": bool(cond), "detail": "" if cond else str(detail)[:300]})
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -- " + str(detail)[:300]))


def fg():
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", FG],
                       capture_output=True, text=True)
    return json.loads(p.stdout.strip().splitlines()[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ad", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    home = tempfile.mkdtemp(prefix="ne-cg-test-")
    state = os.path.join(home, "fakechat-state")
    os.makedirs(os.path.join(home, "bin"))
    shutil.copyfile(a.ad, os.path.join(home, "bin", "agent-desktop.exe"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    viol = os.path.join(state, "violations.jsonl")
    trig = os.path.join(state, "trigger.jsonl")
    pid = None

    def pol(*args):
        subprocess.run([sys.executable, os.path.join(SC, "policy.py")] + list(args) + ["--reason", "test"],
                       env=env, capture_output=True)

    def cg(*args, app="fake-chat"):
        before = fg()
        t0 = time.time()
        p = subprocess.run([sys.executable, os.path.join(SC, "chat_guard.py"), app] + list(args),
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
        after = fg()
        try:
            o = json.loads(p.stdout)
        except Exception:
            o = {"raw": p.stdout[-300:], "err": p.stderr[-300:]}
        if o.get("foreground_taken"):
            HANDBACKS.append(o.get("foreground_returned"))
        STEPS.append({"step": " ".join(args[:3]), "ok": o.get("ok"), "secs": round(time.time() - t0, 1),
                      "fg_changed": before["hwnd"] != after["hwnd"], "fg_after_ours": after["ours"],
                      "cursor_moved": (before["x"], before["y"]) != (after["x"], after["y"])})
        return o

    def violations():
        if not os.path.exists(viol):
            return []
        return [json.loads(l) for l in open(viol, encoding="utf-8") if l.strip()]

    def trigger(obj):
        with open(trig, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        for _ in range(30):
            time.sleep(0.2)
            if not os.path.exists(trig):
                break
        time.sleep(0.3)

    try:
        before = fg()
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Start-Process powershell -WindowStyle Hidden -ArgumentList "
                        "'-NoProfile','-STA','-ExecutionPolicy','Bypass','-File','%s','-StateDir','%s','-NoActivate'"
                        % (FAKE, state)])
        for _ in range(40):
            time.sleep(0.3)
            ev = os.path.join(state, "events.jsonl")
            if os.path.exists(ev) and os.path.getsize(ev) > 0:
                pid = json.loads(open(ev, encoding="utf-8").readline())["pid"]
                break
        time.sleep(1.0)
        after = fg()
        check("啟動假視窗（-NoActivate）不改前景、不動游標", pid and before["hwnd"] == after["hwnd"]
              and (before["x"], before["y"]) == (after["x"], after["y"]), (before, after))

        o = cg("locate", "--chat", "Alpha Team")
        check("閘門：未授權 app → 拒", o.get("refused") is True, o)
        pol("grant", "fake-chat")
        pol("whitelist", "add", "Alpha Team")

        # B3-1
        o = cg("locate", "--chat", "Alpha Team")
        check("B3-1 locate 白名單（有未讀）", o.get("ok") and o.get("matches") == 1 and o.get("fingerprint"), o)
        check("B3-1 locate 輸出不含 app 字串", "unread" not in json.dumps(o) and "Ann" not in json.dumps(o), o)
        o = cg("open", "--chat", "Alpha Team")
        check("B3-1 open 白名單（有未讀）直接開", o.get("ok") and o.get("via") == "whitelist", o)
        o = cg("read")
        check("B3-1 read 沒帶 --chat → 拒", o.get("refused") is True, o)
        o = cg("read", "--chat", "Beta Project")
        check("B3-1 read 的對話不是目前開啟的 → 拒", o.get("refused") is True and "標頭" in o.get("error", ""), o)
        o = cg("read", "--chat", "Alpha Team")
        check("B3-1 read 讀到訊息", o.get("ok") and "Ann: morning all" in (o.get("messages") or []), o)
        exp = os.path.join(home, "export")
        o = cg("export", "--chat", "Alpha Team", "--to", exp)
        check("B3-1 export 經另存新檔對話框寫出 .txt", o.get("ok") and o.get("lines", 0) >= 2
              and os.path.exists(o.get("path", "")), o)
        check("B3-1 export 通過預設檔名與首行身分驗證", (o.get("verified") or {}).get("default_filename") is True
              and (o.get("verified") or {}).get("first_line") is True, o)
        # B3-5 身分驗證失敗的兩種情況
        exp5 = os.path.join(home, "export5")
        trigger({"op": "exportname", "name": "[NE] Chat with Someone Else.txt"})
        o = cg("export", "--chat", "Alpha Team", "--to", exp5)
        check("B3-5 預設檔名不含目標 → 拒、按取消", o.get("refused") is True and "預設檔名" in o.get("error", ""), o)
        check("B3-5 預設檔名不符時沒有產生任何檔案", not os.path.isdir(exp5) or os.listdir(exp5) == [],
              os.listdir(exp5) if os.path.isdir(exp5) else None)
        lw = json.loads(subprocess.run([a.ad, "list-windows"], capture_output=True, text=True, encoding="utf-8").stdout)
        check("B3-5 取消後另存新檔對話框已關閉", not any(w.get("title") == "Save chat history" for w in lw.get("data", [])))
        trigger({"op": "reset"})
        trigger({"op": "firstline", "text": "# Someone Else"})
        o = cg("export", "--chat", "Alpha Team", "--to", exp5)
        check("B3-5 首行不含目標 → 拒", o.get("refused") is True and "第一行" in o.get("error", ""), o)
        check("B3-5 首行不符的檔案已被刪除", os.listdir(exp5) == [], os.listdir(exp5))
        trigger({"op": "reset"})
        if o.get("ok"):
            r = cg("read", "--file", o["path"])
            check("B3-1 read --file 讀匯出檔", r.get("ok") and "Ann: morning all" in (r.get("lines") or []), r)
        o = cg("close")
        check("B3-1 close", o.get("ok"), o)
        check("B3-1 違規偵測器為空", violations() == [], violations())

        # B3-2
        o = cg("open", "--chat", "Beta Project")
        check("B3-2 非白名單無指紋 → 拒", o.get("refused") is True, o)
        fp = cg("locate", "--chat", "Beta Project").get("fingerprint")
        trigger({"op": "message", "chat": "Beta Project", "text": "Cara: one more thing"})
        o = cg("open", "--chat", "Beta Project", "--expect", fp or "x")
        check("B3-2 locate 後狀態改變（新訊息）→ 拒", o.get("refused") is True and "指紋" in o.get("error", ""), o)
        fp2 = cg("locate", "--chat", "Beta Project").get("fingerprint")
        o = cg("open", "--chat", "Beta Project", "--expect", fp2 or "x")
        check("B3-2 指紋相符但有未讀 → 拒", o.get("refused") is True and "未讀" in o.get("error", ""), o)
        fp3 = cg("locate", "--chat", "Delta Friends").get("fingerprint")
        o = cg("open", "--chat", "Delta Friends", "--expect", fp3 or "x")
        check("B3-2 非白名單、無未讀、狀態未變 → 開", o.get("ok") and o.get("via") == "fingerprint", o)
        cg("close")

        # B3-3
        cg("open", "--chat", "Alpha Team")
        for act, target, label in (("set-value", "message input", "對輸入框 set-value"),
                                   ("click", "Send", "對送出鈕 click"),
                                   ("click", "Emoji", "對表情鈕 click"),
                                   ("click", "Gamma Lab", "對非目標清單項目 click"),
                                   ("click", "Export chat", "步驟外的選單 click")):
            args = ["act", act, "--target", target] + (["--text", "hi"] if act == "set-value" else [])
            o = cg(*args)
            check("B3-3 " + label + " → 擋下", o.get("refused") is True, o)
        cg("close")
        check("B3-3 違規偵測器仍為空", violations() == [], violations())

        # B3-4
        check("B3-4 找不到 → 拒", cg("locate", "--chat", "Nobody").get("ok") is False)
        check("B3-4 前綴不算符合 → 拒", cg("locate", "--chat", "Alpha").get("ok") is False)
        trigger({"op": "rename", "chat": "Alpha Team Ops", "name": "Alpha Team | Sun | Finn: server rebooted"})
        o = cg("locate", "--chat", "Alpha Team")
        check("B3-4 名稱重複 → 拒", o.get("ok") is False and o.get("matches") == 2, o)
        o = cg("open", "--chat", "Alpha Team")
        check("B3-4 名稱重複時白名單也不開", o.get("refused") is True, o)

        o = cg("send", "--chat", "Alpha Team")
        check("沒有送出子指令", o.get("refused") is True, o)
        check("全程違規偵測器為空", violations() == [], violations())
        check("每次搶前景都已交還（%d 次）" % len(HANDBACKS), all(HANDBACKS), HANDBACKS)
    finally:
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(0.5)
        shutil.rmtree(home, ignore_errors=True)
    bad = [r for r in RESULTS if not r["ok"]]
    interf = [s for s in STEPS if s["fg_changed"] or s["cursor_moved"]]
    print("\nSTEPS with interference: %d / %d" % (len(interf), len(STEPS)))
    for s in interf:
        print("  ", s)
    print("ALL PASS (%d)" % len(RESULTS) if not bad else "FAILED %d/%d" % (len(bad), len(RESULTS)))
    if a.out:
        json.dump({"results": RESULTS, "steps": STEPS}, open(a.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
