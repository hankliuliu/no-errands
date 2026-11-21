#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範／教學模式（政策 visibility=watch 時用）：每一步放慢、執行、只截那個視窗，最後產出圖文步驟。

  demo.py begin  --out <資料夾> --title "<標題>"
  demo.py step   --out <資料夾> --say "<這一步在做什麼>" [--window <window-id> | --browser <session>] [--delay 1.5] -- <指令…>
  demo.py finish --out <資料夾>          # 產生 <資料夾>/guide.md（編號步驟 + 截圖）

截圖只拍指定的那一個視窗或瀏覽器頁面（不拍全螢幕）。截圖可能含個資：留本機，分享前自己看過。
"""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402

HOME = policy.HOME


def tool(name):
    return os.path.join(HOME, "bin", name + (".exe" if os.name == "nt" else ""))


def arg(a, k, d=None):
    return a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else d


def load(out):
    p = os.path.join(out, "steps.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"title": "", "steps": []}


def save(out, d):
    json.dump(d, open(os.path.join(out, "steps.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    out = os.path.abspath(arg(a, "--out", "demo-out"))
    os.makedirs(out, exist_ok=True)
    if a[0] == "begin":
        save(out, {"title": arg(a, "--title", "示範"), "started": time.strftime("%Y-%m-%d %H:%M"), "steps": []})
        print(json.dumps({"ok": True, "out": out})); return
    if a[0] == "step":
        d = load(out)
        cmd = a[a.index("--") + 1:] if "--" in a else []
        time.sleep(float(arg(a, "--delay", "1.5")))          # 放慢：讓看的人跟得上
        rc, tail = None, ""
        if cmd:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            rc, tail = p.returncode, (p.stdout or "")[-300:]
        n = len(d["steps"]) + 1
        shot = os.path.join(out, "step-%02d.png" % n)
        if arg(a, "--window"):
            subprocess.run([tool("agent-desktop"), "screenshot", shot, "--window-id", arg(a, "--window")],
                           capture_output=True)
        elif arg(a, "--browser"):
            subprocess.run([tool("agent-browser"), "--session", arg(a, "--browser"), "screenshot", shot.replace("\\", "/")],
                           capture_output=True)
        d["steps"].append({"n": n, "say": arg(a, "--say", ""), "cmd": " ".join(cmd)[:200], "rc": rc,
                           "shot": os.path.basename(shot) if os.path.exists(shot) else None})
        save(out, d)
        print(json.dumps({"ok": rc in (None, 0), "step": n, "shot": d["steps"][-1]["shot"]}, ensure_ascii=False)); return
    if a[0] == "finish":
        d = load(out)
        lines = ["# %s" % d["title"], "", "_%s，由 no-errands 示範模式自動產生。_" % d.get("started", ""), ""]
        for s in d["steps"]:
            lines.append("## %d. %s" % (s["n"], s["say"]))
            if s.get("cmd"):
                lines.append("`%s`%s" % (s["cmd"], "" if s["rc"] in (None, 0) else "（失敗，rc=%s）" % s["rc"]))
            if s.get("shot"):
                lines.append("")
                lines.append("![步驟 %d](%s)" % (s["n"], s["shot"]))
            lines.append("")
        open(os.path.join(out, "guide.md"), "w", encoding="utf-8").write("\n".join(lines))
        print(json.dumps({"ok": True, "guide": os.path.join(out, "guide.md"), "steps": len(d["steps"]),
                          "shots": sum(1 for s in d["steps"] if s.get("shot"))})); return
    print(json.dumps({"ok": False, "error": "未知子指令"})); sys.exit(2)


if __name__ == "__main__":
    main()
