#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0.8 與既有環境共存（純檔案、無視窗、三平台）。預先放好 binary，不下載。
  1. 既有 CLAUDE.md（含一段像標記的文字）→ 安裝後原文逐位元組保留在受管區塊前；移除後整個檔案逐位元組還原
  2. 既有 settings.json（hooks + permissions）→ 一般安裝完全不碰（逐位元組相同）
  3. 已有同名 skill 目錄但不是我們裝的 → 中止、不覆蓋；移除時也不刪它
  4. 已有可用 Python → stage-0 直接用，不下載 uv
  5. install_skill 只動「skills/<名稱>」且帶我們標記的那一個資料夾：上層 skills 目錄、家目錄、別人的 skill、
     沒標記的同名資料夾一律拒絕且原封不動；換版先裝新版再換、中途失敗搬回；--sync 走同一套
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
                                "--project", proj4, "--home", home4], capture_output=True, text=True, errors="replace")
        else:
            p = subprocess.run(["sh", os.path.join(REPO, "setup.sh"), "--project", proj4, "--home", home4], capture_output=True, text=True, errors="replace")
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
                    out = ((v.stdout or "") + (v.stderr or "")).strip()
                    if v.returncode == 0 and out.startswith("Python 3.") and int(out.split(".")[1]) >= 9:
                        return True
                except Exception:
                    pass
            return False
        if usable_python():
            check("4 有可用 Python → 沒有下載 uv", not any(b.startswith("uv") for b in bins), bins)
        else:
            print("SKIP 4 這台機器 PATH 上沒有可用 Python（例如只有 Microsoft Store 假捷徑），下載 uv 是正確行為")
        # 5 install_skill 的防護
        sys.path.insert(0, REPO); import setup as S

        def tree(d):   # 整棵樹的（相對路徑, 內容）快照
            out = []
            for r, ds, fs in os.walk(d):
                ds.sort()
                for f in sorted(fs):
                    q = os.path.join(r, f); out.append((os.path.relpath(q, d), open(q, "rb").read()))
                for x in ds:
                    out.append((os.path.relpath(os.path.join(r, x), d) + "/", b""))
            return out

        def refused(*args):
            try:
                S.install_skill(*args)
            except ValueError:
                return True
            return False

        root = os.path.join(td, "u5", ".claude", "skills")
        for n in ("other-skill", "no-errands-lookalike"):
            os.makedirs(os.path.join(root, n)); open(os.path.join(root, n, "SKILL.md"), "w", encoding="utf-8").write("user " + n)
        own_nomark = os.path.join(root, "errand-audit"); os.makedirs(own_nomark)
        open(os.path.join(own_nomark, "SKILL.md"), "w", encoding="utf-8").write("使用者自己的同名 skill，沒有我們的標記")
        snap = tree(os.path.dirname(root))
        check("5 傳上層 skills 目錄 → 拒絕", refused(root) and refused(root, "errand-audit"))
        check("5 傳別人的 skill 資料夾 → 拒絕", refused(os.path.join(root, "other-skill")))
        check("5 沒有我們標記的同名資料夾 → 拒絕", refused(own_nomark, "errand-audit"))
        check("5 傳家目錄 / 不在 skills 底下一層 → 拒絕",
              refused(os.path.expanduser("~")) and refused(os.path.join(td, "u5", "no-errands")))
        check("5 拒絕之後整棵樹逐位元組沒變", tree(os.path.dirname(root)) == snap)
        check("5 remove_own_skill 也拒絕（根目錄、沒標記的同名）",
              not S.remove_own_skill(root, "skills") and not S.remove_own_skill(own_nomark, "errand-audit") and tree(os.path.dirname(root)) == snap)

        dst = os.path.join(root, "no-errands")
        S.install_skill(dst)
        mine = lambda: sorted(f for f in os.listdir(root) if f.startswith("."))
        check("5 正確目的地：裝好、有標記、沒留暫存", os.path.isfile(os.path.join(dst, "SKILL.md"))
              and os.path.isfile(os.path.join(dst, S.OWN_MARK)) and mine() == [], mine())
        open(os.path.join(dst, "stale.txt"), "w").write("舊版才有")
        S.install_skill(dst)
        check("5 換版：新版就位、舊版內容拿掉、沒留暫存", not os.path.exists(os.path.join(dst, "stale.txt"))
              and S.skill_tree_hash(dst) == S.skill_tree_hash(os.path.join(REPO, "skills", "no-errands")) and mine() == [], mine())

        open(os.path.join(dst, "stale.txt"), "w").write("舊版才有")
        before = tree(dst)
        real_replace = os.replace
        calls = []
        def flaky(src, d2):   # 第二次搬移（新版就位）失敗
            calls.append(d2)
            if len(calls) == 2:
                raise OSError("模擬：新版就位失敗")
            return real_replace(src, d2)
        S.os.replace = flaky
        try:
            try:
                S.install_skill(dst); raised = False
            except OSError:
                raised = True
        finally:
            S.os.replace = real_replace
        check("5 新版就位失敗 → 回報錯誤、舊版原封不動搬回、沒留暫存", raised and tree(dst) == before and mine() == [], (raised, mine()))

        # 改名一直被拒（重試到最後都失敗）：舊版原封不動、明確報錯、暫存清乾淨，不停在新舊都不在正式位置的中間狀態
        real_sleep = S.time.sleep
        S.time.sleep = lambda _s: None
        for which in ("old", "new"):
            def always_denied(src, d2, which=which):
                if (which == "old" and os.path.abspath(src) == os.path.abspath(dst)) or                         (which == "new" and os.path.abspath(d2) == os.path.abspath(dst) and ".new-" in src):
                    raise PermissionError("模擬：一直被拒")
                return real_replace(src, d2)
            S.os.replace = always_denied
            try:
                try:
                    S.install_skill(dst); msg = ""
                except OSError as e:
                    msg = str(e)
            finally:
                S.os.replace = real_replace
            check("5 %s一直被拒 → 明確報錯、舊版原封不動、沒留暫存" % ("舊版移開" if which == "old" else "新版就位"),
                  "原封不動" in msg and tree(dst) == before and mine() == [], (msg, mine()))
        S.time.sleep = real_sleep

        real_copy = shutil.copytree
        def boom(*a, **k):
            raise OSError("模擬：複製失敗")
        S.shutil.copytree = boom
        try:
            try:
                S.install_skill(dst); raised = False
            except OSError:
                raised = True
        finally:
            S.shutil.copytree = real_copy
        check("5 新版複製失敗 → 舊版沒被碰", raised and tree(dst) == before and mine() == [], (raised, mine()))

        # 上次換版當掉：正式位置不見、旁邊有帶標記的舊版 → 先搬回再換；別人的點開頭資料夾不碰
        real_replace(dst, os.path.join(root, ".no-errands.old-1-1"))
        os.makedirs(os.path.join(root, ".no-errands.old-9-9")); open(os.path.join(root, ".no-errands.old-9-9", "x"), "w").write("不是我們的")
        S.install_skill(dst)
        check("5 當掉後的善後：正式位置回來、我們的殘留清掉、別人的不碰",
              os.path.isfile(os.path.join(dst, "SKILL.md")) and mine() == [".no-errands.old-9-9"], mine())
        shutil.rmtree(os.path.join(root, ".no-errands.old-9-9"))

        # --sync 走同一套：使用者其他 skill、沒標記的同名 errand-audit 都不動
        h5 = seeded_home(os.path.join(td, "s5")) if os.makedirs(os.path.join(td, "s5")) is None else None
        cd = os.path.join(td, "s5c", ".claude"); os.makedirs(os.path.join(cd, "skills", "keep-me"))
        open(os.path.join(cd, "skills", "keep-me", "SKILL.md"), "w", encoding="utf-8").write("使用者的 skill")
        cx = ["--claude-dir", cd, "--codex-home", os.path.join(td, "s5c", ".codex"), "--home", h5]
        o, rc = setup("--user", *cx)
        check("5 --user 安裝到臨時家目錄", rc == 0 and o.get("ok"), o)
        snap_keep = tree(os.path.join(cd, "skills", "keep-me"))
        o, rc = setup("--user", "--sync", *cx)
        check("5 --sync 成功、兩個 skill 都是新版",
              rc == 0 and o.get("ok") and S.skill_tree_hash(os.path.join(cd, "skills", "no-errands")) == S.skill_tree_hash(os.path.join(REPO, "skills", "no-errands"))
              and os.path.isfile(os.path.join(cd, "skills", "errand-audit", S.OWN_MARK)), o)
        check("5 --sync 後使用者的 skill 原封不動、沒留暫存",
              tree(os.path.join(cd, "skills", "keep-me")) == snap_keep
              and sorted(os.listdir(os.path.join(cd, "skills"))) == ["errand-audit", "keep-me", "no-errands"], os.listdir(os.path.join(cd, "skills")))
        os.remove(os.path.join(cd, "skills", "errand-audit", S.OWN_MARK))
        snap_ea = tree(os.path.join(cd, "skills", "errand-audit"))
        o, rc = setup("--user", "--sync", *cx)
        check("5 --sync 遇到沒標記的同名 skill → 回報拒絕、不動它", rc != 0 and not o.get("ok") and o.get("refused")
              and tree(os.path.join(cd, "skills", "errand-audit")) == snap_ea, o)
    except Exception:   # 中途例外也要留下 FAIL 與原因（CI 只看 stdout，例外印在 stderr 會看不到）
        import traceback
        check("沒有未預期的例外", False, " | ".join(traceback.format_exc().strip().splitlines()[-4:]))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
