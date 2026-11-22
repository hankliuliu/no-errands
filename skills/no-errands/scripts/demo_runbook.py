#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範劇本（demo runbook）引擎——讓 agent 照劇本操作網頁系統做 live demo，每次留下紀錄讓下次更穩。純 stdlib。

  demo_runbook.py validate  <runbook.json>
  demo_runbook.py sends     <runbook.json> [--out 檔]          # 產生「整份劇本一次核准」的送出清單
  demo_runbook.py request-approval <runbook.json>              # 把送出清單起草到發件匣（service=runbook）
  demo_runbook.py preflight <runbook.json> [--approval <id>]   # 服務、登入、模型、資料狀態、核准是否有效
  demo_runbook.py rehearse  <runbook.json> --approval <id> [--from <步驟id>] [--to <步驟id>] [--continue]
  demo_runbook.py live      <runbook.json> --approval <id> [--resume <run 資料夾>]
        live：看得到（--headed）、只走劇本；遇到 advance=cue 的步驟就停下、存檔並結束，講者示意後用 --resume 接著跑。
  demo_runbook.py learn     <runbook.json> [--runs N]          # 由最近 N 次紀錄提出劇本修改（差異清單）
        政策 demo.learn=propose（預設）只提出；auto 自動套用並記進劇本歷史（凍結的劇本除外）；off 不提出。
  demo_runbook.py apply     <runbook.json> <proposal.json> --reason "<使用者同意的原話>"
  demo_runbook.py freeze|unfreeze <runbook.json> --reason "..."
  demo_runbook.py login     <runbook.json> --account <帳號> --secret-cmd "<印出密碼的指令>"
        只限政策檔登記為 agent_login 的示範帳號（policy.py domain add … --agent-login …）；由 neb 的 login 規則填入，
        密碼走 stdin（不進命令列參數）、只在記憶體裡，不輸出、不寫檔。
  demo_runbook.py reset     <runbook.json>                     # 執行還原指令：政策檔要有使用者對「這組指令雜湊」的同意
  demo_runbook.py standby   <runbook.json> [--approval <id>]   # 上場前：把會失敗的事全部做完，引擎與播放器停在第一步前
        通道（劇本 tunnel）→ 檢查指令（失敗且有 fix 就修一次再查；資料不是 demo-base → 還原）→ 帳號登入 →
        核准與次數（長期核准下自動續）→ 備案檔 → 瀏覽器乾淨狀態 → 依當下螢幕排好視窗 → 停在「按下一步開始」。
        登入失效不會自動重新登入（要密碼）：只報出來，上場前請 agent 跑 login。
        任何一項不過就當場報出來、不啟動。劇本資料夾的「上場前準備.cmd」就是這個指令（不經 agent）。

劇本裡的 preflight／reset／tunnel 是會在本機執行的指令：validate 會全部列出；只跑自己寫的或看過的劇本。
這些指令照 POSIX 規則拆成「程式＋參數」直接執行、不經任何殼層（從 cmd、檔案總管、Git Bash 啟動結果都一樣）；
本機不能有管線或重導向——要用就放進遠端指令的引號裡（例：ssh host 'a | b'）。Windows 上 ssh／curl 固定用系統內建的。

長期核准（政策 domain set <主機> runbook_approval standing）：核准次數用完、到期或送出內容改了時，
preflight／rehearse／live 會自動起草同一份清單，由發件匣伺服器依長期核准決定（不是 agent 去按）；
伺服器不放行（例如使用者撤銷過）就照舊停在頁面等使用者。

所有對目標網站的操作都經過 neb；送出類動作帶 --runbook-approval/--step，neb 只放行與核准清單逐字相符的輸入。
政策 demo.persist=off → 一次性：紀錄寫在暫存資料夾、結束即刪，不更新 Plan B、不提出修改。
紀錄只含結構與數字（步驟、耗時、選取方式、斷言結果）；截圖留本機（可能含系統畫面內容，分享前自己看過）。
"""
import copy, hashlib, json, os, re, shlex, shutil, subprocess, sys, tempfile, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402
import nowin  # noqa: E402,F401  Windows：子程序不開主控台視窗（背景引擎與播放器用 nowin.popen_background）

HERE = os.path.dirname(os.path.abspath(__file__))
NEB = os.path.join(HERE, "neb.py")
OUTBOX = os.path.join(HERE, "outbox.py")
SEND_ACTIONS = ("fill", "press", "click", "check")
SNAP_RE = re.compile(r'^\s*-\s+(\w+)\s+"((?:[^"\\]|\\.)*)"(.*?)\bref=(e\d+)')
VAR_RE = re.compile(r"\{([A-Za-z_]\w*)\}")      # 變數名以字母開頭（不和正規表示式的 {8} 混淆）


# ---------------------------------------------------------------- 小工具

def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def arg(a, k, d=None):
    return a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else d


def now_s():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_rb(path):
    with open(path, encoding="utf-8") as f:
        rb = json.load(f)
    rb["_path"] = os.path.abspath(path)
    rb["_dir"] = os.path.dirname(rb["_path"])
    return rb


def save_rb(rb):
    data = {k: v for k, v in rb.items() if not k.startswith("_")}
    tmp = rb["_path"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    replace_retry(tmp, rb["_path"])


def rb_sha(rb):
    """劇本的「送出相關內容」雜湊：步驟的帳號與送出動作。等待預算、選取方式順序、講者台詞改了不影響核准。"""
    return hashlib.sha256(json.dumps(sends_of(rb), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def struct_sha(rb):
    """劇本結構（步驟 id 與每個動作的種類）：learn 只用結構相同的紀錄，否則動作索引會對錯位置。"""
    st = [(s["id"], [a.get("do") for a in s.get("actions") or []], [a.get("do") for a in s.get("fallback") or []])
          for s in rb.get("steps", [])]
    return hashlib.sha256(json.dumps(st, ensure_ascii=False).encode()).hexdigest()[:16]


def sends_sha(sends):
    return hashlib.sha256(json.dumps(sends, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def names_of(t):
    n = t.get("names") or t.get("name") or []
    return [n] if isinstance(n, str) else list(n)


def var_pattern(rb, text):
    """'/case show {case_id}' → (regex, 顯示用文字)；沒有變數回 (None, None)。
    每個變數是具名群組 v_<名>_<序>，neb 另外對群組值加硬上限。沒宣告 pattern 的變數 → ValueError（沒有預設值）。"""
    if not VAR_RE.search(text or ""):
        return None, None
    vars_ = rb.get("vars") or {}
    parts, shown, pos = [], [], 0
    for k, m in enumerate(VAR_RE.finditer(text)):
        parts.append(re.escape(text[pos:m.start()]))
        shown.append(text[pos:m.start()])
        v = vars_.get(m.group(1)) or {}
        if not v.get("pattern"):
            raise ValueError("變數 {%s} 沒有宣告 pattern" % m.group(1))
        parts.append("(?P<v_%s_%d>%s)" % (m.group(1), k, v["pattern"]))
        shown.append("«%s»" % m.group(1))
        pos = m.end()
    parts.append(re.escape(text[pos:]))
    shown.append(text[pos:])
    return "".join(parts), "".join(shown)


def path_of(rb, url):
    import urllib.parse
    p = urllib.parse.urlparse(rb["target"]["base_url"].rstrip("/") + url).path
    return p.rstrip("/") or "/"


# ---------------------------------------------------------------- 送出清單（核准用）

def sends_of(rb):
    """每個含送出類動作的步驟 → 一筆：帳號、位置、種類、欄位與確切文字（或變動規則）、按鍵、點擊名稱。
    名稱清單排序後才進雜湊：選取方式的順序調整不需要重新核准，新增名稱才需要。"""
    res = []
    cur = {}                                  # 每個帳號目前所在的網址路徑（跨步驟延續；每次 open 更新）
    home = path_of(rb, rb["target"].get("home_path", "/"))
    for st in rb.get("steps", []):
        inputs, keys, clicks = [], [], []
        start = cur.get(st["account"], home)
        for seq in (st.get("actions") or [], st.get("fallback") or []):
            at = start                        # 退路從步驟開始時的路徑算起
            for act in seq:
                d = act.get("do")
                if d == "open":
                    at = path_of(rb, act["url"])
                elif d == "fill":
                    pat, shown = var_pattern(rb, act["text"])
                    ent = {"at": at, "fields": sorted(names_of(act["target"]))}
                    if pat:
                        vs = {n: rb["vars"][n]["pattern"] for n in VAR_RE.findall(act["text"])}
                        ent.update(pattern=pat, shown=shown, vars=vs)
                    else:
                        ent["text"] = act["text"]
                    if ent not in inputs:
                        inputs.append(ent)
                elif d == "press":
                    k = {"at": at, "key": act["key"]}
                    if k not in keys:
                        keys.append(k)
                elif d in ("click", "check"):
                    for n in names_of(act["target"]):
                        c = {"at": at, "name": n}
                        if c not in clicks:
                            clicks.append(c)
            if seq is st.get("actions"):
                cur[st["account"]] = at
        if inputs or keys or clicks:
            key = lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)
            res.append({"step": st["id"], "account": st["account"], "where": st.get("where", ""),
                        "kind": st.get("kind", "操作"), "inputs": inputs, "keys": sorted(keys, key=key),
                        "clicks": sorted(clicks, key=key), "vary": list(st.get("vary") or [])})
    return res


def sends_doc(rb):
    s = sends_of(rb)
    doc = {"kind": "no-errands-runbook-sends", "version": 1, "title": rb.get("title", ""),
           "target": rb["target"]["host"].lower(), "runbook_sha256": rb_sha(rb), "days": rb.get("approval_days", 7),
           "sends": s}
    if rb.get("suggested_runs") is not None:                # 卡片預選的可執行次數（使用者仍可改）
        doc["suggested_runs"] = rb["suggested_runs"]
        doc["suggested_runs_why"] = rb.get("suggested_runs_why", "")
    return doc


def stage_errors(rb):
    if not stage_on(rb):
        return []
    import stage_layout
    return stage_layout.caption_errors({st["id"]: [b.get("say", "") for b in beats_of(st)] for st in rb.get("steps", [])})


def validate(rb):
    errs = []
    for k in ("title", "target", "accounts", "steps"):
        if k not in rb:
            errs.append("缺 %s" % k)
    ids = set()
    for st in rb.get("steps", []):
        if st.get("id") in ids:
            errs.append("步驟 id 重複：%s" % st.get("id"))
        ids.add(st.get("id"))
        if st.get("account") not in (rb.get("accounts") or {}):
            errs.append("%s：帳號 %s 不在 accounts" % (st.get("id"), st.get("account")))
        if st.get("advance", "auto") not in ("auto", "cue"):
            errs.append("%s：advance 只能是 auto|cue" % st.get("id"))
        for act in (st.get("actions") or []) + (st.get("fallback") or []):
            if act.get("do") not in ("open", "fill", "press", "click", "check", "wait_text", "capture", "pause",
                                     "snapshot_ok", "expect_absent", "scroll", "mark"):
                errs.append("%s：未知動作 %s" % (st.get("id"), act.get("do")))
            for v in VAR_RE.findall(json.dumps({k: x for k, x in act.items() if k != "regex"}, ensure_ascii=False)):
                if not ((rb.get("vars") or {}).get(v) or {}).get("pattern"):
                    errs.append("%s：變數 {%s} 沒有在 vars 宣告 pattern" % (st.get("id"), v))
        if st.get("resume_at") is not None and not 0 <= int(st["resume_at"]) <= len(st.get("actions") or []):
            errs.append("%s：resume_at 超出範圍" % st.get("id"))
    errs += stage_errors(rb)
    return errs


def loose_waits(rb):
    """「等某段文字出現」的斷言，若頁面會累積歷史（聊天、通知），必須 new_only（配 mark）或帶本次才有的識別（變數）。
    例外：static=true（頁面固定內容），或範圍全是對話框（[role=dialog]）。回傳警告清單。"""
    warn = []
    for st in rb.get("steps", []):
        for phase in ("actions", "fallback"):
            for i, act in enumerate(st.get(phase) or []):
                if act.get("do") != "wait_text" or act.get("new_only") or act.get("static"):
                    continue
                raw = act.get("regex", act.get("text"))
                texts = [raw] if isinstance(raw, str) else raw
                if all(VAR_RE.search(t) for t in texts):
                    continue
                scopes = act.get("scopes") or [None]
                if all(s and "dialog" in s for s in scopes):
                    continue
                warn.append("%s.%s[%d]：等「%s」可能比對到以前留下的內容；加 new_only（前面放 mark）或帶本次識別，"
                            "頁面固定內容就標 static" % (st["id"], phase, i, texts[0][:20]))
    return warn


def local_cmd_errors(rb):
    errs = []
    for c in shell_commands(rb):
        try:
            cmd_argv(c["cmd"])
        except ValueError as e:
            errs.append("%s：%s" % (c["where"], e))
    return errs


def shell_commands(rb):
    """劇本裡會在本機執行的 shell 指令（preflight／reset）：validate 一律列出來給人看。"""
    return [{"where": "preflight", "name": c.get("name"), "cmd": c.get("cmd")} for c in rb.get("preflight") or []] + \
           [{"where": "reset", "cmd": c} for c in (rb.get("reset") or {}).get("commands") or []]


LOCAL_OP_CHARS = set("|&;<>()")


def cmd_argv(cmd):
    """劇本裡的本機指令 → argv。照 POSIX 規則拆（引號、跳脫與 bash 相同），不經任何殼層；
    本機的管線／重導向／串接一律拒絕（放進遠端指令的引號裡）。Windows 的 ssh／curl 固定用系統內建的。"""
    if isinstance(cmd, list):
        argv = [str(x) for x in cmd]
    else:
        lex = shlex.shlex(str(cmd), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        argv = list(lex)
        bad = [t for t in argv if t and set(t) <= LOCAL_OP_CHARS]
        if bad:
            raise ValueError("本機指令不能有管線、重導向或串接（%s）；放進遠端指令的引號裡：%s" % (" ".join(bad), cmd))
    if not argv:
        raise ValueError("空的指令")
    if os.name == "nt" and argv[0] in ("ssh", "curl"):
        sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        cand = os.path.join(sysdir, "OpenSSH", "ssh.exe") if argv[0] == "ssh" else os.path.join(sysdir, "curl.exe")
        if os.path.exists(cand):
            argv[0] = cand
    return argv


def run_cmd(cmd, timeout=60):
    """回 (rc, stdout, stderr)。"""
    try:
        argv = cmd_argv(cmd)
    except ValueError as e:
        return 2, "", str(e)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "找不到程式：%s" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "", "逾時（%s 秒）" % timeout


def in_git_tree(path):
    d = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            return None
        d = nd


def reset_sha(rb):
    return hashlib.sha256(json.dumps((rb.get("reset") or {}).get("commands") or [], ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 瀏覽器（一律經 neb）

class StopDemo(Exception):
    pass


def replace_retry(tmp, path, tries=40):
    """Windows：讀的一方（播放器每 0.25 秒、驗收驅動）剛好開著目標檔時 os.replace 會 PermissionError。
    總驗收實測：引擎「在等下一步」那一次寫入被這樣吞掉，播放器一直顯示舊狀態、沒人按下一步。→ 重試最多約 2 秒。"""
    for k in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(0.05)


def _atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    replace_retry(tmp, path)


def write_control(run_dir, **kw):
    """播放器與 CLI 共用：更新 control.json（state=playing|paused|stopped、advance=auto|step、next_seq 遞增＝下一步）。"""
    p = os.path.join(run_dir, "control.json")
    try:
        c = json.load(open(p, encoding="utf-8"))
    except Exception:
        c = {"state": "playing", "advance": "auto", "next_seq": 0}
    if kw.pop("next", False):
        c["next_seq"] = int(c.get("next_seq", 0)) + 1
        if c.get("state") == "paused":
            c["state"] = "playing"
    c.update(kw)
    c["ts"] = time.time()
    _atomic_json(p, c)
    return c


class Control:
    """引擎端的控制通道。安全點：每個動作之前、等待迴圈每一輪、每步開始前。
    暫停期間不呼叫 neb；等待預算與計時凍結（回傳暫停秒數讓呼叫端扣掉）。"""

    def __init__(self, run_dir, advance="auto"):
        self.dir = run_dir
        if not os.path.exists(os.path.join(run_dir, "control.json")):
            write_control(run_dir, state="playing", advance=advance, next_seq=0)
        self.next_seen = int(self.read().get("next_seq", 0))
        self.now = {}

    def read(self):
        try:
            return json.load(open(os.path.join(self.dir, "control.json"), encoding="utf-8"))
        except Exception:
            return {}

    def publish(self, **kw):
        self.now.update(kw)
        self.now["updated"] = time.time()
        try:
            _atomic_json(os.path.join(self.dir, "now.json"), self.now)
        except OSError:
            pass

    def gate(self, on_resume=None):
        t0, prev, last = None, None, 0.0
        while True:
            c = self.read()
            if c.get("state") == "stopped":
                raise StopDemo()
            if c.get("state") != "paused":
                break
            if t0 is None:
                t0, prev = time.time(), self.now.get("status")
            if time.time() - last >= 2:                    # 第一次與之後每 2 秒（自癒）
                self.publish(status="paused")
                last = time.time()
            time.sleep(0.25)
        if t0 is None:
            return 0.0
        self.next_seen = max(self.next_seen, int(self.read().get("next_seq", 0)))
        self.publish(status=prev if prev not in (None, "paused") else "running")
        if on_resume:
            on_resume()
        return time.time() - t0

    def step_mode(self):
        return self.read().get("advance") == "step"

    def wait_next(self, status="waiting_next", refresh=None):
        t0, last, last_r = time.time(), 0.0, 0.0
        while True:
            if time.time() - last >= 2:                    # 第一次與之後每 2 秒重寫狀態（某次寫入失敗也會自己補上）
                self.publish(status=status)
                last = time.time()
            if refresh and time.time() - last_r >= 0.5:    # 舞台：外框跟著元素走（新訊息會把內容往上推）
                try:
                    refresh()
                except Exception:
                    pass
                last_r = time.time()
            c = self.read()
            if c.get("state") == "stopped":
                raise StopDemo()
            if int(c.get("next_seq", 0)) > self.next_seen:
                self.next_seen = int(c["next_seq"])
                break
            time.sleep(0.25)
        self.publish(status="running")
        return time.time() - t0


class NoControl:
    def gate(self, on_resume=None):
        return 0.0

    def publish(self, **kw):
        pass

    def step_mode(self):
        return False

    def wait_next(self, status="waiting_next", refresh=None):
        return 0.0


def screen_size():
    """邏輯解析度（與瀏覽器 --window-* 的單位一致）。沒有 tk 時退回 1920×1080。"""
    # 在子行程裡量：tk 在沒有畫面的環境（Linux 沒有 DISPLAY、macOS 的背景行程）可能直接 abort，不會丟例外
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return 1920, 1080
    try:
        p = subprocess.run([sys.executable, "-c", "import tkinter;r=tkinter.Tk();r.withdraw();"
                            "print(r.winfo_screenwidth(),r.winfo_screenheight());r.destroy()"],
                           capture_output=True, text=True, timeout=10)
        w, h = (int(x) for x in p.stdout.split()[:2])
        return w, h
    except Exception:
        return 1920, 1080


def layout_of(rb):
    """看得到的模式：瀏覽器視窗與字幕列的位置。side＝帳號並排（預設），stack＝疊放（輪到誰誰在前，盡力而為）。"""
    lay = dict(rb.get("layout") or {})
    sw, sh = lay.get("screen") or screen_size()
    bar_h = int(lay.get("bar_h", 170))
    task = int(lay.get("taskbar_h", 48))
    accts = list(rb["accounts"])
    win_h = sh - bar_h - task
    wins = {}
    for i, a in enumerate(accts):
        if lay.get("mode", "side") == "stack" or len(accts) == 1:
            wins[a] = (0, 0, sw, win_h)
        else:
            w = sw // len(accts)
            wins[a] = (i * w, 0, w, win_h)
    return {"windows": wins, "bar": (0, win_h, sw, bar_h), "screen": (sw, sh), "mode": lay.get("mode", "side")}


class Browser:
    def __init__(self, rb, headed, log):
        self.rb, self.headed, self.log = rb, headed, log
        self.started = set()
        self.cur_url = {}
        self.layout = layout_of(rb) if headed else None

    def prof(self, acct):
        p = self.rb["accounts"][acct]["profile"]
        return p if os.path.isabs(p) else os.path.join(self.rb["_dir"], p)

    def sess(self, acct):
        return self.rb["accounts"][acct].get("session") or ("rb-" + acct)

    def neb(self, acct, *cmd, approval=None, step=None, timeout=90):
        g = ["--session", self.sess(acct), "--profile", self.prof(acct)]
        if self.headed:
            g.append("--headed")
            x, y, w, h = self.layout["windows"][acct]
            g += ["--args", "--window-position=%d,%d\n--window-size=%d,%d" % (x, y, w, h)]
        extra = ["--runbook-approval", approval, "--step", str(step)] if approval else []
        p = subprocess.run([sys.executable, NEB] + g + list(cmd) + extra, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=dict(os.environ, NO_ERRANDS_HOME=policy.HOME, PYTHONIOENCODING="utf-8"))
        return p.returncode, p.stdout, p.stderr

    def ensure(self, acct):
        """每個帳號的第一個動作前：關掉可能以另一種模式（headless/headed）開著的同名 session，設定視窗大小。"""
        if acct in self.started:
            return
        self.neb(acct, "close")
        self.neb(acct, "open", "about:blank")
        if not self.headed:                        # 看得到的模式用真的視窗大小，不另外模擬 viewport
            w, h = (self.rb.get("viewport") or [1400, 900])
            self.neb(acct, "set", "viewport", str(w), str(h))
        self.started.add(acct)

    def return_to(self, acct):
        """暫停後繼續：講者可能點走或捲動；不在最近一次開啟的網址路徑就先回去。回傳是否有回去。"""
        want = self.cur_url.get(acct)
        if not want or acct not in self.started:
            return False
        import urllib.parse
        rc, cur, _ = self.neb(acct, "get", "url")
        cur = (cur.strip().splitlines() or [""])[-1].strip()
        p = lambda u: (urllib.parse.urlparse(u).path or "/").rstrip("/") or "/"
        if p(cur) != p(want):
            self.neb(acct, "open", want)
            time.sleep(1.5)
            return True
        return False

    def close_all(self):
        for acct in list(self.started):
            self.neb(acct, "close")
        self.started.clear()

    def snapshot(self, acct, scope=None):
        cmd = ["snapshot", "-i"] + (["-s", scope] if scope else [])
        rc, o, e = self.neb(acct, *cmd)
        items = []
        for line in o.splitlines():
            m = SNAP_RE.match(line)
            if m:
                items.append({"role": m.group(1), "name": m.group(2).replace('\\"', '"'), "ref": m.group(4)})
        return rc, items

    def text(self, acct, scope=None):
        rc, o, e = self.neb(acct, "get", "text", scope or "body")
        return o if rc == 0 else ""

    def find(self, acct, target):
        """依序試每個 scope × 每個名稱；回 (ref, used) 或 (None, 試過的摘要)。
        名稱要完全相同（neb 依名稱比對核准清單）；同名多個時取 pick（預設 last＝最新的卡片）。"""
        scopes = target.get("scopes") or [None]
        tried = []
        for sc in scopes:
            rc, items = self.snapshot(acct, sc)
            if rc != 0:
                tried.append({"scope": sc, "error": "snapshot rc=%s" % rc})
                continue
            for nm in names_of(target):
                hits = [i for i in items if i["name"] == nm and (not target.get("role") or i["role"] == target["role"])]
                if hits:
                    h = hits[0] if target.get("pick") == "first" else hits[-1]
                    return h["ref"], {"scope": sc, "name": nm, "n_hits": len(hits)}
            tried.append({"scope": sc, "n_items": len(items),
                          "near": [i["role"] + ":" + i["name"][:80] for i in items
                                   if any(n[:2] in i["name"] for n in names_of(target))][:6]})
        return None, tried


# ---------------------------------------------------------------- 執行

def err_of(rc, out_text):
    """紀錄只留結構：neb 自己的拒絕訊息（守門規則的文字）或 rc；不留 agent-browser 輸出（可能含頁面文字）。"""
    for line in reversed((out_text or "").strip().splitlines()):
        try:
            j = json.loads(line)
        except Exception:
            continue
        if isinstance(j, dict) and j.get("tool") == "neb" and j.get("error"):
            return "neb：" + str(j["error"])[:160]
    if '"tool": "neb"' in (out_text or ""):
        try:
            j = json.loads(out_text[out_text.find("{"):])
            return "neb：" + str(j.get("error"))[:160]
        except Exception:
            pass
    return "agent-browser rc=%s" % rc


def count_of(body, x, is_re):
    return len(re.findall(x, body)) if is_re else body.count(x)


def resubst(pat, vars_):
    """正規表示式裡的 {變數} 代入時先跳脫（案件編號等值不會被當成語法）。"""
    return VAR_RE.sub(lambda m: re.escape(str(vars_.get(m.group(1), m.group(0)))), pat)


def subst(text, vars_):
    return VAR_RE.sub(lambda m: str(vars_.get(m.group(1), m.group(0))), text)


def act_acct(st, act):
    return st["account"]


def run_action(br, rb, st, act, vars_, approval, rec, ctl=None):
    """回 (ok, info)。info 只含結構資訊，不含輸入文字。耗時（ms）不含暫停時間（另記 paused_ms）。"""
    ctl = ctl or NoControl()
    acct, d = st["account"], act["do"]
    resume = lambda: rec.setdefault("returned_after_pause", 0) if not br.return_to(acct) else \
        rec.__setitem__("returned_after_pause", rec.get("returned_after_pause", 0) + 1)
    pre = ctl.gate(on_resume=resume)                        # 安全點：動作之前
    paused = 0.0                                            # 動作進行中（等待迴圈裡）的暫停
    t0 = time.time()
    info = {"do": d}
    if d == "open":
        url = rb["target"]["base_url"].rstrip("/") + subst(act["url"], vars_)
        rc, o, e = br.neb(acct, "open", url)
        ok = rc == 0
        if ok:
            br.cur_url[acct] = url
        else:
            info["error"] = err_of(rc, o)
    elif d == "pause":
        end = float(act.get("s", 1))
        while time.time() - t0 - paused < end:
            time.sleep(0.2)
            paused += ctl.gate(on_resume=resume)
        ok = True
    elif d in ("fill", "click", "check"):
        settle = float(act.get("settle_s", 0))           # 先等目標出現／可操作（learn 依「就地重試才成功」的紀錄提出）
        while True:
            ref, used = br.find(acct, act["target"])
            info.pop("error", None)
            if ref and getattr(br, "look", None) is not None:
                br.look.set(acct, "@" + ref, scroll=True)      # 舞台：agent 要動的元素先框住
                time.sleep(float(act.get("show_s", 0.8)))
            if ref:
                info["used"] = used
                cmd = [d, "@" + ref] + ([subst(act["text"], vars_)] if d == "fill" else [])
                rc, o, e = br.neb(acct, *cmd, approval=approval, step=st["id"])
                ok = rc == 0
                if not ok:
                    info["error"] = err_of(rc, o)
                    if info["error"].startswith("neb："):   # 守門拒絕不重試
                        break
            else:
                ok, info["error"], info["tried"] = False, "找不到目標", used
            if ok or time.time() - t0 - paused >= settle:
                break
            time.sleep(0.5)
            paused += ctl.gate(on_resume=resume)
            info["settled"] = True
        if ok:
            info.pop("tried", None)
    elif d == "mark":
        # 記下這幾段文字目前出現幾次（給後面的 wait_text new_only 比較）：聊天、通知這種會累積歷史的頁面必用
        marks = rec.setdefault("_marks", {})
        is_re = "regex" in act
        for x in ([act.get("regex", act.get("text"))] if isinstance(act.get("regex", act.get("text")), str)
                  else act.get("regex", act.get("text"))):
            x = resubst(x, vars_) if is_re else subst(x, vars_)
            marks[x] = max(count_of(br.text(acct, sc), x, is_re) for sc in (act.get("scopes") or [None]))
            if act.get("keep"):                            # 跨步驟保存（例：通知牆在後面的步驟才會出現新的一則）
                vars_.setdefault("__marks", {})[x] = marks[x]
        ok = True
    elif d == "press":
        if getattr(br, "look", None) is not None and act.get("show_s", 1.0):
            time.sleep(float(act.get("show_s", 1.0)))        # 舞台：打完先停一下讓觀眾看到內容，再送出
        rc, o, e = br.neb(act_acct(st, act), "press", act["key"], approval=approval, step=st["id"])
        ok = rc == 0
        if not ok:
            info["error"] = err_of(rc, o)
    elif d == "scroll":
        rc, o, e = br.neb(acct, "scroll", act.get("dir", "down"), str(act.get("px", 600)))
        ok = rc == 0
    elif d in ("wait_text", "expect_absent"):
        budget = float(act.get("budget_s", 30))
        scopes = act.get("scopes") or [None]
        is_re = "regex" in act                              # regex：同一則訊息裡同時要有案件編號與狀態文字
        raw = act["regex"] if is_re else act["text"]
        texts = [(resubst(x, vars_) if is_re else subst(x, vars_)) for x in ([raw] if isinstance(raw, str) else raw)]
        ok, hit = False, None
        marks = dict(vars_.get("__marks") or {}, **rec.get("_marks", {}))
        while True:
            for sc in scopes:
                body = br.text(acct, sc)
                if act.get("new_only"):                     # 只認本次新出現的（比 mark 時多）
                    found = [x for x in texts if count_of(body, x, is_re) > marks.get(x, 0)]
                else:
                    found = [x for x in texts if count_of(body, x, is_re) > 0]
                if (d == "wait_text" and found) or (d == "expect_absent" and not found and body):
                    ok, hit = True, {"scope": sc, "text_index": texts.index(found[0]) if found else None}
                    break
            waited = time.time() - t0 - paused
            if ok or waited >= budget:
                break
            if waited > 3:
                ctl.publish(status="waiting_system", waited_s=int(waited),
                            wait_label=act.get("label") or st.get("wait_label") or "系統回應",
                            expect_s=act.get("expect_s"), slow_s=act.get("slow_s"),
                            slow_say=act.get("slow_say") or st.get("slow_say") or rb.get("slow_say") or
                            "系統還在處理，通常再等一下就好；我們先看上一次跑出來的結果。")
            time.sleep(float(act.get("poll_s", 1.5)))
            paused += ctl.gate(on_resume=resume)           # 安全點：等待迴圈每一輪；暫停時預算凍結
        ctl.publish(status="running", waited_s=0, expect_s=None, slow_s=None)
        info["used"] = hit
        info["budget_s"] = budget
        if not ok:
            info["error"] = "逾時（%.0f 秒）沒看到預期文字" % budget if d == "wait_text" else "不該出現的文字還在"
    elif d == "capture":
        ok = False
        for sc in act.get("scopes") or [None]:
            ms = re.findall(act["regex"], br.text(acct, sc))
            if ms:
                vars_[act["var"]] = ms[-1] if act.get("pick", "last") == "last" else ms[0]
                ok, info["used"] = True, {"scope": sc, "n": len(ms)}
                break
        if not ok:
            info["error"] = "沒抓到 %s" % act["var"]
    elif d == "snapshot_ok":
        ref, used = br.find(acct, act["target"])
        ok = bool(ref)
        info["used" if ok else "tried"] = used
        if not ok:
            info["error"] = "畫面上沒有預期的元素"
    else:
        ok, info["error"] = False, "未知動作"
    info["ok"], info["ms"] = ok, int((time.time() - t0 - paused) * 1000)
    if pre + paused:
        info["paused_ms"] = int((pre + paused) * 1000)
    return ok, info


# ---------------------------------------------------------------- 舞台（rb["stage"] 存在時）

def stage_on(rb):
    return bool(rb.get("stage"))


def crop_of(rb, st):
    """這一步要藏到舞台外的頁面部分（CSS px）：步驟的 crop > 劇本 stage.views[步驟 view] > 不裁。"""
    if st.get("crop") is not None:
        return st["crop"]
    views = (rb.get("stage") or {}).get("views") or {}
    return views.get(st.get("view") or "read", {})


def parse_box(o):
    """agent-browser get box 的輸出 → (x, y, w, h)；讀不到回 None。"""
    m = dict(re.findall(r"(x|y|width|height):\s*(-?[\d.]+)", o or ""))
    try:
        return [float(m["x"]), float(m["y"]), float(m["width"]), float(m["height"])]
    except (KeyError, ValueError):
        return None


class Look:
    """「看這裡」：記住目標（CSS 選取器或 ref），隨時重取位置發布到 now.json；找不到就不畫。"""

    def __init__(self, br, ctl):
        self.br, self.ctl, self.acct, self.sel = br, ctl, None, None

    def set(self, acct, sel, scroll=True):
        self.acct, self.sel = acct, sel
        if sel and scroll:
            self.br.neb(acct, "scrollintoview", sel)
            time.sleep(0.3)
        self.refresh()

    def clear(self):
        self.acct = self.sel = None
        self.ctl.publish(look=None)

    def refresh(self):
        if not self.sel:
            return
        rc, o, e = self.br.neb(self.acct, "get", "box", self.sel)
        box = parse_box(o) if rc == 0 else None
        self.ctl.publish(look={"box": box} if box else None)


def beats_of(st):
    return st.get("beats") or []


def show_beat(rb, st, b, k, n, look, ctl, vars_, live):
    """一拍：字幕＋看這裡；hold → 等講者按下一步（外框持續跟著元素），否則停 s 秒。"""
    ctl.publish(beat={"text": subst(b.get("say", ""), vars_), "i": k, "n": n})
    lk = b.get("look")
    if isinstance(lk, dict) and lk.get("css"):
        look.set(st["account"], subst(lk["css"], vars_), scroll=lk.get("scroll", True))
    elif lk is None or lk is False:
        look.clear()
    waited = 0.0
    if live and (b.get("hold") or ctl.step_mode()):
        waited = ctl.wait_next(refresh=look.refresh)
    else:
        t0 = time.time()
        while time.time() - t0 < float(b.get("s", 2.5)):
            time.sleep(0.5)
            look.refresh()
            ctl.gate()
    return waited


def stage_accounts(rb, br):
    out = {}
    for a, info in rb["accounts"].items():
        out[a] = {"role": (info or {}).get("role") or a, "color": (info or {}).get("color") or "#2f7de1",
                  "profile": br.prof(a)}
    return out


def captions_of(rb, st):
    """字幕：captions.txt（使用者用文字檔改；每行「步驟id | 句子 | 秒數(選填)」）> 步驟的 caption > say。
    字幕不在送出清單裡，改了不用重新核准。"""
    p = os.path.join(rb["_dir"], "captions.txt")
    if os.path.exists(p):
        rows = []
        for line in open(p, encoding="utf-8-sig"):
            parts = [x.strip() for x in line.rstrip("\n").split("|")]
            if len(parts) >= 2 and parts[0] == st["id"] and parts[1]:
                rows.append({"text": parts[1], "s": float(parts[2]) if len(parts) > 2 and parts[2] else 6})
        if rows:
            return rows
    c = st.get("caption")
    if isinstance(c, str):
        return [{"text": c, "s": 6}]
    if isinstance(c, list):
        return [x if isinstance(x, dict) else {"text": str(x), "s": 6} for x in c]
    return [{"text": st.get("say", ""), "s": 6}]


def run_step(br, rb, st, vars_, approval, shots_dir, ctl=None):
    ctl = ctl or NoControl()
    acct = st["account"]
    br.ensure(acct)
    rec = {"id": st["id"], "account": acct, "start": now_s(), "actions": [], "recovered": False,
           "fallback_used": False}
    t0 = time.time()

    beats = beats_of(st) if getattr(br, "look", None) is not None else []
    shown = set()

    def beats_at(i):
        for k, b in enumerate(beats, 1):
            if int(b.get("at", 0)) == i and k not in shown:
                shown.add(k)
                rec["cue_wait_s"] = round(rec.get("cue_wait_s", 0) + show_beat(rb, st, b, k, len(beats), br.look, ctl, vars_,
                                                                                 getattr(br, "live", False)), 1)

    def attempt(actions, tag, base=0):
        for i, act in enumerate(actions):
            if tag == "main":
                beats_at(i)
            ok, info = run_action(br, rb, st, act, vars_, approval, rec, ctl)
            info["i"], info["phase"] = base + i, tag
            rec["actions"].append(info)
            if not ok and act["do"] in ("fill", "click", "check", "snapshot_ok", "capture"):
                time.sleep(2)                                   # 一次就地重試（畫面可能還在更新）
                ok, info2 = run_action(br, rb, st, act, vars_, approval, rec, ctl)
                info2["i"], info2["phase"], info2["retry"] = base + i, tag, True
                rec["actions"].append(info2)
                if ok:
                    rec["recovered"] = True
            if not ok and not act.get("optional"):
                if act["do"] in ("fill", "click", "check", "snapshot_ok") and "找不到" in str(info.get("error", "")):
                    probe(actions[i + 1:], tag, base + i + 1)
                return False, i
        return True, None

    def probe(rest, tag, start):
        """同一頁（直到下一個 open）其餘的目標先全部找一遍：只拍 snapshot，不填、不點、不送任何東西。
        讓一個表單的名稱問題一次排練就全部浮出來（learn 會讀這些紀錄）。"""
        for j, act in enumerate(rest):
            if act["do"] == "open":
                break
            if act["do"] not in ("fill", "click", "check", "snapshot_ok"):
                continue
            ref, used = br.find(acct, act["target"])
            ent = {"do": act["do"], "phase": "probe", "of": tag, "i": start + j,
                   "ok": bool(ref), "ms": 0}
            ent["used" if ref else "tried"] = used
            if not ref:
                ent["error"] = "探查：找不到目標"
            rec["actions"].append(ent)

    ok, failed_at = attempt(st.get("actions") or [], "main")
    if ok and beats:
        beats_at(len(st.get("actions") or []))                # 動作都做完之後的拍子（例：看結果）
    if not ok and st.get("fallback"):
        rec["fallback_used"] = True
        fok, _ = attempt(st["fallback"], "fallback")
        if fok:
            acts = st.get("actions") or []
            if st.get("resume_at") is not None:          # 退路已取代前面的動作：從指定位置接著跑
                off = int(st["resume_at"])
            else:
                off = failed_at if st.get("retry_from_failed", True) else 0
            ok, _ = attempt(acts[off:], "after_fallback", off)
            rec["recovered"] = ok
    rec.pop("_marks", None)
    rec["ok"] = ok
    rec["paused_s"] = round(sum(x.get("paused_ms", 0) for x in rec["actions"]) / 1000.0, 1)
    rec["dur_s"] = round(time.time() - t0 - rec["paused_s"], 1)      # 不含暫停
    if shots_dir:
        p = os.path.join(shots_dir, "%s.png" % st["id"])
        br.neb(acct, "screenshot", p)
        rec["shot"] = os.path.basename(p) if os.path.exists(p) else None
    return rec


def request_approval(rb):
    """把送出清單起草到發件匣；回 outbox 的 JSON（長期核准下可能直接是 state=approved）。"""
    doc = sends_doc(rb)
    p = os.path.join(rb["_dir"], "sends.json")
    json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    r = subprocess.run([sys.executable, OUTBOX, "draft", "--service", "runbook", "--to", doc["target"],
                        "--body-file", p, "--why", rb.get("approval_why", "示範劇本：整份一次核准")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, NO_ERRANDS_HOME=policy.HOME))
    try:
        o = json.loads(r.stdout)
    except Exception:
        o = {"ok": False, "error": (r.stdout or r.stderr)[-300:]}
    o["_rc"] = r.returncode
    return o


def standing_mode(rb):
    pol = policy.load()
    host, port = policy.url_host_port(rb["target"]["base_url"])
    return policy.runbook_approval_mode(policy.sensitive_domain_entry(pol, host, port))


def remaining_runs(rec):
    """{步驟: 還剩幾次}（每一步取用得最多的那個動作）。"""
    import neb
    ap = rec["approval"]
    usage = neb.runbook_usage(rec["id"])
    return {s["step"]: int(ap.get("max_runs") or 1) - max((usage.get(str(s["step"])) or {0: 0}).values())
            for s in json.loads(rec["body"])["sends"]}


def renew_standing(rb, why):
    """長期核准下續一筆：起草同一份清單，由伺服器決定。回 (新 id 或 None, 說明)。"""
    if standing_mode(rb) != "standing":
        return None, why
    o = request_approval(rb)
    if o.get("state") == "approved":
        return o["id"], "依長期核准續一筆 %s（原因：%s；依據 %s）" % (o["id"], why, o.get("basis"))
    if o.get("id"):
        return None, "%s；已起草 %s，但伺服器沒有自動核准（%s），在發件匣頁面等使用者" % (
            why, o["id"], o.get("standing_skip") or o.get("error") or "原因不明")
    return None, "%s；續約起草失敗：%s" % (why, o.get("error"))


def usable_approval(rb, aid):
    """核准有效、內容相符、每步還有次數 → (aid, rec, None)；否則在長期核准下嘗試續約。"""
    ok, rec = check_approval(rb, aid) if aid else (False, "沒有指定核准（--approval）")
    if ok:
        rem = remaining_runs(rec)
        if rem and min(rem.values()) < 1:
            ok, rec = False, "核准 %s 的可執行次數用完了（%s 剩 0 次）" % (aid, min(rem, key=rem.get))
    if ok:
        return aid, rec, None
    new, note = renew_standing(rb, rec)
    if not new:
        return None, None, note
    ok2, rec2 = check_approval(rb, new)
    return (new, rec2, note) if ok2 else (None, None, rec2)


def check_approval(rb, aid):
    """核准存在、有效，且目前劇本的送出內容與核准版本完全相同。"""
    import outbox
    ok, rec = outbox.runbook_approval(aid)
    if not ok:
        return False, rec
    if rec["approval"]["sends_sha256"] != sends_sha(sends_of(rb)):
        return False, "劇本的送出內容和核准的版本不同（有步驟改過），需重新核准：demo_runbook.py request-approval"
    if rec["approval"]["target"] != rb["target"]["host"].lower():
        return False, "核准的目標主機和劇本不同"
    return True, rec


def runs_dir(rb):
    """紀錄放哪：劇本的 runs_dir（相對於劇本資料夾）或預設 <劇本資料夾>/runs。"""
    d = rb.get("runs_dir") or "runs"
    return os.path.normpath(d if os.path.isabs(d) else os.path.join(rb["_dir"], d))


def new_run_dir(root, mode):
    """每次執行一個資料夾；同一秒內啟動兩次（例：停止後立刻重開）時加序號，不會撞名。"""
    base = os.path.join(root, time.strftime("%Y%m%d-%H%M%S-") + mode)
    d, k = base, 1
    while True:
        try:
            os.makedirs(d)
            return d
        except FileExistsError:
            k += 1
            d = "%s-%d" % (base, k)


def runs_root(rb, persist):
    if persist:
        d = runs_dir(rb)
        os.makedirs(d, exist_ok=True)
        return d
    return tempfile.mkdtemp(prefix="ne-demo-")


def execute(rb, mode, a):
    pol = policy.load()
    persist = (pol.get("demo") or {}).get("persist", "on") == "on"
    aid = arg(a, "--approval")
    if arg(a, "--resume") or "--foreground" in a:
        ok, why = check_approval(rb, aid)          # 接續／背景子行程：沿用父行程決定的核准
        renewed = None
    else:
        aid, _rec, renewed = usable_approval(rb, aid)
        ok, why = aid is not None, renewed
    if not ok:
        out({"ok": False, "error": why, "next": "請使用者在發件匣核准劇本（demo_runbook.py request-approval）"}, 3)
    advance = "step" if "--step-mode" in a else "auto"
    if mode == "live" and "--foreground" not in a:
        # 正式示範：引擎與播放器是獨立的背景行程，指令立刻返回；講者用播放器（或 control 指令）控制
        root = runs_root(rb, persist)
        run_dir = new_run_dir(root, mode)
        write_control(run_dir, state="playing", advance=advance, next_seq=0)
        passthru = [x for k in ("--from", "--to") if arg(a, k) for x in (k, arg(a, k))]
        kw = {"stdin": subprocess.DEVNULL, "env": dict(os.environ, NO_ERRANDS_HOME=policy.HOME, PYTHONIOENCODING="utf-8")}
        log = open(os.path.join(run_dir, "engine.log"), "a", encoding="utf-8")
        # 不用 DETACHED_PROCESS：沒有主控台的引擎每啟動一個 neb／agent-browser 都會冒出一個終端機視窗
        eng = nowin.popen_background([sys.executable, os.path.abspath(__file__), "live", rb["_path"], "--approval", aid,
                                       "--foreground", "--run-dir", run_dir] + passthru, stdout=log, stderr=log, **kw)
        pl = None
        if "--no-player" not in a:          # 播放器的 tk 視窗照常顯示；主控台不顯示
            pl = nowin.popen_background([sys.executable, os.path.join(HERE, "demo_stage.py" if stage_on(rb) else "demo_player.py"),
                                          run_dir], stdout=log, stderr=log, **kw)
        out({"ok": True, "mode": "live", "run_dir": run_dir, "engine_pid": eng.pid, "approval": aid,
             "renewed": renewed,
             "player_pid": pl.pid if pl else None,
             "control": "demo_runbook.py control %s pause|resume|next|stop|auto|step" % run_dir,
             "status": "demo_runbook.py status %s" % run_dir,
             "next": "講者用播放器控制；agent 不必守在前景（出錯時再看 status）"})
    resume = arg(a, "--resume")
    if resume:
        run_dir = resume
        state = json.load(open(os.path.join(run_dir, "run.json"), encoding="utf-8"))
    else:
        run_dir = arg(a, "--run-dir")
        if not run_dir:
            root = runs_root(rb, persist)
            run_dir = new_run_dir(root, mode)
        os.makedirs(run_dir, exist_ok=True)
        state = {"mode": mode, "runbook_sha256": rb_sha(rb), "struct_sha": struct_sha(rb), "approval": aid, "started": now_s(),
                 "persist": persist, "vars": {}, "steps": [], "next": 0}
    shots = os.path.join(run_dir, "shots")
    os.makedirs(shots, exist_ok=True)
    steps = rb["steps"]
    ids = [s["id"] for s in steps]
    lo = ids.index(arg(a, "--from")) if arg(a, "--from") in ids else 0
    hi = ids.index(arg(a, "--to")) + 1 if arg(a, "--to") in ids else len(steps)
    i = max(state["next"], lo)
    br = Browser(rb, headed=(mode == "live"), log=None)
    ctl = Control(run_dir, advance=advance)
    replay = rb.get("replay")
    replay = (replay if os.path.isabs(replay) else os.path.join(rb["_dir"], replay)) if replay else None
    staged = mode == "live" and stage_on(rb)
    if staged:
        br.look = Look(br, ctl)
        br.live = True
        ctl.publish(accounts=stage_accounts(rb, br), title=rb.get("title", ""), zoom=1.0, card=None)
    ctl.publish(steps_total=hi - lo, status="running", mode=mode, title=rb.get("title", ""),
                layout=br.layout and {"bar": br.layout["bar"], "mode": br.layout["mode"]},
                planb_dir=os.path.join(rb["_dir"], "planb"), replay=replay if replay and os.path.exists(replay) else None)
    stopped = None
    skipped = []
    held = False
    try:
        if mode == "live" and ctl.read().get("hold_first") and i == lo:
            # 上場前準備（standby）：先把每個帳號的瀏覽器開到它第一步要用的頁面，停在「按下一步開始」
            ctl.publish(step_index=0, status="staging", captions=[{"text": "準備畫面中…", "s": 60}],
                        card="title" if staged else None)
            home = rb["target"]["base_url"].rstrip("/") + rb["target"].get("home_path", "/")
            for acct in rb["accounts"]:
                br.ensure(acct)
                first = next((x for s_ in steps[lo:hi] if s_["account"] == acct for x in (s_.get("actions") or [])
                              if x["do"] == "open"), None)
                url = rb["target"]["base_url"].rstrip("/") + subst(first["url"], state["vars"]) if first and "{" not in first["url"] else home
                br.neb(acct, "open", url)
                br.cur_url[acct] = url
            ctl.publish(step_index=0, role="", captions=[{"text": rb.get("ready_caption") or
                                                          "準備好了。按「下一步」開始示範。", "s": 600}],
                        card="title" if staged else None)
            ctl.wait_next(status="ready")
            held = True
        while i < hi:
            st = steps[i]
            role = (rb["accounts"].get(st["account"]) or {}).get("role") or st["account"]
            pb = os.path.join(rb["_dir"], "planb", "%s.png" % st["id"])
            if staged:
                info_ = stage_accounts(rb, br)[st["account"]]
                prev_acct = steps[i - 1]["account"] if i > lo else None
                if prev_acct and prev_acct != st["account"]:          # 換角色：明確的轉場
                    ctl.publish(card="transition", role=info_["role"], role_color=info_["color"], look=None,
                                beat={"text": "", "i": 0, "n": 0})
                    time.sleep(float((rb.get("stage") or {}).get("transition_s", 1.6)))
                ctl.publish(card=None, role=info_["role"], role_color=info_["color"], section=st.get("section", ""),
                            crop=crop_of(rb, st), look=None, beat={"text": "", "i": 0, "n": len(beats_of(st))})
            ctl.publish(step_index=i - lo + 1, step_id=st["id"], account=st["account"], role=role,
                        captions=captions_of(rb, st), on_fail=st.get("on_fail", ""), status="running", waited_s=0,
                        planb=pb if os.path.exists(pb) else None)
            cue_wait = 0.0
            if mode == "live" and (st.get("advance") == "cue" or ctl.step_mode()) and not held:
                cue_wait = ctl.wait_next()                     # 播放器「下一步」或 control next
            held = False
            ctl.gate()
            print(json.dumps({"step": st["id"]}, ensure_ascii=False), flush=True)
            rec = run_step(br, rb, st, state["vars"], aid, shots, ctl)
            if cue_wait:
                rec["cue_wait_s"] = round(cue_wait, 1)
            state["steps"].append(rec)
            i += 1
            state["next"] = i
            if not rec["ok"]:
                planb = os.path.join(rb["_dir"], "planb", "%s.png" % st["id"])
                stopped = {"failed": st["id"], "planb": planb if os.path.exists(planb) else None,
                           "fallback_text": st.get("on_fail", "")}
                ctl.publish(status="failed", captions=[{"text": st.get("on_fail") or "這一步沒有完成", "s": 60}],
                            planb=stopped["planb"])
                if mode == "rehearse" and "--continue" in a:
                    stopped = None
                    continue
                if mode == "live" and i < hi:
                    # 台上：不反覆重試；停在這一步（字幕是備案說明），講者按「下一步」跳過、或看備案／回放、或停止
                    ctl.wait_next(status="failed")
                    skipped.append(st["id"])
                    stopped = None
                    continue
                break
            if mode == "rehearse" and persist and rec.get("shot"):
                os.makedirs(os.path.join(rb["_dir"], "planb"), exist_ok=True)
                shutil.copyfile(os.path.join(shots, rec["shot"]), os.path.join(rb["_dir"], "planb", rec["shot"]))
    except StopDemo:
        stopped = {"user_stop": steps[min(i, len(steps) - 1)]["id"]}
        ctl.publish(status="stopped")
    finally:
        if mode != "live" or not stopped or "user_stop" in (stopped or {}):
            br.close_all()
        state["ended"] = now_s()
        state["stopped"] = stopped
        state["skipped_after_fail"] = skipped
        state["vars"].pop("__marks", None)
        state["vars_seen"] = sorted(state["vars"])           # 變數名；值（例如案件編號）也留在本機紀錄
        with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        if not stopped:
            ctl.publish(status="done")
        if staged and not (stopped and "user_stop" in stopped):
            # 片尾：停住讓講者可以回頭講；按「結束」由引擎自己還原，片尾卡顯示結果
            end = (rb.get("stage") or {}).get("end") or {}
            ctl.publish(card="end", look=None, card_data={"heading": end.get("heading", "示範結束"), "text": end.get("text", "")})
            try:
                ctl.wait_next(status="done")
                ok_, res_ = do_reset(rb)
                state["reset"] = {"ok": ok_, "results": res_}
                with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=1)
                ctl.publish(card="end", card_data={"heading": end.get("heading", "示範結束"), "text": end.get("text", ""),
                                                   "reset": "資料已還原" if ok_ else "還原沒有成功：請 agent 處理（%s）" % str(res_)[:60],
                                                   "reset_ok": ok_})
                time.sleep(4)
            except StopDemo:
                pass
            ctl.publish(status="closing")
    summary = {"ok": (stopped is None or "user_stop" in stopped) and not skipped, "mode": mode,
               "skipped_after_fail": skipped, "run_dir": run_dir if persist else None,
               "steps_run": len(state["steps"]), "steps_ok": sum(1 for s in state["steps"] if s["ok"]),
               "recovered": [s["id"] for s in state["steps"] if s.get("recovered")],
               "failed": [s["id"] for s in state["steps"] if not s["ok"]],
               "durations": {s["id"]: s["dur_s"] for s in state["steps"]},
               "paused_s": round(sum(s.get("paused_s", 0) for s in state["steps"]), 1), "stopped": stopped}
    if not persist:
        shutil.rmtree(run_dir, ignore_errors=True)
        summary["note"] = "demo.persist=off：一次性執行，紀錄已刪除"
    out(summary, 0 if summary["ok"] else 1)


def control_cli(a):
    """demo_runbook.py control <run 資料夾> pause|resume|next|stop|auto|step；status <run 資料夾>。"""
    run_dir = a[1]
    if not os.path.isdir(run_dir):
        out({"ok": False, "error": "找不到這個執行資料夾"}, 2)
    if a[0] == "status":
        try:
            now = json.load(open(os.path.join(run_dir, "now.json"), encoding="utf-8"))
        except Exception:
            now = {}
        try:
            c = json.load(open(os.path.join(run_dir, "control.json"), encoding="utf-8"))
        except Exception:
            c = {}
        out({"ok": True, "now": {k: now.get(k) for k in ("step_index", "steps_total", "step_id", "role", "status",
                                                         "waited_s")}, "control": c})
    cmd = a[2] if len(a) > 2 else ""
    m = {"pause": {"state": "paused"}, "resume": {"state": "playing"}, "next": {"next": True},
         "stop": {"state": "stopped"}, "auto": {"advance": "auto"}, "step": {"advance": "step"}}
    if cmd not in m:
        out({"ok": False, "error": "要 pause|resume|next|stop|auto|step"}, 2)
    out({"ok": True, "control": write_control(run_dir, **m[cmd])})


# ---------------------------------------------------------------- preflight

def run_check(chk):
    rc, o, e = run_cmd(chk["cmd"], chk.get("timeout_s", 60))
    o = o.strip()
    ok = rc == 0 and (not chk.get("expect") or bool(re.search(chk["expect"], o, re.M)))
    return ok, (o.splitlines()[-1] if o else e.strip()[-150:])


def preflight(rb, a, fix=False, say=None, item=None):
    res = []

    def add(name, ok, detail=""):
        res.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
        if say:
            say(("✓ " if ok else "✗ ") + name + ("" if ok else "：" + str(detail)[:120]))
        if item:
            item(name, ok, detail)

    for chk in rb.get("preflight") or []:
        ok, det = run_check(chk)
        if not ok and fix and chk.get("fix"):
            # standby：可修的就修一次再查（資料不是 demo-base → 還原；模型沒載入 → 暖機）
            if chk["fix"] == "reset":
                fok, fdet = do_reset(rb)
            else:
                rc, fo, fe = run_cmd(chk["fix"], chk.get("fix_timeout_s", 300))
                fok, fdet = rc == 0, (fo or fe).strip()[-150:]
            if say:
                say("  修復「%s」：%s" % (chk["name"], "完成" if fok else "失敗 " + str(fdet)[:100]))
            ok, det = run_check(chk)
            det = ("（已修復）" if ok else "") + det
        add(chk["name"], ok, det)
    br = Browser(rb, headed=False, log=None)
    try:
        for acct in rb["accounts"]:
            br.ensure(acct)
            url = rb["target"]["base_url"].rstrip("/") + rb["target"].get("home_path", "/")
            rc, o, e = br.neb(acct, "open", url)
            time.sleep(2)
            rc2, cur, _ = br.neb(acct, "get", "url")
            logged = rc == 0 and "/login" not in cur and "landing" not in cur
            who = None
            me = rb["target"].get("whoami_path")             # 例：/api/v4/users/me（GET，回 JSON）
            if logged and me:
                br.neb(acct, "open", rb["target"]["base_url"].rstrip("/") + me)
                try:
                    txt = br.text(acct)
                    who = json.loads(txt[txt.find("{"):]).get(rb["target"].get("whoami_field", "username"))
                except Exception:
                    who = None
            want = (rb["accounts"][acct] or {}).get("username", acct)
            ok = logged and (who == want if me else True)
            add("登入有效且身分正確：" + acct, ok,
                "沒有登入（登入已失效）：上場前請 agent 重新登入這個帳號（demo_runbook.py login），standby 不會自動登入"
                if not logged else ("登入的是 %s" % who) if me else "（劇本沒設 whoami_path，只檢查不在登入頁）")
    finally:
        br.close_all()
    aid = arg(a, "--approval")
    if aid:
        new, rec, note = usable_approval(rb, aid)
        if new and new != aid:
            add("依長期核准續約（不消耗送出次數）", True, note)
        ok = new is not None
        if not ok:      # 不能續約：照舊報原核准的狀態（次數用完的話下一項會顯示剩 0 次）
            ok, rec = check_approval(rb, aid)
        aid = new or aid
        add("劇本核准有效且內容相符", ok, "" if ok else rec)
        if not new and note and standing_mode(rb) == "standing":
            add("依長期核准續約", False, note)
        if ok:
            ap = rec["approval"]
            rem = remaining_runs(rec)
            low = min(rem.values()) if rem else 0
            add("每個送出步驟還剩至少 1 次（上限 %s）" % ap.get("max_runs"), low >= 1,
                "最少的是 %s：剩 %d 次" % (min(rem, key=rem.get), low) if rem else "")
            demo_at = arg(a, "--demo-at")             # 預定的示範時間（本機時間 YYYY-MM-DD HH:MM）
            if demo_at:
                try:
                    t = time.mktime(time.strptime(demo_at, "%Y-%m-%d %H:%M"))
                    add("核准到期（%s）晚於預定示範時間" % ap.get("expires"), ap.get("expires_ts", 0) > t, demo_at)
                except ValueError:
                    add("預定示範時間格式", False, "要 YYYY-MM-DD HH:MM")
    allok = all(r["ok"] for r in res)
    if fix:
        return allok, res, aid
    out({"ok": allok, "checks": res, "approval": aid}, 0 if allok else 1)


# ---------------------------------------------------------------- learn / apply

def iter_targets(rb):
    for si, st in enumerate(rb.get("steps", [])):
        for phase in ("actions", "fallback"):
            for ai, act in enumerate(st.get(phase) or []):
                yield si, st, phase, ai, act


def learn(rb, a):
    pol = policy.load()
    dm = pol.get("demo") or {}
    if dm.get("persist", "on") != "on" or dm.get("learn", "propose") not in ("propose", "auto"):
        out({"ok": True, "proposal": None, "note": "政策 demo.persist/learn 關閉：不提出修改"})
    root = runs_dir(rb)
    runs = sorted(d for d in os.listdir(root) if os.path.exists(os.path.join(root, d, "run.json"))) if os.path.isdir(root) else []
    runs = runs[-int(arg(a, "--runs", "5")):]
    if not runs:
        out({"ok": False, "error": "還沒有紀錄"}, 1)
    obs = {}   # (step, phase, i) -> {"ms": [...], "used": [...], "fail": n, "near": set()}
    issues = []
    used_runs, skipped = [], []
    for r in runs:
        st_ = json.load(open(os.path.join(root, r, "run.json"), encoding="utf-8"))
        if st_.get("struct_sha") != struct_sha(rb) and not (st_.get("struct_sha") is None and "--allow-unverified" in a):
            skipped.append(r)
            continue
        used_runs.append(r)
        for s in st_["steps"]:
            for act in s["actions"]:
                k = (s["id"], "fallback" if act["phase"] == "fallback" or act.get("of") == "fallback" else "actions", act["i"])
                o = obs.setdefault(k, {"ms": [], "used": [], "fail": 0, "ok": 0, "near": []})
                if act.get("retry") and act["ok"]:
                    o["retried"] = o.get("retried", 0) + 1
                if act["ok"]:
                    o["ok"] += 1
                    o["ms"].append(act["ms"])
                    if act.get("used"):
                        o["used"].append(act["used"])
                else:
                    o["fail"] += 1
                    for t in act.get("tried") or []:
                        for n in t.get("near") or []:
                            if n not in o["near"]:
                                o["near"].append(n)
            if not s["ok"] or s.get("recovered"):
                errs = [x.get("error", "") for x in s["actions"] if not x["ok"]]
                issues.append({"step": s["id"], "run": r, "recovered": s.get("recovered"), "errors": errs[:3]})
    changes = []
    for si, st, phase, ai, act in iter_targets(rb):
        o = obs.get((st["id"], phase, ai))
        if not o:
            continue
        base = "steps[%d].%s[%d]" % (si, phase, ai)
        if act["do"] in ("wait_text",) and o["ms"]:
            hi = max(o["ms"]) / 1000.0
            new = int(max(10, round(hi * 1.5 + 10, -1)))       # 觀測最大值 ×1.5 + 10 秒，取整到 10 秒
            cur = float(act.get("budget_s", 30))
            raw = act.get("regex", act.get("text"))
            texts = [raw] if isinstance(raw, str) else raw
            reliable = act.get("new_only") or act.get("static") or all(VAR_RE.search(t) for t in texts) or \
                all(s and "dialog" in s for s in (act.get("scopes") or [None]))
            if o["fail"]:
                new = int(max(new, cur * 1.5))
            # 放寬：一次觀測就可以。縮短：至少 3 次成功，而且斷言可靠（不會比對到以前留下的字而「秒過」）——
            # 本機模型的回應時間會飄；排練 2 的假陽性曾讓 learn 想把 360 秒縮成 10 秒。
            shrink_ok = len(o["ms"]) >= 3 and reliable
            if new > cur * 1.25 or o["fail"] or (shrink_ok and new < cur * 0.75):
                changes.append({"path": base + ".budget_s", "old": cur, "new": new,
                                "why": "觀測 %d 次成功，最長 %.1f 秒；失敗 %d 次" % (len(o["ms"]), hi, o["fail"]),
                                "needs_reapproval": False})
        if act["do"] in ("fill", "click", "check", "snapshot_ok") and o["fail"] and not o["ok"] and o["near"]:
            # 找不到目標：頁面上同角色、以候選名稱開頭的元素 → 提議加入候選名稱（送出內容改變 → 需重新核准）
            t = act["target"]
            names = names_of(t)
            found = []
            for n in o["near"]:
                role, _, nm = n.partition(":")
                if (not t.get("role") or role == t["role"]) and any(nm.startswith(b.rstrip(" *")) for b in names) \
                        and nm not in names and nm not in found:
                    found.append(nm)
            if found:
                changes.append({"path": base + ".target.names", "old": names, "new": found + names,
                                "why": "排練中找不到目標；頁面上相近的元素：%s" % "、".join(found),
                                "needs_reapproval": act["do"] != "snapshot_ok"})
        if act["do"] in ("fill", "click", "check") and o.get("retried") and not act.get("settle_s"):
            changes.append({"path": base + ".settle_s", "old": None, "new": 5,
                            "why": "有 %d 次第一下失敗、就地重試才成功（重試會讓畫面停 2 秒）；改成先等目標出現最多 5 秒" % o["retried"],
                            "needs_reapproval": False})
        if act["do"] in ("fill", "click", "check", "snapshot_ok") and o["used"]:
            t = act["target"]
            # 名稱順序：最常成功的放前面
            cnt = {}
            for u in o["used"]:
                cnt[u.get("name")] = cnt.get(u.get("name"), 0) + 1
            names = names_of(t)
            newn = sorted(names, key=lambda n: (-cnt.get(n, 0), names.index(n)))
            if newn != names:
                changes.append({"path": base + ".target.names", "old": names, "new": newn,
                                "why": "依成功次數排序 %s" % cnt, "needs_reapproval": False})
            sc = t.get("scopes") or [None]
            scnt = {}
            for u in o["used"]:
                scnt[json.dumps(u.get("scope"))] = scnt.get(json.dumps(u.get("scope")), 0) + 1
            news = sorted(sc, key=lambda s: (-scnt.get(json.dumps(s), 0), sc.index(s)))
            if news != sc:
                changes.append({"path": base + ".target.scopes", "old": sc, "new": news,
                                "why": "依成功次數排序選取範圍", "needs_reapproval": False})
    known = [{"step": i["step"], "seen": i["run"], "recovered": i["recovered"], "errors": i["errors"]} for i in issues]
    prop = {"runbook": rb["_path"], "runbook_sha256": rb_sha(rb), "made": now_s(), "from_runs": used_runs,
            "skipped_runs_other_structure": skipped,
            "changes": changes, "known_issues": known, "frozen": bool(rb.get("frozen"))}
    pdir = os.path.join(rb["_dir"], "proposals")
    os.makedirs(pdir, exist_ok=True)
    pp = os.path.join(pdir, time.strftime("%Y%m%d-%H%M%S") + ".json")
    json.dump(prop, open(pp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    diff = ["%s: %s → %s（%s）" % (c["path"], json.dumps(c["old"], ensure_ascii=False),
                                   json.dumps(c["new"], ensure_ascii=False), c["why"]) for c in changes]
    res = {"ok": True, "proposal": pp, "n_changes": len(changes), "diff": diff, "known_issues": len(known),
           "frozen": prop["frozen"]}
    if dm.get("learn") == "auto" and changes and not prop["frozen"]:
        n, reap = apply_changes(rb, prop, os.path.basename(pp), "政策 demo.learn=auto（自動套用）", auto=True)
        res.update(applied=n, needs_reapproval=reap,
                   next="已依政策自動套用並記進劇本歷史" + ("；送出內容改了，下次執行會依長期核准續約（不是長期核准就要使用者核准）" if reap else ""))
    elif dm.get("learn") == "auto" and prop["frozen"]:
        res.update(applied=0, next="劇本凍結中：不自動套用（解凍後再 learn）")
    else:
        res["next"] = "把差異清單給使用者看；他同意後才執行 demo_runbook.py apply <runbook> <proposal> --reason \"…\""
    out(res)


def set_path(obj, path, val):
    toks = re.findall(r"(\w+)(?:\[(\d+)\])?", path)
    cur = obj
    for n, (k, idx) in enumerate(toks):
        last = n == len(toks) - 1
        if idx:
            cur = cur[k][int(idx)]
            if last:
                raise ValueError("路徑不能以索引結尾")
        elif last:
            cur[k] = val
        else:
            cur = cur[k]


def apply(rb, a):
    reason = arg(a, "--reason")
    if not reason:
        out({"ok": False, "error": "套用劇本修改必須附 --reason（使用者同意的原話摘要）"}, 3)
    if rb.get("frozen"):
        out({"ok": False, "error": "劇本已凍結（重要示範前），不能修改；先 unfreeze"}, 3)
    prop = json.load(open(a[2], encoding="utf-8"))
    if prop["runbook_sha256"] != rb_sha(rb):
        out({"ok": False, "error": "提案是針對舊版劇本產生的；重新 learn"}, 3)
    n, reap = apply_changes(rb, prop, os.path.basename(a[2]), reason)
    out({"ok": True, "applied": n, "needs_reapproval": reap})


def apply_changes(rb, prop, name, reason, auto=False):
    before = sends_sha(sends_of(rb))
    for c in prop["changes"]:
        set_path(rb, c["path"], c["new"])
    ki = rb.setdefault("known_issues", [])
    for k in prop.get("known_issues") or []:
        if k not in ki:
            ki.append(k)
    h = {"ts": now_s(), "applied": name, "n_changes": len(prop["changes"]), "reason": reason}
    if auto:
        h["auto"] = True
        h["changes"] = [{"path": c["path"], "old": c["old"], "new": c["new"]} for c in prop["changes"]]
    rb.setdefault("history", []).append(h)
    save_rb(rb)
    return len(prop["changes"]), before != sends_sha(sends_of(rb))


# ---------------------------------------------------------------- 其他子指令

def login(rb, a):
    """示範帳號登入：可不可以由 agent 登入看政策檔（使用者那一側），不看劇本。實際填入由 neb 的 login 規則做：
    只在登記的示範主機登入頁、只填頁面上唯一的帳號欄與 password 型別欄；密碼走 stdin，不進命令列參數。"""
    acct, cmd = arg(a, "--account"), arg(a, "--secret-cmd")
    if acct not in (rb.get("accounts") or {}):
        out({"ok": False, "error": "劇本裡沒有這個帳號"}, 3)
    pol = policy.load()
    host, port = policy.url_host_port(rb["target"]["base_url"])
    dent = policy.sensitive_domain_entry(pol, host, port) or {}
    if acct not in (dent.get("agent_login") or []):
        out({"ok": False, "error": "使用者沒有在政策檔同意 agent 代為登入這個帳號（policy.py domain add … --agent-login …）"}, 3)
    # 和 preflight 一樣不經殼層（從 cmd／檔案總管啟動時引號與管線不會被拆錯）；本機不能有管線
    rc_, pw, err_ = run_cmd(cmd, 60)
    pw = pw.strip() if rc_ == 0 else ""
    if rc_ == 2 and "管線" in err_:
        out({"ok": False, "error": err_}, 2)
    if rc_ in (124, 127):                  # 我們自己產生的訊息（找不到程式／逾時）：直接帶出
        out({"ok": False, "error": "取不到密碼：" + err_}, 1)
    if rc_ != 0:                           # 其他：只報結束碼，不印那個指令的 stderr（可能含機密）
        out({"ok": False, "error": "取不到密碼：secret-cmd 結束碼 %d" % rc_}, 1)
    if not pw:
        out({"ok": False, "error": "取不到密碼（secret-cmd 沒有輸出）"}, 1)
    br = Browser(rb, headed=False, log=None)
    try:
        br.ensure(acct)
        br.neb(acct, "open", rb["target"]["base_url"].rstrip("/") + "/login")
        time.sleep(2)
        g = ["--session", br.sess(acct), "--profile", br.prof(acct)]
        p = subprocess.run([sys.executable, NEB] + g + ["login"], input=pw + "\n", capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=dict(os.environ, NO_ERRANDS_HOME=policy.HOME, PYTHONIOENCODING="utf-8"))
        pw = None
        time.sleep(4)
        rc, cur, _ = br.neb(acct, "get", "url")
    finally:
        pw = None
        br.close_all()
    ok = p.returncode == 0 and "/login" not in cur
    out({"ok": ok, "account": acct, "neb": err_of(p.returncode, p.stdout) if p.returncode else "ok",
         "landed": cur.strip().splitlines()[-1] if cur.strip() else ""}, 0 if ok else 1)


def do_reset(rb):
    """回 (ok, 結果或錯誤)。政策檔要有使用者對這組指令雜湊的同意。"""
    r = rb.get("reset") or {}
    consent = ((policy.load().get("demo") or {}).get("reset_consent") or {})
    if consent.get("commands_sha256") != reset_sha(rb):
        return False, "政策檔沒有使用者對「這組」還原指令的同意"
    res = []
    for c in r.get("commands") or []:
        rc, o, e = run_cmd(c, 300)
        res.append({"rc": rc, "tail": (o or e).strip().splitlines()[-1:]})
    return all(x["rc"] == 0 for x in res), res


def reset(rb, a):
    consent = ((policy.load().get("demo") or {}).get("reset_consent") or {})
    if consent.get("commands_sha256") != reset_sha(rb):
        out({"ok": False, "error": "政策檔沒有使用者對「這組」還原指令的同意（指令改過就要重新同意）",
             "commands": (rb.get("reset") or {}).get("commands"), "commands_sha256": reset_sha(rb),
             "next": "把指令給使用者看；他同意後：policy.py set demo.reset_consent <sha256> --reason \"<原話>\""}, 3)
    ok, res = do_reset(rb)
    out({"ok": ok, "results": res}, 0 if ok else 1)


# ---------------------------------------------------------------- standby（上場前準備）

def http_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def clean_profile(prof):
    """瀏覽器乾淨啟動：上次沒正常關也不跳「還原分頁」；擋掉網站通知請求與翻譯提示（不改其他設定）。"""
    pp = os.path.join(prof, "Default", "Preferences")
    try:
        d = json.load(open(pp, encoding="utf-8"))
    except FileNotFoundError:
        return True, "（新 profile）"
    except Exception as e:
        return False, "讀不到 Preferences：%s" % e
    d.setdefault("profile", {})["exit_type"] = "Normal"
    d["profile"]["exited_cleanly"] = True
    d["profile"].setdefault("default_content_setting_values", {})["notifications"] = 2
    d.setdefault("translate", {})["enabled"] = False
    d.setdefault("browser", {})["check_default_browser"] = False
    tmp = pp + ".ne-tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    replace_retry(tmp, pp)
    return True, ""


def stop_previous_standby(rb):
    """上一次 standby 留下、還停在第一步前的引擎：叫它停（它會關掉自己的瀏覽器），最多等 20 秒。"""
    p = os.path.join(rb["_dir"], "standby.json")
    try:
        prev = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    rd = prev.get("run_dir")
    if rd and os.path.isdir(rd):
        now = {}
        try:
            now = json.load(open(os.path.join(rd, "now.json"), encoding="utf-8"))
        except Exception:
            pass
        if now.get("status") not in ("done", "stopped"):
            write_control(rd, state="stopped")
            for _ in range(80):
                time.sleep(0.25)
                try:
                    if json.load(open(os.path.join(rd, "now.json"), encoding="utf-8")).get("status") in ("stopped", "done"):
                        break
                except Exception:
                    pass
            return rd
    return None


def notification_state():
    """Windows 的通知狀態（SHQueryUserNotificationState）：2 忙碌（全螢幕 app）、3 D3D 全螢幕、4 簡報模式 → 通知被壓住。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        v = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(v)) == 0:
            return v.value
    except Exception:
        pass
    return None


def standby(rb, a):
    t0 = time.time()
    lines = []

    def say(x):
        lines.append(x)
        print(x, file=sys.stderr, flush=True)

    say("上場前準備：%s" % rb.get("title", ""))
    old = stop_previous_standby(rb)
    staged = stage_on(rb)
    run_dir, stage_p, items = None, None, []
    if staged:
        # 一雙擊就先蓋上舞台背板：觀眾看到的是乾淨的檢查清單，不是主控台或被排來排去的視窗
        persist = (policy.load().get("demo") or {}).get("persist", "on") == "on"
        run_dir = new_run_dir(runs_root(rb, persist), "live")
        write_control(run_dir, state="playing", advance="auto", next_seq=0, hold_first=True)
        br0 = Browser(rb, headed=False, log=None)
        base = {"card": "checklist", "status": "standby", "title": rb.get("title", ""),
                "accounts": stage_accounts(rb, br0), "zoom": 1.0}

        def show(heading="上場前準備"):
            _atomic_json(os.path.join(run_dir, "now.json"), dict(base, card_data={"heading": heading, "items": items}))
        show()
        if "--no-player" not in a:
            lg = open(os.path.join(run_dir, "engine.log"), "a", encoding="utf-8")
            stage_p = nowin.popen_background([sys.executable, os.path.join(HERE, "demo_stage.py"), run_dir],
                                             stdin=subprocess.DEVNULL, stdout=lg, stderr=lg,
                                             env=dict(os.environ, NO_ERRANDS_HOME=policy.HOME, PYTHONIOENCODING="utf-8"))
        labels = (rb.get("stage") or {}).get("checklist") or {}

        def item(name, ok, detail="", warn=False):
            # 背板上只寫乾淨的名稱與一句講者看得懂的話（不含主機、帳號、路徑）
            lab = labels.get(name) or {}
            if name.startswith("登入有效且身分正確："):
                acct = name.split("：", 1)[1]
                role = ((rb["accounts"].get(acct) or {}).get("role")) or "示範帳號"
                lab = {"name": "示範帳號已登入：" + role, "say": "登入已過期：請在上場前請 agent 重新登入，再雙擊一次。"}
            nm = lab.get("name") or name
            items[:] = [x for x in items if x["name"] != nm]
            items.append({"name": nm, "state": "ok" if ok else ("warn" if warn else "fail"),
                          "say": "" if ok else lab.get("say") or "這一項沒有通過：處理後再雙擊一次。"})
            show()
    else:
        def item(name, ok, detail="", warn=False):
            pass
    if old:
        say("  已關掉上一次準備好的畫面（%s）" % os.path.basename(old))
    # 1. 通道
    ping = rb["target"].get("ping_url") or rb["target"]["base_url"].rstrip("/") + rb["target"].get("ping_path", "/api/v4/system/ping")
    tun_ok = http_ok(ping)
    tun_pid = None
    if not tun_ok and rb.get("tunnel"):
        lf = open(os.path.join(rb["_dir"], "tunnel.log"), "a", encoding="utf-8")
        tun_pid = nowin.popen_background(cmd_argv(rb["tunnel"]), stdin=subprocess.DEVNULL, stdout=lf, stderr=lf).pid
        for _ in range(40):
            time.sleep(0.5)
            if http_ok(ping):
                tun_ok = True
                break
        say(("✓ " if tun_ok else "✗ ") + "通道（剛剛開啟）")
    else:
        say(("✓ " if tun_ok else "✗ ") + "通道")
    item("通道", tun_ok)
    # 2–5. 檢查（可修的修一次）、登入、核准與次數（長期核准下自動續）
    aid = arg(a, "--approval") or (json.load(open(os.path.join(rb["_dir"], "standby.json"), encoding="utf-8")).get("approval")
                                   if os.path.exists(os.path.join(rb["_dir"], "standby.json")) else None) or rb.get("approval")
    if not aid:
        import outbox
        best = None
        for r in outbox.list_state("approved"):
            if r.get("service") == "runbook" and (r.get("approval") or {}).get("sends_sha256") == sends_sha(sends_of(rb)):
                if best is None or (r.get("decided") or "") > (best.get("decided") or ""):
                    best = r
        aid = best and best["id"]
    pa = list(a)
    if aid and "--approval" not in pa:
        pa += ["--approval", aid]
    ok, res, aid = preflight(rb, pa, fix=True, say=say, item=item)
    res.insert(0, {"check": "通道", "ok": tun_ok, "detail": ""})
    # 6. 備案檔
    missing = [st["id"] for st in rb["steps"] if not os.path.exists(os.path.join(rb["_dir"], "planb", "%s.png" % st["id"]))]
    rp = rb.get("replay")
    rp = (rp if os.path.isabs(rp) else os.path.join(rb["_dir"], rp)) if rp else None
    pb_ok = not missing and (not rp or os.path.exists(rp))
    res.append({"check": "備案畫面（每一步的截圖＋回放頁）", "ok": pb_ok,
                "detail": ("缺：" + "、".join(missing)) if missing else ("" if pb_ok else "回放頁不存在")})
    say(("✓ " if pb_ok else "✗ ") + "備案畫面" + ("" if pb_ok else "：" + res[-1]["detail"]))
    item("備案畫面", pb_ok)
    # 7. 瀏覽器乾淨狀態
    br = Browser(rb, headed=False, log=None)
    cl = [(acct,) + clean_profile(br.prof(acct)) for acct in rb["accounts"]]
    cok = all(x[1] for x in cl)
    res.append({"check": "瀏覽器乾淨啟動（不跳還原分頁、通知、翻譯）", "ok": cok,
                "detail": "；".join("%s %s" % (x[0], x[2]) for x in cl if x[2])})
    say(("✓ " if cok else "✗ ") + "瀏覽器乾淨啟動")
    item("瀏覽器乾淨啟動", cok)
    if staged and stage_p:
        # 勿擾：舞台是前景全螢幕時，Windows 會把通知壓住（查得到就是一項檢查；查不到不擋上場，只提醒）
        time.sleep(1.5)
        qs = notification_state()
        item("通知", qs in (2, 3, 4), warn=True)
        res.append({"check": "系統通知會被擋下（勿擾）", "ok": qs in (2, 3, 4), "detail": "state=%s" % qs, "warn": True})
    allok = tun_ok and ok and pb_ok and cok
    result = {"ok": allok, "checks": res, "approval": aid, "standby_s": None, "run_dir": run_dir, "tunnel_pid": tun_pid}
    if staged and not allok:
        _atomic_json(os.path.join(run_dir, "now.json"),
                     dict(base, card_data={"heading": "還沒準備好：照下面的說明處理後，再雙擊一次", "items": items},
                          beat={"text": "按右下角「⋯」→「停止示範」可以關掉這個畫面。", "i": 0, "n": 0}))
    if allok:
        # 8. 依當下的螢幕排好視窗、引擎與播放器停在第一步前
        sw, sh = screen_size()
        say("  螢幕 %d×%d：開啟兩個瀏覽器與播放器…" % (sw, sh))
        if not run_dir:
            persist = (policy.load().get("demo") or {}).get("persist", "on") == "on"
            run_dir = new_run_dir(runs_root(rb, persist), "live")
            write_control(run_dir, state="playing", advance="step" if "--step-mode" in a else "auto", next_seq=0,
                          hold_first=True)
        kw = {"stdin": subprocess.DEVNULL, "env": dict(os.environ, NO_ERRANDS_HOME=policy.HOME, PYTHONIOENCODING="utf-8")}
        log = open(os.path.join(run_dir, "engine.log"), "a", encoding="utf-8")
        eng = nowin.popen_background([sys.executable, os.path.abspath(__file__), "live", rb["_path"], "--approval", aid,
                                      "--foreground", "--run-dir", run_dir], stdout=log, stderr=log, **kw)
        pl = stage_p if staged else (None if "--no-player" in a else nowin.popen_background(
            [sys.executable, os.path.join(HERE, "demo_player.py"), run_dir], stdout=log, stderr=log, **kw))
        st_ = None
        for _ in range(240):
            time.sleep(0.5)
            try:
                st_ = json.load(open(os.path.join(run_dir, "now.json"), encoding="utf-8")).get("status")
            except Exception:
                st_ = None
            if st_ in ("ready", "failed", "stopped", "done") or eng.poll() is not None:
                break
        ready = st_ == "ready"
        res.append({"check": "畫面就緒、停在第一步前", "ok": ready, "detail": "" if ready else "引擎狀態：%s" % st_})
        say(("✓ " if ready else "✗ ") + "畫面就緒、停在第一步前")
        result.update(ok=ready, run_dir=run_dir, engine_pid=eng.pid, player_pid=pl and pl.pid, screen=[sw, sh])
        json.dump({"run_dir": run_dir, "approval": aid, "engine_pid": eng.pid, "player_pid": pl and pl.pid, "ts": now_s()},
                  open(os.path.join(rb["_dir"], "standby.json"), "w", encoding="utf-8"), ensure_ascii=False)
    result["standby_s"] = round(time.time() - t0, 1)
    try:                                       # 從捷徑（pythonw，沒有主控台）啟動時也留下結果
        _atomic_json(os.path.join(rb["_dir"], "standby-last.json"), result)
    except OSError:
        pass
    say(("就緒：上場時按播放器的「下一步」。" if result["ok"] else "沒有就緒：看上面打 ✗ 的項目。") +
        "（準備花了 %.0f 秒）" % result["standby_s"])
    out(result, 0 if result["ok"] else 1)


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(2)
    sub = a[0]
    if sub in ("control", "status"):
        control_cli(a)
    rb = load_rb(a[1])
    if sub == "validate":
        e = validate(rb)
        warn = []
        for label, d in (("劇本資料夾", rb["_dir"]), ("紀錄資料夾", runs_dir(rb))):
            g = in_git_tree(d)
            if g:
                warn.append("%s在 git 工作目錄（%s）裡：紀錄與截圖可能被 commit；建議移到工作目錄外" % (label, g))
        try:
            n = len(sends_of(rb))
        except ValueError as ex:
            e.append(str(ex)); n = 0
        warn += loose_waits(rb)
        e += local_cmd_errors(rb)
        out({"ok": not e, "errors": e, "warnings": warn, "steps": len(rb.get("steps", [])), "sends": n,
             "runbook_sha256": rb_sha(rb)[:16] if not e else None,
             "shell_commands": shell_commands(rb),
             "note": "shell_commands 會在本機執行：只跑自己寫的或看過的劇本"}, 0 if not e else 1)
    e = validate(rb)
    if e:
        out({"ok": False, "errors": e}, 1)
    if sub == "sends":
        doc = sends_doc(rb)
        p = arg(a, "--out") or os.path.join(rb["_dir"], "sends.json")
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        out({"ok": True, "file": p, "n": len(doc["sends"]), "sends_sha256": sends_sha(doc["sends"])[:16]})
    if sub == "request-approval":
        o = request_approval(rb)
        rc = o.pop("_rc", 1)
        out(o, rc)
    if sub == "preflight":
        preflight(rb, a)
    if sub in ("rehearse", "live"):
        execute(rb, sub, a)
    if sub == "learn":
        learn(rb, a)
    if sub == "apply":
        apply(rb, a)
    if sub in ("freeze", "unfreeze"):
        if not arg(a, "--reason"):
            out({"ok": False, "error": "需要 --reason"}, 3)
        rb["frozen"] = sub == "freeze"
        rb.setdefault("history", []).append({"ts": now_s(), sub: True, "reason": arg(a, "--reason")})
        save_rb(rb)
        out({"ok": True, "frozen": rb["frozen"]})
    if sub == "login":
        login(rb, a)
    if sub == "reset":
        reset(rb, a)
    if sub == "standby":
        standby(rb, a)
    out({"ok": False, "error": "未知子指令"}, 2)


if __name__ == "__main__":
    main()
