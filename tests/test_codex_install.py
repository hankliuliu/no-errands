#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex 安裝位置測試（純 stdlib）：依官方文件，skill 放 <CODEX_HOME>/skills/ 與 ~/.agents/skills/，
全域指示放 <CODEX_HOME>/AGENTS.md。裝 → 檢查 → 移除 → 檢查乾淨。有 codex CLI 時印出版本。
（沒有 Codex 帳號：不做實際對話驗證。）"""
import json, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


td = tempfile.mkdtemp(prefix="ne-codex-")
try:
    home = os.path.join(td, "ne-home"); b = os.path.join(home, "bin"); os.makedirs(b)
    ver = json.load(open(os.path.join(REPO, "versions.json"), encoding="utf-8"))
    for t in ("agent-desktop", "agent-browser"):
        open(os.path.join(b, t + (".exe" if os.name == "nt" else "")), "wb").write(b"stub")
    json.dump({t: ver[t]["version"] for t in ("agent-desktop", "agent-browser")}, open(os.path.join(b, ".installed.json"), "w"))
    codex_home = os.path.join(td, "user", ".codex"); claude_dir = os.path.join(td, "user", ".claude")
    os.makedirs(claude_dir)
    run = lambda *a: subprocess.run([sys.executable, os.path.join(REPO, "setup.py"), "--user", "--home", home,
                                     "--claude-dir", claude_dir, "--codex", "--codex-home", codex_home] + list(a),
                                    capture_output=True, text=True, encoding="utf-8")
    p = run()
    check("安裝成功", p.returncode == 0, p.stdout[-300:] + p.stderr[-300:])
    sk1 = os.path.join(codex_home, "skills", "no-errands", "SKILL.md")
    sk2 = os.path.join(td, "user", ".agents", "skills", "no-errands", "SKILL.md")
    am = os.path.join(codex_home, "AGENTS.md")
    check("skill 在 <CODEX_HOME>/skills/no-errands/", os.path.exists(sk1))
    check("skill 在 ~/.agents/skills/no-errands/（新版文件的位置）", os.path.exists(sk2))
    check("SKILL.md 有 name 與 description", os.path.exists(sk1) and "name: no-errands" in open(sk1, encoding="utf-8").read()
          and "description:" in open(sk1, encoding="utf-8").read())
    check("<CODEX_HOME>/AGENTS.md 有受管區塊", os.path.exists(am) and "<!-- no-errands:start -->" in open(am, encoding="utf-8").read())
    p = run("--uninstall")
    check("移除後 skill 與區塊都清掉", not os.path.exists(sk1) and not os.path.exists(sk2) and not os.path.exists(am),
          [os.path.exists(x) for x in (sk1, sk2, am)])
    if shutil.which("codex"):
        v = subprocess.run(["codex", "--version"], capture_output=True, text=True)
        print("codex CLI:", (v.stdout or v.stderr).strip()[:80])
finally:
    shutil.rmtree(td, ignore_errors=True)
print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
