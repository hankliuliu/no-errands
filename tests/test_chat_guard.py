#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_guard 守門驗收 B3（Windows；需要 agent-desktop 與 PowerShell）。

對假聊天視窗（scenarios/fake-chat/fakechat.ps1，以 -NoActivate 啟動、不搶焦點）跑：
  B3-1 白名單項目（有未讀）→ locate/open/read/export/close 成功，違規偵測器為空
  B3-2 非白名單、未讀保護 on：無指紋 → 拒；locate 後狀態改變 → 拒；有未讀 → 拒；未變且無未讀 → 開
  B3-3 對輸入框 set-value、對送出/表情 click、對非目標項目 click → 全被擋，違規偵測器仍為空
  B3-4 名稱找不到、前綴、重複 → 拒
  B3-6 群組名稱結尾的人數標示（-CountNames，名稱全是虛構的）：人數變了照樣找得到；名稱本身以括號數字結尾不被誤削；
       名稱含「/」（預設檔名被換成「_」）；全形括號；帶人數整串存進白名單；去掉人數後同名 → 拒
  B3-7 存檔對話框延遲 10 秒才出現（像 LINE 準備大群組的匯出）：等得到、只點一次、只存一份、沒有殘留對話框
  B3-8 probe（只讀探索）：白名單對話點開記事本 → 放行、輸出不含名稱與內容、開出來的視窗有關掉；不在白名單 → 拒絕、沒點開
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
    ap.add_argument("--standin", action="store_true",
                    help="CI only: put an ordinary 'user' window in front first, so focus hand-back has a target")
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

    standin = None
    if a.standin:
        import ctypes
        sps = os.path.join(home, "standin.ps1")
        open(sps, "w", encoding="ascii").write(chr(10).join([
            "Add-Type -AssemblyName System.Windows.Forms", "$f = New-Object System.Windows.Forms.Form",
            "$f.Text = 'STANDIN-USER'; $f.Width = 500; $f.Height = 300",
            "[System.Windows.Forms.Application]::Run($f)", ""]))
        standin = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", sps])
        for _ in range(50):
            time.sleep(0.2)
            hw = ctypes.windll.user32.FindWindowW(None, "STANDIN-USER")
            if hw:
                ctypes.windll.user32.SetForegroundWindow(hw); break
        time.sleep(0.8)
    try:
        before = fg()
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Start-Process powershell -WindowStyle Hidden -ArgumentList "
                        "'-NoProfile','-STA','-ExecutionPolicy','Bypass','-File','%s','-StateDir','%s','-NoActivate','-CountNames'"
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
        _p = os.path.join(home, "policy.json"); _d = json.load(open(_p, encoding="utf-8")); _d["idle_min_s"] = 0
        json.dump(_d, open(_p, "w", encoding="utf-8"), ensure_ascii=False)   # 測試：閒置閘門不等（輸入監看仍在）
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
        RESULTS.append({"name": "B3-1 export 存檔路徑（資訊）", "ok": True,
                        "detail": "saved_via=%s dialog_folder_known=%s" % (o.get("saved_via"), o.get("dialog_folder_known"))})
        print("INFO B3-1 export saved_via=%s dialog_folder_known=%s" % (o.get("saved_via"), o.get("dialog_folder_known")))
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

        # B3-6 人數標示
        for n in ("Field Crew (21)", "Report(2024)", "11/30 Buoy Test", "Echo", "Kilo"):
            pol("whitelist", "add", n)
        exp6 = os.path.join(home, "export6")

        def last_open():
            ev = [json.loads(l) for l in open(os.path.join(state, "events.jsonl"), encoding="utf-8") if l.strip()]
            return ([e.get("chat") for e in ev if e.get("kind") == "open"] or [None])[-1]

        def open_export(chat):
            o1 = cg("open", "--chat", chat)
            o2 = cg("export", "--chat", chat, "--to", exp6) if o1.get("ok") else {}
            who = last_open()
            cg("close")
            return o1, o2, who

        o1, o2, who = open_export("Field Crew")
        check("B3-6 純名稱找得到「Field Crew (21)」（白名單存的是帶人數的整串）", o1.get("ok") and o2.get("ok") and who == "Field Crew", (o1, o2, who))
        trigger({"op": "members", "chat": "Field Crew", "count": 22})
        o1, o2, who = open_export("Field Crew (21)")
        check("B3-6 人數 21→22 後，用舊的帶人數名稱還是同一個群組", o1.get("ok") and o2.get("ok") and who == "Field Crew", (o1, o2, who))
        o1, o2, who = open_export("Report(2024)")
        check("B3-6 名稱以括號數字結尾：開到 Report(2024)、不是人數 2024 的 Report", o1.get("ok") and o2.get("ok") and who == "Report(2024)", (o1, o2, who))
        o1, o2, who = open_export("11/30 Buoy Test")
        check("B3-6 名稱含斜線：預設檔名被換成底線也通過身分檢查", o1.get("ok") and o2.get("ok")
              and (o2.get("verified") or {}).get("default_filename") is True and who == "11/30 Buoy Test", (o1, o2, who))
        o1, o2, who = open_export("Kilo")
        check("B3-6 全形括號人數", o1.get("ok") and o2.get("ok") and who == "Kilo", (o1, o2, who))
        o = cg("open", "--chat", "Echo")
        check("B3-6 去掉人數後同名的兩個群組 → 拒、說明原因", o.get("refused") is True and "同名" in o.get("error", ""), o)
        check("B3-6 違規偵測器為空", violations() == [], violations())

        # B3-7 對話框遲到
        def events(kind):
            return sum(1 for l in open(os.path.join(state, "events.jsonl"), encoding="utf-8") if '"%s"' % kind in l)
        trigger({"op": "dialogdelay", "ms": 10000})
        s0, e0 = events("save-clicked"), events("export")
        exp7 = os.path.join(home, "export7")
        cg("open", "--chat", "Kilo")
        t7 = time.time()
        o = cg("export", "--chat", "Kilo", "--to", exp7)
        secs7 = round(time.time() - t7, 1)
        cg("close")
        lw = json.loads(subprocess.run([a.ad, "list-windows"], capture_output=True, text=True, encoding="utf-8").stdout)
        check("B3-7 對話框延遲 10 秒：匯出成功（%.1f 秒）" % secs7, o.get("ok") and os.path.exists(o.get("path", "")), o)
        check("B3-7 只點了一次「儲存聊天」（點擊送到後不重點）", events("save-clicked") - s0 == 1, events("save-clicked") - s0)
        check("B3-7 只存了一份", events("export") - e0 == 1, events("export") - e0)
        check("B3-7 沒有殘留的存檔對話框", not any(w.get("title") == "Save chat history" for w in lw.get("data", [])))
        trigger({"op": "reset"})

        # B3-8 probe
        n0, c0 = events("notes-open"), events("notes-close")
        cg("open", "--chat", "Kilo")
        o = cg("probe", "--chat", "Kilo", "--view", "notes")
        dump = json.dumps(o, ensure_ascii=False)
        check("B3-8 probe 白名單對話的記事本：放行、點開了一次", o.get("ok") and events("notes-open") - n0 == 1, o.get("error") or o.keys())
        check("B3-8 probe 輸出不含對話名稱與記事內容", "Kilo" not in dump and "deploy" not in dump and "Ann" not in dump, dump[:300])
        lw = json.loads(subprocess.run([a.ad, "list-windows"], capture_output=True, text=True, encoding="utf-8").stdout)
        check("B3-8 probe 收尾：開出來的記事本視窗已關掉", o.get("closed_windows", 0) >= 1 and o.get("windows_left_open") == 0
              and events("notes-close") - c0 == 1, o)
        # notes-tab：在記事本視窗裡只點「記事本」分頁（只讀切換），讀完照樣關掉
        nt0, c1 = events("notes-tab"), events("notes-close")
        o = cg("probe", "--chat", "Kilo", "--view", "notes-tab")
        check("B3-8 probe notes-tab：分頁切換點到一次、視窗有關、輸出不含內容", o.get("ok") and (o.get("tab_click") or {}).get("done")
              and events("notes-tab") - nt0 == 1 and events("notes-close") - c1 == 1 and "deploy" not in json.dumps(o, ensure_ascii=False), o.get("tab_click"))
        led = [json.loads(l) for l in open(os.path.join(home, "chat-guard-ledger.jsonl"), encoding="utf-8") if l.strip()]
        check("B3-8 分頁點擊留在 ledger（投遞滑鼠訊息的點擊也查得到）", any(e.get("sub") == "probe-tab-click" and e.get("allowed") for e in led),
              [e.get("sub") for e in led][-5:])
        check("B3-8 notes-tab 回報兩顆分頁的選取狀態（點擊前後）", set((o.get("tab_state") or {}).get("after", {})) >= {"相簿", "記事本"}, o.get("tab_state"))
        # 看一眼：只截這次開出的記事本視窗，存在該目標收集資料夾的暫存處，ledger 記一筆
        o = cg("probe", "--chat", "Kilo", "--view", "notes", "--peek")
        pk = (o.get("peek") or {}).get("path") or ""
        okpng = bool(pk) and os.path.exists(pk) and open(pk, "rb").read(4) == bytes([0x89, 0x50, 0x4E, 0x47])
        check("B3-8 peek：截到記事本視窗（PNG）、放在收集資料夾的 _peek 底下", okpng and ("%sgather%s" % (os.sep, os.sep)) in pk and "_peek" in pk, o.get("peek"))
        check("B3-8 peek：輸出不含內容、視窗照樣關掉", "deploy" not in json.dumps(o, ensure_ascii=False) and o.get("windows_left_open") == 0, o.get("windows_left_open"))
        led = [json.loads(l) for l in open(os.path.join(home, "chat-guard-ledger.jsonl"), encoding="utf-8") if l.strip()]
        check("B3-8 peek 留在 ledger", any(e.get("sub") == "probe-peek" and e.get("allowed") for e in led), [e.get("sub") for e in led][-4:])
        if pk and os.path.exists(pk):
            os.remove(pk)
        # notes-deep：只看結構（子視窗類別、RawView 統計、Text/Value 模式長度），不點、不截、不輸出文字
        o = cg("probe", "--chat", "Kilo", "--view", "notes-deep")
        dp = o.get("deep") or {}
        check("B3-8 notes-deep：回報子視窗類別與 RawView 統計、輸出不含任何文字", o.get("ok") and (dp.get("scan_root") or {}).get("ok")
              and (dp["scan_root"].get("nodes") or 0) > 0 and "deploy" not in json.dumps(o, ensure_ascii=False)
              and "Ann" not in json.dumps(o, ensure_ascii=False) and o.get("windows_left_open") == 0, {k: dp.get(k) for k in ("error",)} or dp.get("scan_root"))
        # 記事本開著時 LINE 剛好跳出別的有標題視窗（像來電）：兩個新視窗 → 都不關（關掉來電可能等於拒接），照實回報
        trigger({"op": "callonnotes"})
        o = cg("probe", "--chat", "Kilo", "--view", "notes")
        check("B3-8 probe 期間跳出不相干的視窗：不關、照實回報 windows_left_open", o.get("ok") and o.get("windows_left_open", 0) >= 1
              and events("call-closed") == 0 and events("call-shown") == 1, o)
        import ctypes
        for ttl in ("NE-CALL", "記事本"):   # 測試自己收掉
            hw = ctypes.windll.user32.FindWindowW(None, ttl)
            if hw:
                ctypes.windll.user32.PostMessageW(hw, 0x0010, 0, 0)
        time.sleep(1.0)
        cg("close")
        n1 = events("notes-open")
        cg("open", "--chat", "Delta Friends", "--expect", cg("locate", "--chat", "Delta Friends").get("fingerprint") or "x")
        o = cg("probe", "--chat", "Delta Friends", "--view", "notes")
        check("B3-8 probe 不在白名單 → 拒絕、記事本沒被點開", o.get("refused") is True and "白名單" in o.get("error", "")
              and events("notes-open") == n1, (o, events("notes-open"), n1))
        cg("close")
        check("B3-8 違規偵測器為空", violations() == [], violations())

        o = cg("send", "--chat", "Alpha Team")
        check("沒有送出子指令", o.get("refused") is True, o)
        check("全程違規偵測器為空", violations() == [], violations())
        check("每次搶前景都已交還（%d 次）" % len(HANDBACKS), all(HANDBACKS), HANDBACKS)
    finally:
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        if standin:
            standin.kill()
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
