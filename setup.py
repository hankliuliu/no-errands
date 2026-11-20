#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""no-errands setup — 純標準函式庫。下載並核對工具、安裝 skill 與 CLAUDE.md 受管區塊。

用法:
  python setup.py --project <dir> [--home <dir>]     # 只在該專案生效（A/B 與試用）
  python setup.py --user [--home <dir>] [--claude-dir <dir>]
  python setup.py --uninstall --project <dir> [--home <dir>]

輸出：單一 JSON 物件（stdout）。失敗的步驟附 recovery 建議。
"""
import argparse, hashlib, io, json, os, platform, shutil, stat, sys, tarfile, tempfile, urllib.request, zipfile

REPO = os.path.dirname(os.path.abspath(__file__))
MARK_S, MARK_E = "<!-- no-errands:start -->", "<!-- no-errands:end -->"
UA = {"User-Agent": "no-errands-setup"}

def plat_key():
    sysname = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    mach = platform.machine().lower()
    arch = "arm64" if mach in ("arm64", "aarch64") else "x86_64"
    return "%s-%s" % (sysname, arch) if sysname else None

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def download(url, dest):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)

def exe_name(base):
    return base + (".exe" if os.name == "nt" else "")

def extract_tool(archive, kind, tool, dest_dir):
    """回傳解出的執行檔完整路徑。"""
    target = os.path.join(dest_dir, exe_name(tool))
    if kind == "raw":
        shutil.copyfile(archive, target)
    elif kind in ("tar.gz", "archive") and archive.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as t:
            for m in t.getmembers():
                base = os.path.basename(m.name)
                if base in (tool, tool + ".exe"):
                    src = t.extractfile(m)
                    with open(os.path.join(dest_dir, base), "wb") as f:
                        shutil.copyfileobj(src, f)
                    target = os.path.join(dest_dir, base)
    elif archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                base = os.path.basename(n)
                if base in (tool, tool + ".exe"):
                    with z.open(n) as src, open(os.path.join(dest_dir, base), "wb") as f:
                        shutil.copyfileobj(src, f)
                    target = os.path.join(dest_dir, base)
    if os.name != "nt":
        os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR)
    return target

def install_binaries(home, steps):
    key = plat_key()
    versions = json.load(open(os.path.join(REPO, "versions.json"), encoding="utf-8"))
    bin_dir = os.path.join(home, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    state_p = os.path.join(bin_dir, ".installed.json")
    state = json.load(open(state_p, encoding="utf-8")) if os.path.exists(state_p) else {}
    paths = {}
    for tool in ("agent-desktop", "agent-browser"):
        spec = versions[tool]
        tgt = spec["targets"].get(key)
        step = {"step": "install:" + tool, "version": spec["version"]}
        if not tgt:
            step.update(ok=False, error="unsupported platform %s" % key,
                        recovery="手動從 GitHub Releases 下載對應平台檔案放到 %s" % bin_dir)
            steps.append(step); continue
        exe = os.path.join(bin_dir, exe_name(tool))
        if state.get(tool) == spec["version"] and os.path.exists(exe):
            step.update(ok=True, skipped="already installed", path=exe)
            steps.append(step); paths[tool] = exe; continue
        try:
            with tempfile.TemporaryDirectory() as td:
                ar = os.path.join(td, os.path.basename(tgt["url"]))
                download(tgt["url"], ar)
                got = sha256_file(ar)
                if got != tgt["sha256"]:
                    step.update(ok=False, error="sha256 mismatch: got %s" % got,
                                recovery="versions.json 的雜湊或上游檔案有變；確認官方 checksums 後更新 versions.json，不要跳過核對")
                    steps.append(step); continue
                exe = extract_tool(ar, spec["kind"], tool, bin_dir)
            state[tool] = spec["version"]
            step.update(ok=True, path=exe)
            paths[tool] = exe
        except Exception as e:
            step.update(ok=False, error=str(e),
                        recovery="檢查網路；或手動下載 %s 核對 sha256 後放到 %s" % (tgt["url"], bin_dir))
        steps.append(step)
    json.dump(state, open(state_p, "w", encoding="utf-8"))
    return paths

def managed_block(home, paths):
    ad = paths.get("agent-desktop", os.path.join(home, "bin", exe_name("agent-desktop")))
    ab = paths.get("agent-browser", os.path.join(home, "bin", exe_name("agent-browser")))
    lines = [
        MARK_S,
        "## no-errands：人負責決定，agent 負責動手",
        "準備請使用者動手／截圖／貼上之前，先讀 skill `no-errands` 並走完能力階梯。",
        "- 桌面 a11y／視窗截圖（PNG）：`%s`（首次先跑 `skills get desktop`）" % ad,
        "- 瀏覽器自動化（系統 Chrome/Edge，免安裝）：`%s`（首次先跑 `skills get core`）" % ab,
        "- agent 專用已登入 profile：`--profile %s`（缺登入＝請人登入一次，之後不再問）" % os.path.join(home, "browser-profile"),
        "- 本機能力實測結果：`%s`（doctor 產出）" % os.path.join(home, "capabilities.md"),
        "- 走通的路寫進 playbook：repo `playbook/`；私人 `%s`" % os.path.join(home, "playbook-local"),
        "- 驗收規則：有視覺成果就自己看過（截圖/渲染/讀回），語意層 ok ≠ 任務正確。",
        MARK_E,
    ]
    return "\n".join(lines)

def upsert_block(md_path, block):
    text = open(md_path, encoding="utf-8").read() if os.path.exists(md_path) else ""
    if MARK_S in text and MARK_E in text:
        pre = text.split(MARK_S)[0]
        post = text.split(MARK_E, 1)[1]
        text = pre + block + post
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"
    open(md_path, "w", encoding="utf-8", newline="\n").write(text)

def remove_block(md_path):
    if not os.path.exists(md_path):
        return False
    text = open(md_path, encoding="utf-8").read()
    if MARK_S not in text:
        return False
    pre = text.split(MARK_S)[0]
    post = text.split(MARK_E, 1)[1] if MARK_E in text else ""
    out = (pre.rstrip() + "\n" + post.lstrip()).strip()
    if out:
        open(md_path, "w", encoding="utf-8", newline="\n").write(out + "\n")
    else:
        os.remove(md_path)
    return True

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project")
    g.add_argument("--user", action="store_true")
    ap.add_argument("--home", default=os.path.join(os.path.expanduser("~"), ".no-errands"))
    ap.add_argument("--claude-dir", default=os.path.join(os.path.expanduser("~"), ".claude"),
                    help="--user 模式的 Claude 設定目錄（測試時務必覆寫到沙盒）")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()

    home = os.path.abspath(a.home)
    steps, ok = [], True
    if a.project:
        proj = os.path.abspath(a.project)
        skill_dst = os.path.join(proj, ".claude", "skills", "no-errands")
        md_path = os.path.join(proj, "CLAUDE.md")
    else:
        skill_dst = os.path.join(a.claude_dir, "skills", "no-errands")
        md_path = os.path.join(a.claude_dir, "CLAUDE.md")

    if a.uninstall:
        s1 = remove_block(md_path)
        s2 = os.path.isdir(skill_dst)
        if s2:
            shutil.rmtree(skill_dst)
        steps.append({"step": "uninstall", "ok": True, "removed_block": s1, "removed_skill": s2,
                      "note": "%s 保留（bin/、browser-profile/、playbook-local/ 屬使用者資產），要全清就整個刪掉它" % home})
    else:
        for d in ("bin", "browser-profile", "playbook-local"):
            os.makedirs(os.path.join(home, d), exist_ok=True)
        paths = install_binaries(home, steps)
        ok = all(s.get("ok") for s in steps)
        shutil.rmtree(skill_dst, ignore_errors=True)
        shutil.copytree(os.path.join(REPO, "skills", "no-errands"), skill_dst)
        steps.append({"step": "install:skill", "ok": True, "path": skill_dst})
        upsert_block(md_path, managed_block(home, paths))
        steps.append({"step": "install:claude-md-block", "ok": True, "path": md_path})
        cfg_p = os.path.join(home, "config.json")
        cfg = json.load(open(cfg_p, encoding="utf-8")) if os.path.exists(cfg_p) else {"installs": []}
        entry = {"type": "project" if a.project else "user", "target": md_path, "skill": skill_dst}
        if entry not in cfg["installs"]:
            cfg["installs"].append(entry)
        cfg["home"] = home
        json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        steps.append({"step": "config", "ok": True, "path": cfg_p,
                      "next": "跑 doctor.py --home %s 做各階 smoke test 並產出 capabilities.md" % home})
    print(json.dumps({"ok": ok and all(s.get("ok", True) for s in steps), "home": home, "steps": steps},
                     ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
