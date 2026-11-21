#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0.8 與既有環境共存（純檔案、無視窗、三平台）。預先放好 binary，不下載。
  1. 既有 CLAUDE.md（含一段像標記的文字）→ 安裝後原文逐位元組保留在受管區塊前；移除後整個檔案逐位元組還原
  2. 既有 settings.json（hooks + permissions）→ 一般安裝完全不碰（逐位元組相同）
  3. 已有同名 skill 目錄但不是我們裝的 → 中止、不覆蓋；移除時也不刪它
  4. 已有可用 Python → stage-0 直接用，不下載 uv
"""
import json, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


def seeded_home(td):
    home = os.path.join(td, "home")
    b = os.path.join(home, "bin"); os.makedirs(b)
    ver = json.load(open(os.path.join(REPO, "versions.json"), encoding="utf-8"))
    for t in ("agent-desktop", "agent-browser"):
        open(os.path.join(b, t + (".exe" if os.name == "nt" else "")), "wb").write(b"stub")
    json.dump({t: ver[t]["version"] for t in ("agent-desktop", "agent-browser")}, open(os.path.join(b, ".installed.json"), "w"))
    return home


def setup(*a):
    p = subprocess.run([sys.executable, os.path.join(REPO, "setup.py")] + list(a), capture_output=True, text=True,
                       encoding="utf-8")
    try:
        return json.loads(p.stdout), p.returncode
    except Exception:
        return {"raw": p.stdout[-300:], "err": p.stderr[-300:]}, p.returncode


def main():
    td = tempfile.mkdtemp(prefix="ne-coexist-")
    try:
        home = seeded_home(td)
        proj = os.path.join(td, "proj"); os.makedirs(os.path.join(proj, ".claude"))
        original_md = ("# 我的專案規則\n\n- 用 2 空白縮排\n<!-- no-errands 這不是標記，只是看起來像 -->\n"
                       "## 其他\n內容 with trailing spaces   \n").encode("utf-8")
        open(os.path.join(proj, "CLAUDE.md"), "wb").write(original_md)
        original_settings = json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
                                        "permissions": {"allow": ["Bash(npm test:*)"]}}, indent=4).encode("utf-8")
        open(os.path.join(proj, ".claude", "settings.json"), "wb").write(original_settings)

        o, rc = setup("--project", proj, "--home", home)
        check("安裝成功", rc == 0 and o.get("ok"), o)
        md = open(os.path.join(proj, "CLAUDE.md"), "rb").read()
        check("1 原 CLAUDE.md 逐位元組保留在受管區塊之前", md.startswith(original_md) and b"<!-- no-errands:start -->" in md)
        check("2 settings.json 逐位元組沒動", open(os.path.join(proj, ".claude", "settings.json"), "rb").read() == original_settings)
        o, rc = setup("--project", proj, "--home", home, "--uninstall")
        check("1 移除後 CLAUDE.md 逐位元組還原", open(os.path.join(proj, "CLAUDE.md"), "rb").read() == original_md,
              open(os.path.join(proj, "CLAUDE.md"), "rb").read()[-80:])

        # 3. 同名 skill 不是我們的
        proj2 = os.path.join(td, "proj2"); sk = os.path.join(proj2, ".claude", "skills", "no-errands"); os.makedirs(sk)
        open(os.path.join(sk, "SKILL.md"), "w", encoding="utf-8").write("---\nname: no-errands\n---\n別人的同名 skill\n")
        o, rc = setup("--project", proj2, "--home", home)
        check("3 同名 skill 不是我們的 → 中止", rc != 0 and not o.get("ok") and "不是本工具裝的" in json.dumps(o, ensure_ascii=False), o)
        check("3 別人的 skill 內容未被覆蓋", "別人的同名 skill" in open(os.path.join(sk, "SKILL.md"), encoding="utf-8").read())
        check("3 中止時沒有寫 CLAUDE.md", not os.path.exists(os.path.join(proj2, "CLAUDE.md")))
        o, rc = setup("--project", proj2, "--home", home, "--uninstall")
        check("3 移除時也不刪別人的 skill", os.path.exists(os.path.join(sk, "SKILL.md")))

        # 4. 已有 Python → stage-0 不下載 uv
        home4 = seeded_home(os.path.join(td, "h4")) if os.makedirs(os.path.join(td, "h4")) is None else None
        proj4 = os.path.join(td, "proj4"); os.makedirs(proj4)
        if os.name == "nt":
            p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(REPO, "setup.ps1"),
                                "--project", proj4, "--home", home4], capture_output=True, text=True)
        else:
            p = subprocess.run(["sh", os.path.join(REPO, "setup.sh"), "--project", proj4, "--home", home4], capture_output=True, text=True)
        bins = os.listdir(os.path.join(home4, "bin"))
        check("4 stage-0 安裝成功", p.returncode == 0, (p.stdout[-200:], p.stderr[-200:]))

        def usable_python():   # stage-0 的判準：PATH 上真的能執行的 Python >= 3.9（Microsoft Store 假捷徑不算）
            # Windows 要經 PowerShell 問（和 setup.ps1 一樣）：從 Python 子程序呼叫 "python" 會先找到父程序自己的資料夾
            for c in (["python3"], ["python"], ["py"]):
                try:
                    if os.name == "nt":
                        v = subprocess.run(["powershell", "-NoProfile", "-Command", "& %s --version" % c[0]],
                                           capture_output=True, text=True, errors="replace", timeout=20)
                    else:
                        v = subprocess.run(c + ["--version"], capture_output=True, text=True, timeout=10)
                    out = (v.stdout + v.stderr).strip()
                    if v.returncode == 0 and out.startswith("Python 3.") and int(out.split(".")[1]) >= 9:
                        return True
                except Exception:
                    pass
            return False
        if usable_python():
            check("4 有可用 Python → 沒有下載 uv", not any(b.startswith("uv") for b in bins), bins)
        else:
            print("SKIP 4 這台機器 PATH 上沒有可用 Python（例如只有 Microsoft Store 假捷徑），下載 uv 是正確行為")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
