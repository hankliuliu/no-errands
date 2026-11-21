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
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:
        # macOS 的 python.org 版常缺根憑證（ssl.SSLCertVerificationError）→ fallback 到系統 curl
        import subprocess
        rc = subprocess.run(["curl", "-fsSL", "-o", dest, url], timeout=300).returncode
        if rc != 0:
            raise RuntimeError("urllib failed (%s) and curl fallback rc=%d" % (e, rc))

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

def ned_cmd(home):
    py = sys.executable
    cfg_p = os.path.join(home, "config.json")
    if os.path.exists(cfg_p):
        try:
            py = json.load(open(cfg_p, encoding="utf-8")).get("python") or py
        except Exception:
            pass
    return '"%s" "%s"' % (py, os.path.join(home, "bin", "ned.py"))

SCRIPTS = os.path.join(REPO, "skills", "no-errands", "scripts")

def copy_scripts(home):
    """scripts/*.py（ned、policy、chat_guard、outbox…）一起放進 bin/，彼此可 import。"""
    bin_dir = os.path.join(home, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    out = []
    for fn in sorted(os.listdir(SCRIPTS)):
        if fn.endswith(".py"):
            shutil.copyfile(os.path.join(SCRIPTS, fn), os.path.join(bin_dir, fn))
            out.append(fn)
    return out

def load_policy_mod():
    sys.path.insert(0, SCRIPTS)
    import policy
    return policy

def skill_tree_hash(d):
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(d)):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            h.update(fn.encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()[:16]

def managed_block(home, paths, checks=None, repo=None):
    """checks=None（setup 剛裝完、尚未實測）→ 標「未驗證」；有 checks → 只對通過的說可用。"""
    ad = paths.get("agent-desktop", os.path.join(home, "bin", exe_name("agent-desktop")))
    ab = paths.get("agent-browser", os.path.join(home, "bin", exe_name("agent-browser")))
    def status(key, line_ok):
        if checks is None:
            return line_ok + "（⚠ 尚未驗證：先跑 doctor.py）"
        c = checks.get(key, {})
        if c.get("ok"):
            return line_ok
        return "- ❌ %s 不可用：%s（修好後重跑 doctor）" % (key, c.get("error", "doctor 未通過"))
    playbook = os.path.join(repo, "playbook") if repo else "（repo 的 playbook/，路徑見 config.json）"
    prof = os.path.join(home, "browser-profile")
    pol_mod = load_policy_mod()
    pol = pol_mod.load(home)
    py_bin = ned_cmd(home).rsplit(" ", 1)[0]
    pol_cmd = '%s "%s"' % (py_bin, os.path.join(home, "bin", "policy.py"))
    watch = pol.get("visibility") == "watch"
    # v0.3：區塊自給自足（可照抄的一行指令），輕手段優先，skill 只在交人/被擋時讀。
    lines = [
        MARK_S,
        "## no-errands：人負責決定，agent 負責動手",
        "1. 先用最輕的手段：檔案 / CLI / curl 能解就不開瀏覽器、不開桌面工具。",
        "2. 回報完成前自己驗收一次（截圖或讀回）；單步回 ok ≠ 任務正確。",
        "以下工具本機已實測可用，直接照抄（不必先讀任何說明）：",
        status("browser", "- 看網頁（一輪完成）：`\"%s\" batch \"open <url>\" \"screenshot <絕對路徑.png>\" \"close\"`" % ab),
        status("browser", "- 操作網頁：`\"%s\" open <url>`→`snapshot -i -c`→`click '#sel'` / `fill '#sel' 文字`→`get text '#sel'`→`close`" % ab),
        status("profile", "- 登入過的站免再登入：上述指令加 `--profile \"%s\"`" % prof),
        status("desktop", "- 看桌面視窗：`%s <App.exe> screenshot <絕對路徑.png>`" % ned_cmd(home)),
        status("desktop", "- 操作桌面：`%s <App.exe> snapshot -i --compact`→`... click '@ref'` / `... set-value '@ref' 文字`→`... get '@ref' --property value`（ned 會鎖定 app 範圍）" % ned_cmd(home)),
        "- 政策：%s（`%s show`；只在使用者明確要求時才改）" % (pol_mod.summary(pol), pol_cmd),
        "- 通訊/郵件/金融類 app 與網站：未授權就不讀不碰（ned 會拒絕）；要碰先在對話中取得使用者指名同意。",
        "- **你不能自己送出任何訊息**：要傳給別人的內容寫進一個文字檔，起草到核准發件匣："
        "`%s \"%s\" draft --service <fake|mattermost> --to <對象> --body-file <檔> --why \"<來由>\"`，"
        "回傳有 id 且 `... status <id>` 為 pending 才可說草稿已在發件匣；把網址告訴使用者後**就結束回合**（不要在前景等核准，最多輪詢約 60 秒；能掛背景任務才監看）。發件匣頁面是你的禁區，不要開。"
        % (py_bin, os.path.join(home, "bin", "outbox.py")),
        "- 已授權的桌面聊天 app 走守門：`%s \"%s\" <app> locate|open|read|export|close --chat <名稱>`（輸入框/送出鈕永遠擋下）。"
        % (py_bin, os.path.join(home, "bin", "chat_guard.py")),
        ("- 觀看模式：使用者想看你操作——瀏覽器加 `--headed`，桌面操作可見（此時搶焦點是預期的）。" if watch else
         "- 使用者可能同時在用電腦：**不要搶焦點、不要動滑鼠鍵盤**（上述指令預設都不會）。"),
        "- 能力明細：`%s`；playbook：`%s`（動手前先查、走通後回寫）" % (os.path.join(home, "capabilities.md"), playbook),
        "只有「準備請使用者動手」或被「登入牆 / GUI-only / 安裝精靈」擋住時，才讀 skill `no-errands`。",
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
    ap.add_argument("--sync", action="store_true",
                    help="repo 規則變更後：重新同步所有已安裝目標的 skill 與受管區塊")
    a = ap.parse_args()

    if a.sync:
        home = os.path.abspath(a.home)
        cfg_p = os.path.join(home, "config.json")
        cfg = json.load(open(cfg_p, encoding="utf-8"))
        copy_scripts(home)
        load_policy_mod().ensure(home)
        synced = []
        paths = {t: os.path.join(home, "bin", exe_name(t)) for t in ("agent-desktop", "agent-browser")}
        for inst in cfg.get("installs", []):
            shutil.rmtree(inst["skill"], ignore_errors=True)
            shutil.copytree(os.path.join(REPO, "skills", "no-errands"), inst["skill"])
            upsert_block(inst["target"], managed_block(home, paths, checks=None, repo=REPO))
            synced.append(inst["target"])
        cfg["skill_hash"] = skill_tree_hash(os.path.join(REPO, "skills", "no-errands"))
        json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps({"ok": True, "synced": synced,
                          "note": "受管區塊標回「尚未驗證」，跑 doctor 刷新"}, ensure_ascii=False, indent=1))
        sys.exit(0)

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
        scripts = copy_scripts(home)
        pol, created = load_policy_mod().ensure(home)
        steps.append({"step": "install:scripts+policy", "ok": True, "scripts": scripts,
                      "policy": os.path.join(home, "policy.json"),
                      "policy_created": created, "note": "既有 policy.json 不覆寫"})
        shutil.rmtree(skill_dst, ignore_errors=True)
        shutil.copytree(os.path.join(REPO, "skills", "no-errands"), skill_dst)
        steps.append({"step": "install:skill", "ok": True, "path": skill_dst,
                      "hash": skill_tree_hash(skill_dst)})
        upsert_block(md_path, managed_block(home, paths, checks=None, repo=REPO))
        steps.append({"step": "install:claude-md-block", "ok": True, "path": md_path})
        cfg_p = os.path.join(home, "config.json")
        cfg = json.load(open(cfg_p, encoding="utf-8")) if os.path.exists(cfg_p) else {"installs": []}
        entry = {"type": "project" if a.project else "user", "target": md_path, "skill": skill_dst}
        if entry not in cfg["installs"]:
            cfg["installs"].append(entry)
        cfg["home"] = home
        cfg["repo"] = REPO
        cfg["python"] = sys.executable
        cfg["skill_hash"] = skill_tree_hash(os.path.join(REPO, "skills", "no-errands"))
        json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        steps.append({"step": "config", "ok": True, "path": cfg_p,
                      "next": "跑 doctor.py --home %s 做各階 smoke test 並產出 capabilities.md" % home})
    print(json.dumps({"ok": ok and all(s.get("ok", True) for s in steps), "home": home, "steps": steps},
                     ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
