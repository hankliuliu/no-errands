#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""no-errands setup — 純標準函式庫。下載並核對工具、安裝 skill 與 CLAUDE.md 受管區塊。

用法:
  python setup.py --project <dir> [--home <dir>]     # 只在該專案生效（A/B 與試用）
  python setup.py --user [--home <dir>] [--claude-dir <dir>]
  python setup.py --uninstall --project <dir> [--home <dir>]

輸出：單一 JSON 物件（stdout）。失敗的步驟附 recovery 建議。
"""
import argparse, hashlib, io, json, os, platform, shutil, stat, sys, tarfile, tempfile, time, urllib.request, zipfile

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "skills", "no-errands", "scripts"))
import nowin  # noqa: E402,F401  Windows：子程序不開主控台視窗
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

def neb_cmd(home):
    return ned_cmd(home).replace("ned.py", "neb.py")


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

OWN_MARK = ".installed-by-no-errands"


def skill_is_ours(skill_dst, home):
    """同名 skill 目錄是不是我們裝的：有標記檔，或記在我們的 config.json 安裝清單裡。"""
    if not os.path.isdir(skill_dst):
        return True   # 不存在 → 可以裝
    if os.path.exists(os.path.join(skill_dst, OWN_MARK)):
        return True
    try:
        cfg = json.load(open(os.path.join(home, "config.json"), encoding="utf-8"))
        return any(os.path.abspath(i.get("skill", "")) == os.path.abspath(skill_dst) for i in cfg.get("installs", []))
    except Exception:
        return False


def install_skill(skill_dst, name="no-errands"):
    shutil.rmtree(skill_dst, ignore_errors=True)
    shutil.copytree(os.path.join(REPO, "skills", name), skill_dst)
    open(os.path.join(skill_dst, OWN_MARK), "w", encoding="utf-8").write("installed by no-errands setup.py" + chr(10))


def skill_tree_hash(d):
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(d)):
        for fn in sorted(files):
            if fn == OWN_MARK:   # 安裝標記不算 skill 內容
                continue
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
        c = checks.get(key)
        if c and c.get("ok"):
            return line_ok
        if key in _said:          # 同一項只說一次：第二行起整行省略
            return None
        _said.add(key)
        if c is None:             # doctor 沒跑這項（例如 --skip-desktop）≠ 失敗
            return "- ⚠ %s 未驗證（doctor 跳過；要用前先跑 `doctor.py`）" % key
        return "- ❌ %s 不可用：%s（修好後重跑 doctor）" % (key, c.get("error") or "doctor 未通過")
    _said = set()
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
        # 所有網頁一律經 neb（一般網站原樣通過、每次多約 0.1 秒）：不讓模型判斷哪個網站敏感，中途跳轉到敏感網域也有人看
        status("browser", "- 看網頁（一輪完成）：`%s batch \"open <url>\" \"screenshot <絕對路徑.png>\" \"close\"`" % neb_cmd(home)),
        status("browser", "- 操作網頁：`%s open <url>`→`... snapshot -i -c`→`... click @ref` / `... fill @ref 文字`→`... get text @ref`→`... close`"
               % neb_cmd(home)),
        status("profile", "- 登入過的站免再登入：上述指令加 `--profile \"%s\"`" % prof),
        status("desktop", "- 看桌面視窗：`%s <App.exe> screenshot <絕對路徑.png>`" % ned_cmd(home)),
        status("desktop", "- 操作桌面：`%s <App.exe> snapshot -i --compact`→`... click '@ref'` / `... set-value '@ref' 文字`→`... get '@ref' --property value`（ned 會鎖定 app 範圍）" % ned_cmd(home)),
        "- 政策：%s（`%s show`；只在使用者明確要求時才改）" % (pol_mod.summary(pol), pol_cmd),
        "- **網頁一律經 neb、桌面一律經 ned，不直接呼叫 agent-browser／agent-desktop。** 通訊/郵件/金融類 app 與網站："
        "未授權的會被拒絕，要碰先在對話中取得使用者指名同意；授權了也只讀（不輸入、不執行腳本、不點送出類按鈕）。",
        "- **你不能自己送出任何訊息**：要傳給別人的內容寫進一個文字檔，起草到核准發件匣："
        "`%s \"%s\" draft --service <fake|mattermost> --to <對象> --body-file <檔> --why \"<來由>\"`，"
        "回傳有 id 且 `... status <id>` 為 pending 才可說草稿已在發件匣；把網址告訴使用者，最多 `... wait <id>` 一次（上限 120 秒），沒結果就**結束回合**，不要自己寫迴圈查詢。發件匣頁面是你的禁區，不要開。"
        % (py_bin, os.path.join(home, "bin", "outbox.py")),
        "- 已授權的桌面聊天 app 走守門：`%s \"%s\" <app> locate|open|read|export|close --chat <名稱>`（輸入框/送出鈕永遠擋下）。"
        % (py_bin, os.path.join(home, "bin", "chat_guard.py")),
        ("- 觀看模式：使用者想看你操作——瀏覽器加 `--headed`，桌面操作可見（此時搶焦點是預期的）。" if watch else
         "- 使用者可能同時在用電腦：**不要搶焦點、不要動滑鼠鍵盤**（上述指令預設都不會）。"),
        "- 能力明細：`%s`；playbook：`%s`（動手前先查、走通後回寫）" % (os.path.join(home, "capabilities.md"), playbook),
        "只有「準備請使用者動手」或被「登入牆 / GUI-only / 安裝精靈」擋住時，才讀 skill `no-errands`。",
        MARK_E,
    ]
    return "\n".join(l for l in lines if l is not None)

GUARD_TAG = "no-errands"


def rule_path(path):
    """Claude Code 權限規則的絕對路徑寫法：//<路徑>。Windows 要寫成 //c/Users/...（小寫磁碟代號、沒有冒號）——
    //C:/Users/... 實測比對不到（2026-11-21，巢狀 claude -p 驗證）。"""
    ap_ = os.path.abspath(path).replace("\\", "/")
    if len(ap_) > 1 and ap_[1] == ":":
        ap_ = ap_[0].lower() + ap_[2:]
    return "//" + ap_.lstrip("/")


GUARD_GROUPS = ("direct-tools", "token", "consent")


def token_hook_cmd(py_bin, home):
    """hook 指令一律「找不到就放行」：PreToolUse 的結束代碼 2 = 擋下，而 python 開不到腳本檔就是以 2 結束——
    ~/.no-errands 被刪掉或搬走時，這台機器上所有 session 的所有工具呼叫都會被擋、agent 自己也修不了。
    所以先用 python -c 檢查檔案在不在，不在就以 0 結束；python 本身不在時是殼層的 127／9009，不是 2，也不擋。
    路徑用正斜線（bash 的雙引號會吃掉成對的反斜線；Windows 的 Python 認得正斜線）。"""
    p = os.path.join(home, "bin", "token_guard.py").replace("\\", "/")
    code = "import os,runpy;p=r'%s';os.path.isfile(p) and runpy.run_path(p,run_name='__main__')" % p.replace("'", "")
    return '%s -c "%s"' % (py_bin, code)


TOKEN_HOOK_MATCHER = "Read|Bash|PowerShell|Grep|Glob|Edit|Write|NotebookEdit"


def guard_rules(home, groups=GUARD_GROUPS):
    """建議的 Claude Code 設定，分三組可分開開關（預設三組都開）：
      direct-tools：deny 直接呼叫 agent-browser／agent-desktop（網頁一律經 neb、桌面一律經 ned）
      token       ：PreToolUse hook——預設不准讀 Mattermost token 檔，政策檔 token_read_allow 裡的專案資料夾放行
                    （deny 規則無法被專案層級蓋掉，做不到白名單，所以用 hook）
      consent     ：ask——policy.py 會改變同意範圍的子指令"""
    py_bin = ned_cmd(home).rsplit(" ", 1)[0]
    deny, ask, hooks = [], [], []
    if "token" in groups:
        hooks.append({"matcher": TOKEN_HOOK_MATCHER, "hooks": [{"type": "command", "command": token_hook_cmd(py_bin, home), "timeout": 10}]})
    # 守門從「建議」變成「只有這條路」：不准直接呼叫兩個執行檔（ned／neb 用子行程呼叫，不受權限規則影響）。
    # 2026-11-22 巢狀 claude -p（haiku）實測：萬用字元規則擋得住裸名稱、.exe、引號 Windows 路徑、正斜線與 /c/ 路徑、
    # 先 cd 再相對路徑、bash -c 包裝、變數存整段路徑、PowerShell 呼叫運算子與先 Set-Location；neb 照常放行。
    # 擋不住：把名稱拆開組（${A}-${B}.exe）、python 子行程拼字串、glob（agent-b*.exe）。只提到名稱的指令也會被擋（例如 grep）。
    for tool in ("agent-browser", "agent-desktop") if "direct-tools" in groups else ():
        deny.append("Bash(*%s*)" % tool)
        deny.append("PowerShell(*%s*)" % tool)
    # 「要詢問」只留給會改變同意範圍的指令（授權 app、改設定、白名單、網域）。
    # 不對 chat_guard 加詢問（2026-11-22 使用者：「白名單的聊天室都可以匯出」）：授權與白名單已由 chat_guard 自己把關，
    # 再多一層詢問只會讓無人在場的同步卡住。
    for sub in POLICY_ASK if "consent" in groups else ():
        ask.append("Bash(%s \"%s\" %s:*)" % (py_bin, os.path.join(home, "bin", "policy.py"), sub))
    return {"deny": deny, "ask": ask, "hooks": {"PreToolUse": hooks}}


POLICY_ASK = ("grant", "set", "whitelist", "domain", "token-allow")


def _ours(entry):
    return any("token_guard.py" in (h.get("command") or "") for h in entry.get("hooks") or [])


def apply_rules(path, rules, remove=False):
    """只增刪自己這幾筆；其餘內容原樣保留。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    perms = cfg.setdefault("permissions", {})
    changed = 0
    for kind in ("deny", "ask"):
        cur = perms.setdefault(kind, [])
        for r in rules.get(kind, []):
            if remove and r in cur:
                cur.remove(r); changed += 1
            elif not remove and r not in cur:
                cur.append(r); changed += 1
        if not cur:
            perms.pop(kind)
    if not perms:
        cfg.pop("permissions")
    # hooks：我們的那筆用指令裡的 token_guard.py 認；移除只拿掉自己的，新增前先換掉舊的（路徑變了也只留一筆）
    for ev, mine in (rules.get("hooks") or {}).items():
        hk = cfg.setdefault("hooks", {})
        cur = hk.setdefault(ev, [])
        old = [e for e in cur if _ours(e)]
        cur[:] = [e for e in cur if not _ours(e)] + ([] if remove else list(mine))
        if remove:
            changed += len(old)
        elif old != list(mine):
            changed += len(mine)
        if not cur:
            hk.pop(ev)
        if not hk:
            cfg.pop("hooks")
    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return {"ok": True, "changed": changed, "removed" if remove else "added": changed}


def _read_raw(path):
    with open(path, encoding="utf-8", newline="") as f:   # 不轉換換行：逐位元組保留使用者的檔案
        return f.read()


def _write_raw(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


CRLF, LF = chr(13) + chr(10), chr(10)


def codex_targets(a):
    """Codex 的安裝位置（官方文件兩種說法都裝）：skill 在 <CODEX_HOME>/skills/ 與 ~/.agents/skills/；
    全域指示在 <CODEX_HOME>/AGENTS.md 的受管區塊。"""
    agents_home = os.path.join(os.path.dirname(os.path.abspath(a.codex_home)), ".agents")         if os.path.basename(os.path.abspath(a.codex_home)) == ".codex" else os.path.join(os.path.expanduser("~"), ".agents")
    return ([os.path.join(a.codex_home, "skills", "no-errands"), os.path.join(agents_home, "skills", "no-errands")],
            os.path.join(a.codex_home, "AGENTS.md"))


def codex_install(a, home, paths):
    skills, agents_md = codex_targets(a)
    out = []
    for sk in skills:
        if skill_is_ours(sk, home):
            install_skill(sk)
            out.append({"step": "install:codex-skill", "ok": True, "path": sk})
        else:
            out.append({"step": "install:codex-skill", "ok": True, "skipped": "已有別人的同名 skill，保留不動", "path": sk})
    os.makedirs(a.codex_home, exist_ok=True)
    upsert_block(agents_md, managed_block(home, paths, checks=None, repo=REPO))
    out.append({"step": "install:codex-agents-md-block", "ok": True, "path": agents_md,
                "note": "安裝位置依 Codex 官方文件；未以實際 Codex 對話驗證"})
    return out


def codex_uninstall(a, home):
    skills, agents_md = codex_targets(a)
    out = []
    for sk in skills:
        if os.path.isdir(sk) and os.path.exists(os.path.join(sk, OWN_MARK)):
            shutil.rmtree(sk); out.append({"step": "uninstall:codex-skill", "ok": True, "path": sk})
    if remove_block(agents_md):
        out.append({"step": "uninstall:codex-agents-md-block", "ok": True, "path": agents_md})
    return out


def upsert_block(md_path, block):
    """只動受管區塊；區塊以外逐位元組保留（含行尾空白與 CRLF）。新加時在原文後面接「空行 + 區塊 + 換行」。"""
    text = _read_raw(md_path) if os.path.exists(md_path) else ""
    nl = CRLF if CRLF in text else LF
    block = block.replace(LF, nl)
    if MARK_S in text and MARK_E in text:
        pre = text.split(MARK_S)[0]
        post = text.split(MARK_E, 1)[1]
        text = pre + block + post
    elif text:
        if not text.endswith(LF):
            text += nl
        text = text + nl + block + nl
    else:
        text = block + nl
    _write_raw(md_path, text)


def remove_block(md_path):
    """移除受管區塊，以及新加時多接的那一個空行與結尾換行——原檔逐位元組還原。"""
    if not os.path.exists(md_path):
        return False
    text = _read_raw(md_path)
    if MARK_S not in text:
        return False
    nl = CRLF if CRLF in text else LF
    pre = text.split(MARK_S)[0]
    post = text.split(MARK_E, 1)[1] if MARK_E in text else ""
    if pre.endswith(nl + nl):
        pre = pre[: -len(nl)]
    if post.startswith(nl):
        post = post[len(nl):]
    out = pre + post
    if out.strip():
        _write_raw(md_path, out)
    else:
        os.remove(md_path)
    return True


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project")
    g.add_argument("--user", action="store_true")
    ap.add_argument("--home", default=os.environ.get("NO_ERRANDS_HOME") or os.path.join(os.path.expanduser("~"), ".no-errands"))
    ap.add_argument("--claude-dir", default=os.path.join(os.path.expanduser("~"), ".claude"),
                    help="--user 模式的 Claude 設定目錄（測試時務必覆寫到沙盒）")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--sync", action="store_true",
                    help="repo 規則變更後：重新同步所有已安裝目標的 skill 與受管區塊")
    ap.add_argument("--codex", action="store_true",
                    help="也裝給 Codex（預設：--user 且偵測到 Codex 主目錄時自動裝）")
    ap.add_argument("--codex-home", default=os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex"))
    ap.add_argument("--guard-rules", action="store_true",
                    help="把建議的 Claude Code 權限規則加進 settings.json（只增刪自己這幾筆；搭配 --uninstall 移除）")
    ap.add_argument("--guard-groups", default=",".join(GUARD_GROUPS),
                    help="要開哪幾組（逗號分隔）：direct-tools、token、consent；預設三組都開。--uninstall 一律移除三組")
    a = ap.parse_args()

    if a.guard_rules:
        home = os.path.abspath(a.home)
        target = (os.path.join(os.path.abspath(a.project), ".claude", "settings.json") if a.project
                  else os.path.join(a.claude_dir, "settings.json"))
        groups = tuple(g_.strip() for g_ in a.guard_groups.split(",") if g_.strip())
        bad = [g_ for g_ in groups if g_ not in GUARD_GROUPS]
        if bad:
            print(json.dumps({"ok": False, "error": "沒有這一組：%s（可選 %s）" % (bad, ", ".join(GUARD_GROUPS))}, ensure_ascii=False))
            sys.exit(2)
        if a.uninstall:
            res = apply_rules(target, guard_rules(home), remove=True); rules = guard_rules(home)
        else:
            # hook 指向 bin/token_guard.py：檔案不在時 Python 以代碼 2 結束，而 PreToolUse hook 的代碼 2 = 擋下
            # → 每個工具呼叫都會被擋。所以先把腳本放進 bin/、確認在了才寫設定。
            if "token" in groups:
                copy_scripts(home)
                if not os.path.exists(os.path.join(home, "bin", "token_guard.py")):
                    print(json.dumps({"ok": False, "error": "bin/token_guard.py 放不進去，沒有寫設定"}, ensure_ascii=False))
                    sys.exit(1)
            # 重新套用 = 先把三組都拿掉，再加這次選的（關掉某一組就真的關掉）
            apply_rules(target, guard_rules(home), remove=True)
            rules = guard_rules(home, groups)
            res = apply_rules(target, rules)
            res["groups"] = list(groups)
        print(json.dumps(dict(res, target=target, rules=rules,
                              limits="Bash 規則是前綴比對：擋得住工具直接讀 token 檔，擋不住刻意用別的 shell 指令讀；"
                                     "這組規則防的是過度熱心，不是安全邊界"), ensure_ascii=False, indent=1))
        sys.exit(0)

    if a.sync:
        home = os.path.abspath(a.home)
        cfg_p = os.path.join(home, "config.json")
        cfg = json.load(open(cfg_p, encoding="utf-8"))
        copy_scripts(home)
        load_policy_mod().ensure(home)
        synced = []
        paths = {t: os.path.join(home, "bin", exe_name(t)) for t in ("agent-desktop", "agent-browser")}
        for inst in cfg.get("installs", []):
            install_skill(inst["skill"])   # 只同步記在我們清單裡的目標
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
        sp = (os.path.join(os.path.abspath(a.project), ".claude", "settings.json") if a.project
              else os.path.join(a.claude_dir, "settings.json"))
        if os.path.exists(sp):   # 權限規則與 token hook 一定一起拿掉（沒加過就什麼都不動）
            steps.append(dict(apply_rules(sp, guard_rules(home), remove=True), step="uninstall:guard-rules", path=sp))
        if a.user:
            steps.extend(codex_uninstall(a, home))
        s2 = os.path.isdir(skill_dst) and skill_is_ours(skill_dst, home)
        if s2:
            shutil.rmtree(skill_dst)
        audit_dst = os.path.join(os.path.dirname(skill_dst), "errand-audit")
        if os.path.isdir(audit_dst) and os.path.exists(os.path.join(audit_dst, OWN_MARK)):
            shutil.rmtree(audit_dst)
        elif os.path.isdir(skill_dst):
            steps.append({"step": "uninstall:skill", "ok": True, "skipped": "同名 skill 目錄不是我們裝的，保留不動",
                          "path": skill_dst})
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
        if not skill_is_ours(skill_dst, home):
            steps.append({"step": "install:skill", "ok": False, "path": skill_dst,
                          "error": "已有同名 skill 目錄「no-errands」，而且不是本工具裝的：中止，不覆蓋",
                          "recovery": "確認那個目錄是什麼；要換成本工具的版本，先自己改名或移走它再重跑"})
            print(json.dumps({"ok": False, "home": home, "steps": steps}, ensure_ascii=False, indent=1))
            sys.exit(1)
        install_skill(skill_dst)
        steps.append({"step": "install:skill", "ok": True, "path": skill_dst,
                      "hash": skill_tree_hash(skill_dst)})
        audit_dst = os.path.join(os.path.dirname(skill_dst), "errand-audit")   # 跑腿稽核 skill（同樣不覆蓋別人的同名目錄）
        if skill_is_ours(audit_dst, home):
            install_skill(audit_dst, "errand-audit")
            steps.append({"step": "install:skill-errand-audit", "ok": True, "path": audit_dst})
        else:
            steps.append({"step": "install:skill-errand-audit", "ok": True, "skipped": "已有別人的同名 skill，保留不動",
                          "path": audit_dst})
        upsert_block(md_path, managed_block(home, paths, checks=None, repo=REPO))
        steps.append({"step": "install:claude-md-block", "ok": True, "path": md_path})
        if a.user and (a.codex or os.path.isdir(a.codex_home)):
            steps.extend(codex_install(a, home, paths))
        cfg_p = os.path.join(home, "config.json")
        cfg = json.load(open(cfg_p, encoding="utf-8")) if os.path.exists(cfg_p) else {"installs": []}
        entry = {"type": "project" if a.project else "user", "target": md_path, "skill": skill_dst}
        if entry not in cfg["installs"]:
            cfg["installs"].append(entry)
        cfg["home"] = home
        cfg["repo"] = REPO
        cfg["python"] = sys.executable
        cfg["skill_hash"] = skill_tree_hash(os.path.join(REPO, "skills", "no-errands"))
        cfg.setdefault("installed_at", time.strftime("%Y-%m-%d %H:%M"))   # 稽核用來切安裝前後；重裝不改
        json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        steps.append({"step": "config", "ok": True, "path": cfg_p,
                      "next": "跑 doctor.py --home %s 做各階 smoke test 並產出 capabilities.md" % home})
    print(json.dumps({"ok": ok and all(s.get("ok", True) for s in steps), "home": home, "steps": steps},
                     ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
