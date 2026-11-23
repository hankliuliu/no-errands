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


class EndDemo(StopDemo):
    """講者在示範中途按「結束示範」（control state=ending）：停下動作 → 片尾 → 還原 → 收掉舞台（5c：時間不夠時任何一步都能收）。"""
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
        self.now = {"owner_pid": os.getpid()}             # 舞台的看門狗：主控端不在了就自己撤下

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
            if c.get("state") == "ending":
                raise EndDemo()
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
            if c.get("state") == "ending":
                raise EndDemo()
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
        self._found = None                                  # 新的 snapshot 會換掉 ref：之前記住的目標作廢
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

    def find(self, acct, target, reuse=False):
        """依序試每個 scope × 每個名稱；回 (ref, used) 或 (None, 試過的摘要)。
        名稱要完全相同（neb 依名稱比對核准清單）；同名多個時取 pick（預設 last＝最新的卡片）。
        reuse：上一拍剛為同一個目標找到的 ref（10 秒內、之後沒有別的 snapshot）直接沿用，省一次 snapshot。"""
        key = (acct, json.dumps(target, ensure_ascii=False, sort_keys=True))
        f = getattr(self, "_found", None)
        if reuse and f and f[0] == key and time.time() - f[3] < 10:
            return f[1], f[2]
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
                    used = {"scope": sc, "name": nm, "n_hits": len(hits)}
                    self._found = (key, h["ref"], used, time.time())
                    return h["ref"], used
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
        first_try = True
        while True:
            ref, used = br.find(acct, act["target"], reuse=first_try)   # 第一次可以沿用拍子剛找到的目標；重試一律重新找
            first_try = False
            info.pop("error", None)
            t_act = time.time()
            if ref and getattr(br, "look", None) is not None:
                paused += br.look.act(acct, "@" + ref, press=d in ("click", "check")) or 0.0   # 舞台：示範游標移過去、按下效果
            if ref:
                info["t_ms"] = {"find": int((t_act - t0) * 1000), "pointer_ready": int((time.time() - t0) * 1000)}
                info["used"] = used
                cmd = [d, "@" + ref] + ([subst(act["text"], vars_)] if d == "fill" else [])
                # 舞台：逐步輸入（守門自己切段、每段前後讀回；暫停時停在原地、繼續時接著打）
                prog = d == "fill" and getattr(br, "look", None) is not None and getattr(ctl, "dir", None)                     and effects_of(rb)["typing"]
                if prog:
                    cmd += ["--progressive", "--pace-file", os.path.join(ctl.dir, "control.json")]
                t_neb = time.time()
                rc, o, e = br.neb(acct, *cmd, approval=approval, step=st["id"], timeout=7200 if prog else 90)
                info["t_ms"]["neb_start"] = int((t_neb - t0) * 1000)        # 量 A34：游標到位 → 送出 → 守門返回
                info["t_ms"]["neb_end"] = int((time.time() - t0) * 1000)
                if prog:
                    for line in reversed((o or "").strip().splitlines()):
                        try:
                            pj = json.loads(line)
                        except Exception:
                            continue
                        if isinstance(pj, dict) and isinstance(pj.get("progressive"), dict):
                            pi = pj["progressive"]
                            paused += float(pi.get("paused_s") or 0)      # 打字中途暫停的時間不算進這一步
                            info["typing"] = {k: pi.get(k) for k in ("chunks", "typed_chars", "ms", "paused_s")}
                            break
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
        if getattr(br, "look", None) is not None and act.get("show_s", 0.7):
            time.sleep(float(act.get("show_s", 0.7)))        # 舞台：打完先停一下讓觀眾看到內容，再送出（0.7 秒）
        rc, o, e = br.neb(act_acct(st, act), "press", act["key"], approval=approval, step=st["id"])
        ok = rc == 0
        if not ok:
            info["error"] = err_of(rc, o)
    elif d == "scroll":
        total = int(act.get("px", 600))
        if getattr(br, "look", None) is not None:          # 舞台：滾輪式分段（看得出是捲過去的）
            ok = True
            for i in range(4):
                part = total * (i + 1) // 4 - total * i // 4
                rc, o, e = br.neb(acct, "scroll", act.get("dir", "down"), str(part))
                ok = ok and rc == 0
                time.sleep(0.1)
        else:
            rc, o, e = br.neb(acct, "scroll", act.get("dir", "down"), str(total))
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


EFFECTS = ("pointer", "smooth_scroll", "typing", "shield", "faint_look")


def effects_of(rb):
    """F-3 的效果開關（劇本 stage.effects；預設全開）。任一項在真頁面上不穩就單獨關掉、退回前一種做法：
    pointer＝示範游標與按下效果（關：外框框住目標、停 0.8 秒再動作）；smooth_scroll＝平順捲動（關：瞬間捲到）；
    typing＝逐步輸入（關：一次填入、停一下再送出）；shield＝隔離層（關：沒有隔離層）；faint_look＝淡外框（關：原本的粗框、不淡出）。"""
    e = dict.fromkeys(EFFECTS, True)
    e.update({k: bool(v) for k, v in (((rb.get("stage") or {}).get("effects")) or {}).items() if k in EFFECTS})
    if (rb.get("stage") or {}).get("typing") == "instant":
        e["typing"] = False
    return e


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
    """「看這裡」：記住目標（CSS 選取器或 ref），隨時重取位置發布到 now.json；找不到就不畫。
    劇本 stage.align 有設定時，每拍也把訊息區頂端對齊到一則訊息的開頭（A9：不留被切一半的字）：
    能往上捲就捲（被切的那則從開頭顯示、目標仍完整）；捲了會把目標擠出去就請舞台用頁面底色蓋住殘段。
    全部只用唯讀的 get box／get count 和 scroll。"""

    def __init__(self, br, ctl, align=None, effects=None):
        self.br, self.ctl, self.acct, self.sel = br, ctl, None, None
        self.fx = dict.fromkeys(EFFECTS, True)
        self.fx.update(effects or {})
        self.align_cfg, self.align_on, self.box, self.anchor, self._align_t = align, False, None, None, 0.0
        self.color = (align or {}).get("color")
        self.ptr, self.ptr_seq, self.press_seq, self.look_seq = None, 0, 0, 0

    def step(self, acct, on):
        """換步驟：這一步要不要對齊；殘段遮罩清掉。換角色時示範游標從新畫面的中間偏下重新出發（舞台負責淡入）。"""
        if acct != self.acct:
            self.ptr = None
        self.acct, self.sel, self.box, self.anchor = acct, None, None, None
        self.align_on = bool(self.align_cfg and on and self.align_cfg.get("top_align", True))   # 5c：預設不對齊、不遮
        self.ctl.publish(mask=None, align=None, look=None)

    # ---- F-3：示範游標（畫在舞台上；系統游標不動）與平順捲動
    def wait(self, secs):
        """等 secs 秒；暫停的時間不算（游標停在原地）。回暫停了幾秒。"""
        t, pz = time.time(), 0.0
        while time.time() - t < secs:
            time.sleep(0.04)
            g = self.ctl.gate() or 0.0
            t += g
            pz += g
        return pz

    def point(self, x, y):
        """示範游標移到頁面上的 CSS 點；回移動秒數（依距離 0.3–0.9 秒；第一次 0.6 秒）。"""
        import math
        if not self.fx["pointer"]:
            return 0.0
        if self.ptr is None:
            dur = 0.6
        else:
            d = math.hypot(x - self.ptr[0], y - self.ptr[1])
            dur = 0.0 if d < 2 else min(0.6, max(0.3, 0.3 + d / 2000.0))    # 5c：移動上限 0.6 秒
        self.ptr, self.ptr_seq = (x, y), self.ptr_seq + 1
        self.ctl.publish(pointer={"x": round(x, 1), "y": round(y, 1), "dur": round(dur, 2), "seq": self.ptr_seq})
        return dur

    def press(self, timeout=0.6):
        """按下的效果：發布後等舞台回報「已畫出來」（stage_ack.json）才回來，之後才送出真正的點擊——
        觀眾先看到按下、頁面才有反應（A22）。沒有舞台（--no-player）時最多等 timeout 秒。回暫停秒數。"""
        if not self.fx["pointer"]:
            return 0.0
        self.press_seq += 1
        self.ctl.publish(press=self.press_seq)
        ack = os.path.join(getattr(self.ctl, "dir", "") or ".", "stage_ack.json")
        t0, pz = time.time(), 0.0
        while time.time() - t0 < timeout:
            try:
                if int(json.load(open(ack, encoding="utf-8")).get("press") or 0) >= self.press_seq:
                    self.acked = getattr(self, "acked", 0) + 1
                    break
            except Exception:
                pass
            time.sleep(0.02)
        return pz

    def into_view(self, sel):
        """目標不在可視範圍時平順地捲過去（訊息區：滾輪式分 3–4 段、約 0.4 秒）；其他地方用 scrollintoview。回目標方框。"""
        box = self._box(sel)
        if not box:
            return None
        cfg = self.align_cfg or {}
        lst = self._box(cfg["list"]) if cfg.get("list") and cfg.get("scroller") and self.fx["smooth_scroll"] else None
        if lst and lst[0] - 2 <= box[0] <= lst[0] + lst[2] and box[1] < lst[1] + lst[3] + 4000:
            top, bot = lst[1] + 8, lst[1] + lst[3] - 8
            delta = 0.0
            if box[1] < lst[1] - 1 or box[1] + box[3] > lst[1] + lst[3] + 1:   # 不完整可見才捲；捲到訊息區上方約四分之一（5c）
                want = lst[1] + 0.25 * lst[3] if box[3] <= 0.7 * lst[3] else top
                delta = box[1] - want
            if abs(delta) >= 4:
                n = 4 if abs(delta) > 240 else 3
                for i in range(n):
                    part = int(round(abs(delta) * (i + 1) / n)) - int(round(abs(delta) * i / n))
                    self.br.neb(self.acct, "scroll", "down" if delta > 0 else "up", str(part), "--selector", cfg["scroller"])
                    time.sleep(0.1)
                time.sleep(0.15)
                box = self._box(sel) or box
            return box
        self.br.neb(self.acct, "scrollintoview", sel)
        time.sleep(0.2)
        return self._box(sel) or box

    def aim(self, acct, sel, click=True):
        """講某個動作的拍子：捲到目標、示範游標指到它上面（點擊＝元素中心、輸入＝框內左側），不畫外框、不按。
        接著真的做這個動作時游標已經在位，直接按下（5c：每次點擊之前游標確實停在目標上）。"""
        if not self.fx["pointer"]:
            self.set(acct, sel, scroll=True)
            return 0.0
        self.acct, self.sel, self.box = acct, None, None
        self.ctl.publish(look=None)
        box = self.into_view(sel)
        if not box:
            return 0.0
        x, y = (box[0] + box[2] / 2, box[1] + box[3] / 2) if click else (box[0] + min(36, box[2] / 2), box[1] + box[3] / 2)
        return self.wait(self.point(x, y) + 0.2)

    def act(self, acct, sel, press):
        """動作的拍子：捲到目標 → 示範游標移過去（不畫外框，游標就是標示）→ 停 0.2 秒 → 點擊的話加按下效果。"""
        if not self.fx["pointer"]:                           # 效果關掉：前一種做法——外框框住目標、停一下再動作
            self.set(acct, sel, scroll=True)
            return self.wait(0.8)
        self.acct, self.sel, self.box = acct, None, None
        self.ctl.publish(look=None)
        box = self.into_view(sel)
        if not box:
            return 0.0
        if press:
            x, y = box[0] + box[2] / 2, box[1] + box[3] / 2      # 和真正點擊的位置（元素中心）一致
        else:
            x, y = box[0] + min(36, box[2] / 2), box[1] + box[3] / 2   # 輸入框：指在框內左側
        pz = self.wait(self.point(x, y) + 0.2)
        if press:
            pz += self.press()                                   # 舞台回報漣漪已畫出 → 立刻送出點擊（5c A34：到位到頁面反應 1 秒內）
        return pz

    def set(self, acct, sel, scroll=True):
        """讀內容的拍子：捲到那一段 → 淡外框（舞台 1.2 秒後淡出）→ 游標指在那一段開頭的左邊、不蓋住字。"""
        self.acct, self.sel = acct, sel
        self.look_seq += 1
        if sel and scroll:
            self.into_view(sel)
        self.refresh(realign=False)
        self.align()
        if self.box:
            import stage_layout
            self.point(*stage_layout.look_pointer(self.box))

    def clear(self):
        self.sel = self.box = None
        self.ctl.publish(look=None)

    def refresh(self, realign=True):
        if self.sel:
            rc, o, e = self.br.neb(self.acct, "get", "box", self.sel)
            box = parse_box(o) if rc == 0 else None
            self.box = box
            self.ctl.publish(look={"box": box, "seq": self.look_seq} if box else None)
        if realign and self.align_on and self.anchor is not None and time.time() - self._align_t >= 1.0:
            b = self._box(self.align_cfg["item_nth"].format(k=1))       # 有新訊息、版面動了 → 重新對齊
            if b is None or abs(b[1] - self.anchor[1]) > 2 or abs(b[3] - self.anchor[3]) > 2:
                self.align()

    def _box(self, sel):
        rc, o, e = self.br.neb(self.acct, "get", "box", sel)
        return parse_box(o) if rc == 0 else None

    def align(self):
        if not self.align_on or not self.acct:
            return
        import stage_layout
        cfg = self.align_cfg
        self._align_t = time.time()
        if cfg.get("skip_if"):
            rc, o, e = self.br.neb(self.acct, "get", "count", cfg["skip_if"])
            if rc == 0 and o.strip().split()[-1:] and o.strip().split()[-1].isdigit() and int(o.strip().split()[-1]) > 0:
                self.anchor = None
                self.ctl.publish(mask=None, align={"how": "skip", "ok": None, "why": "對話框開著"})
                return
        lst = self._box(cfg["list"])
        if not lst:
            self.anchor = None
            self.ctl.publish(mask=None, align={"how": "none", "ok": False, "why": "找不到訊息區"})
            return
        top = lst[1]
        last = strad = None
        for k in range(1, int(cfg.get("max_items", 15)) + 1):
            b = self._box(cfg["item_nth"].format(k=k))
            if b is None:
                break
            last = last or b
            if b[1] >= top - 1:
                continue
            if b[1] + b[3] > top + 1:
                strad = (k, b)
            break
        plan = stage_layout.align_plan(lst, strad and strad[1], self.box or last)
        rec = {"how": plan[0], "ok": plan[0] in ("ok", "scroll", "mask"), "cut": round(top - strad[1][1], 1) if strad else 0}
        mask = None
        if plan[0] == "scroll":
            self.br.neb(self.acct, "scroll", "up", str(plan[1]), "--selector", cfg["scroller"])
            time.sleep(0.25)
            b = self._box(cfg["item_nth"].format(k=strad[0]))
            rec["ok"] = bool(b and abs(b[1] - top) <= 2)
            if self.sel:
                self.refresh_box_only()
        elif plan[0] == "mask":
            mask = {"box": plan[1], "color": self.page_color()}
        elif plan[0] == "none":
            rec["why"] = plan[1]
        self.anchor = self._box(cfg["item_nth"].format(k=1))
        self.ctl.publish(mask=mask, align=rec)

    def refresh_box_only(self):
        rc, o, e = self.br.neb(self.acct, "get", "box", self.sel)
        self.box = parse_box(o) if rc == 0 else None
        self.ctl.publish(look={"box": self.box, "seq": self.look_seq} if self.box else None)

    def page_color(self):
        """遮罩用的頁面底色：劇本寫的 > 頁面的 CSS 變數（例：--center-channel-bg）> 黑。"""
        if self.color:
            return self.color
        var = (self.align_cfg or {}).get("color_var")
        if var:
            rc, o, e = self.br.neb(self.acct, "get", "styles", self.align_cfg["list"])
            m = re.search(re.escape(var) + r":\s*(#[0-9a-fA-F]{3,8})\b", o or "")
            if m:
                self.color = m.group(1)
                return self.color
        return "#000000"


def stage_fit_probe(rb, steps, ctl, run_dir, timeout=30.0):
    """A1／A19：上場前，把每個帳號的瀏覽器照它第一步的裁切擺到舞台位置（在背板下面，觀眾看不到），
    讀舞台量到的實際頁面範圍（stage_fit.json）。連續兩次合格才算；不合格回一句原因（不 READY、不上台）。
    每個帳號最多等 30 秒（5c：冷開機時瀏覽器第一次畫出頁面會慢，要會等，不要直接判定沒準備好）。"""
    p = os.path.join(run_dir, "stage_fit.json")
    for acct in rb["accounts"]:
        st = next((x for x in steps if x["account"] == acct), None)
        if not st:
            continue
        role = (rb["accounts"].get(acct) or {}).get("role") or acct
        t0 = time.time()
        ctl.publish(fit_probe=acct, crop=crop_of(rb, st))
        rec, good = None, 0
        while time.time() - t0 < timeout:
            time.sleep(0.4)
            try:
                r = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            if r.get("account") != acct or not r.get("probe") or float(r.get("t") or 0) < t0 or r.get("ok") is None:
                continue
            if rec is not None and r.get("t") == rec.get("t"):
                continue
            rec = r
            good = good + 1 if r.get("ok") else 0
            if good >= 2:
                break
        if good < 2:
            ctl.publish(fit_probe=None)
            why = list(((rec or {}).get("fit") or {}).get("why") or [])
            pe = (rec or {}).get("placement_error")
            if pe is not None and pe > 3:
                why.append("視窗沒有照要求的大小擺好（差 %d 像素）" % pe)
            return "%s的畫面：%s" % (role, "；".join(why) or "量不到頁面範圍")
    ctl.publish(fit_probe=None, crop=None)
    return None


def beats_of(st):
    return st.get("beats") or []


def beat_secs(b, text):
    """這一拍留多久（像播影片：按一次開始就自己往下走）：1.5 秒＋每字 0.25 秒、最少 3.5 秒；hold（多留一會兒）再加 2.5 秒。"""
    if b.get("s") is not None:
        return float(b["s"])
    return max(3.0, 1.5 + 0.2 * len(text))     # 5c：最短 3 秒、每字 0.2 秒；重點拍不再加時（講者模式本來就每步停一次）


def beat_target(st, b):  # noqa: C901
    """這一拍要看的東西：劇本寫的 look（CSS 或 {"target": 角色＋名稱}）；沒寫（None）就用這一拍接著要做的那個動作的目標
    （5c：每一拍都要有自己真正的目標，不沿用上一拍）。False＝不框。"""
    lk = b.get("look")
    if lk is None:
        acts = st.get("actions") or []
        i = int(b.get("at", 0))
        if i < len(acts) and acts[i].get("do") in ("click", "fill", "check") and acts[i].get("target"):
            return {"target": acts[i]["target"], "action": acts[i]["do"]}
    return lk


def show_beat(rb, st, b, k, n, look, ctl, vars_, live):
    """一拍：先把這一拍的目標捲到看得見、框好，再出字幕（5c：先捲到再出字幕）。
    自動模式（預設）：字幕留 beat_secs 秒、暫停時計時凍結；逐步模式：hold 的拍才停下等「下一步」。"""
    lk = beat_target(st, b)
    if isinstance(lk, dict) and lk.get("css"):
        look.set(st["account"], subst(lk["css"], vars_), scroll=lk.get("scroll", True))
    elif isinstance(lk, dict) and lk.get("target"):
        ref, _ = look.br.find(st["account"], lk["target"])
        if ref and lk.get("action"):                          # 講接下來的動作：游標指到目標上（不框）
            look.aim(st["account"], "@" + ref, click=lk["action"] in ("click", "check"))
        elif ref:
            look.set(st["account"], "@" + ref, scroll=lk.get("scroll", True))
    elif lk is False:
        look.clear()
    ctl.publish(beat={"text": subst(b.get("say", ""), vars_), "i": k, "n": n})
    t0 = time.time()                                          # 字幕出來才開始計時
    dur = beat_secs(b, subst(b.get("say", ""), vars_))
    while time.time() - t0 < dur:                           # (停)＝多留一會兒；講者模式只在步與步之間停
        time.sleep(0.5)
        look.refresh()
        t0 += ctl.gate()                                     # 暫停的時間不算
    return 0.0


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
            if not ok and act["do"] in ("fill", "click", "check", "snapshot_ok", "capture") and not act.get("optional"):
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
        write_control(run_dir, state="playing", advance=advance, next_seq=0, unattended="--presenter" not in a)
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
        br.look = Look(br, ctl, align=(rb.get("stage") or {}).get("align"), effects=effects_of(rb))
        ctl.publish(effects=effects_of(rb))
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
            # 片頭從第一刻就完整（副標、模式）——準備階段也是觀眾會看到的畫面（5c：擷取裡有一格片頭少了副標與模式）
            ctl.publish(step_index=0, status="staging", captions=[{"text": "準備畫面中…", "s": 60}],
                        card="title" if staged else None,
                        card_data={"subtitle": (rb.get("stage") or {}).get("subtitle", ""),
                                   "mode": "按下一步前進" if ctl.step_mode() else "自動播放"} if staged else None)
            home = rb["target"]["base_url"].rstrip("/") + rb["target"].get("home_path", "/")
            for acct in rb["accounts"]:
                br.ensure(acct)
                first = next((x for s_ in steps[lo:hi] if s_["account"] == acct for x in (s_.get("actions") or [])
                              if x["do"] == "open"), None)
                url = rb["target"]["base_url"].rstrip("/") + subst(first["url"], state["vars"]) if first and "{" not in first["url"] else home
                br.neb(acct, "open", url)
                br.cur_url[acct] = url
            fit_why = stage_fit_probe(rb, steps[lo:hi], ctl, run_dir) if staged else None
            if fit_why:
                stopped = {"stage_fit": fit_why}
                ctl.publish(status="failed", fit_fail=fit_why)
                i = hi
            else:
                ctl.publish(step_index=0, role="", captions=[{"text": rb.get("ready_caption") or
                                                              "準備好了。按「下一步」開始示範。", "s": 600}],
                            card="title" if staged else None,
                            card_data={"subtitle": (rb.get("stage") or {}).get("subtitle", ""),
                                       "mode": "按下一步前進" if ctl.step_mode() else "自動播放"} if staged else None)
            if fit_why:
                pass
            elif staged and not ctl.step_mode():
                # 自動模式（排練、驗收、agent 示範）：不等任何人按——片頭留幾秒就開始
                t_ = time.time()
                ctl.publish(status="ready")
                while time.time() - t_ < float((rb.get("stage") or {}).get("title_s", 5)):
                    time.sleep(0.25)
                    t_ += ctl.gate()
            else:
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
                al = (rb.get("stage") or {}).get("align") or {}
                br.look.step(st["account"], (st.get("view") or "read") in (al.get("views") or [st.get("view") or "read"]))
            ctl.publish(step_index=i - lo + 1, step_id=st["id"], account=st["account"], role=role,
                        captions=captions_of(rb, st), on_fail=st.get("on_fail", ""), status="running", waited_s=0,
                        planb=pb if os.path.exists(pb) else None)
            cue_wait = 0.0
            if mode == "live" and ((st.get("advance") == "cue" and not staged) or ctl.step_mode()) and not held:
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
                    # 台上：不反覆重試；停在這一步（字幕是備案說明）。講者模式等「下一步」跳過；
                    # 自動模式（排練、agent 示範）沒有人會按 → 顯示備案說明幾秒後自己跳過（排練 3 在這裡卡了 40 分鐘）
                    if ctl.step_mode() or not staged:
                        ctl.wait_next(status="failed")
                    else:
                        t_ = time.time()
                        while time.time() - t_ < float((rb.get("stage") or {}).get("fail_hold_s", 8)):
                            time.sleep(0.25)
                            t_ += ctl.gate()
                    skipped.append(st["id"])
                    stopped = None
                    continue
                break
            if mode == "rehearse" and persist and rec.get("shot"):
                os.makedirs(os.path.join(rb["_dir"], "planb"), exist_ok=True)
                shutil.copyfile(os.path.join(shots, rec["shot"]), os.path.join(rb["_dir"], "planb", rec["shot"]))
    except EndDemo:
        # 中途結束：照常走片尾與還原（控制狀態改回 playing，片尾的等待才不會又被當成結束）
        stopped = {"ended_early": steps[min(i, len(steps) - 1)]["id"]}
        try:
            write_control(run_dir, state="playing")
        except Exception:
            pass
        ctl.publish(status="running", look=None, pointer=None)
    except StopDemo:
        sid_ = steps[min(i, len(steps) - 1)]["id"]
        try:
            why_ = str(json.load(open(os.path.join(run_dir, "control.json"), encoding="utf-8")).get("dropped") or "")
        except Exception:
            why_ = ""
        # 舞台的看門機制收掉舞台（更新迴圈卡死）不是使用者停止：照實記原因（5c：紀錄的原因要寫對）
        stopped = {"stage_hang": sid_, "why": why_[:80]} if why_.startswith("watchdog") else {"user_stop": sid_}
        ctl.publish(status="stopped")
    finally:
        if mode != "live" or not stopped or any(k in (stopped or {}) for k in ("user_stop", "stage_fit", "ended_early", "stage_hang")):
            br.close_all()
        if staged and stopped and ("user_stop" in stopped or "stage_hang" in stopped):
            # 緊急停止（Ctrl+Shift+F12、舞台被撤下）：沒有片尾，但資料照樣還原（不留給別人收）
            try:
                ok_, res_ = do_reset(rb)
                state["reset"] = {"ok": ok_, "results": res_}
            except Exception as e:
                state["reset"] = {"ok": False, "results": [str(e)[:120]]}
        state["ended"] = now_s()
        state["stopped"] = stopped
        state["skipped_after_fail"] = skipped

        state["vars"].pop("__marks", None)
        state["vars_seen"] = sorted(state["vars"])           # 變數名；值（例如案件編號）也留在本機紀錄
        with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        if not stopped:
            ctl.publish(status="done")
        if staged and stopped and "stage_hang" in stopped:
            show_hang_notice(rb, run_dir, ctl, state.get("reset") or {}, launch="--no-player" not in a)
        if staged and not (stopped and ("user_stop" in stopped or "stage_fit" in stopped or "stage_hang" in stopped)):
            # 片尾：停住讓講者可以回頭講；按「結束」由引擎自己還原，片尾卡顯示結果
            end = (rb.get("stage") or {}).get("end") or {}
            ctl.publish(card="end", look=None, beat={"text": "", "i": 0, "n": 0},
                        card_data={"heading": end.get("heading", "示範結束"), "text": end.get("text", ""),
                                   "points": end.get("points") or []})
            try:
                if ctl.step_mode():
                    ctl.wait_next(status="done")                 # 講者模式：片尾停住，按「結束」才還原
                else:
                    t_ = time.time()
                    while time.time() - t_ < float(end.get("hold_s", 12)) and int(ctl.read().get("next_seq", 0)) <= ctl.next_seen:
                        time.sleep(0.25)
                        t_ += ctl.gate()
                ok_, res_ = do_reset(rb)
                state["reset"] = {"ok": ok_, "results": res_}
                with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=1)
                ctl.publish(card="end", card_data={"heading": end.get("heading", "示範結束"), "text": end.get("text", ""),
                                                   "points": end.get("points") or [],
                                                   "reset": "資料已還原" if ok_ else "還原沒有成功：請 agent 處理（%s）" % str(res_)[:60],
                                                   "reset_ok": ok_})
                time.sleep(4)
            except StopDemo:
                pass
            ctl.publish(status="closing")
    summary = {"ok": (stopped is None or "user_stop" in stopped or "ended_early" in stopped) and not skipped, "mode": mode,
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


def show_hang_notice(rb, run_dir, ctl, reset, hold_s=10.0, launch=True):
    """舞台卡死被看門機制收掉之後：重開一個舞台，告訴講者發生了什麼（不是畫面直接消失），停 hold_s 秒或按「下一步」後收掉。"""
    try:
        write_control(run_dir, state="playing")              # 新的舞台看到 stopped 會直接撤下
        if launch:                                           # --no-player（測試）：不開視窗，只發布
            lg = open(os.path.join(run_dir, "engine.log"), "a", encoding="utf-8")
            nowin.popen_background([sys.executable, os.path.join(HERE, "demo_stage.py"), run_dir],
                                   stdin=subprocess.DEVNULL, stdout=lg, stderr=lg,
                                   env=dict(os.environ, NO_ERRANDS_HOME=policy.HOME, PYTHONIOENCODING="utf-8"))
        ok_ = bool(reset.get("ok"))
        ctl.publish(status="running", card="end", look=None, pointer=None, beat={"text": "", "i": 0, "n": 0},
                    card_data={"heading": "示範中斷：畫面卡住，已自動收起",
                               "text": "不是操作造成的。可以改放備案畫面接著講。",
                               "points": [], "reset": "資料已還原" if ok_ else "還原沒有成功：請 agent 處理",
                               "reset_ok": ok_})
        t_ = time.time()
        seen = int(ctl.read().get("next_seq", 0))
        while time.time() - t_ < hold_s and int(ctl.read().get("next_seq", 0)) <= seen:
            time.sleep(0.25)
    except Exception:
        pass
    ctl.publish(status="closing")


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


def screen_locked():
    """A36：電腦是不是鎖定（登入畫面蓋在上面）。用工作階段的鎖定狀態（WTSQuerySessionInformation 的
    WTSSessionInfoEx → SessionFlags：0＝鎖定、1＝未鎖定；Windows 10 以後）。Windows 11 鎖定時輸入桌面仍回報
    Default，不能用桌面名稱判斷（候選二實測）。查不到回 False（不擋）。測試用 NE_TEST_SCREEN_LOCKED=1／0 覆寫。"""
    v = os.environ.get("NE_TEST_SCREEN_LOCKED")
    if v in ("0", "1"):
        return v == "1"
    if os.name != "nt":
        return False
    try:
        import ctypes
        import struct
        from ctypes import wintypes
        w = ctypes.windll.wtsapi32
        p, n = ctypes.c_void_p(), wintypes.DWORD()
        if not w.WTSQuerySessionInformationW(None, wintypes.DWORD(0xFFFFFFFF), 25, ctypes.byref(p), ctypes.byref(n)):
            return False
        try:
            b = ctypes.string_at(p, n.value)
            level, _pad, _sid, _state, flags = struct.unpack_from("<IIIii", b, 0)   # WTSINFOEXW：Level、對齊、LEVEL1
            return level == 1 and flags == 0
        finally:
            w.WTSFreeMemory(p)
    except Exception:
        return False


LOCKED_SAY = "電腦目前是鎖定狀態，請先解鎖，再雙擊一次。"


def standby(rb, a):
    t0 = time.time()
    lines = []

    def say(x):
        lines.append(x)
        print(x, file=sys.stderr, flush=True)

    say("上場前準備：%s" % rb.get("title", ""))
    if screen_locked():
        # A36（5c）：鎖定時我們的視窗不在顯示中的桌面上、瀏覽器停止繪製——照常往下跑只會在奇怪的地方失敗（候選二）
        say("✗ " + LOCKED_SAY)
        result = {"ok": False, "checks": [{"check": "電腦沒有鎖定", "ok": False, "detail": LOCKED_SAY}],
                  "locked": True, "standby_s": round(time.time() - t0, 1), "run_dir": None}
        try:
            _atomic_json(os.path.join(rb["_dir"], "standby-last.json"), result)
        except OSError:
            pass
        out(result, 1)
    old = stop_previous_standby(rb)
    staged = stage_on(rb)
    run_dir, stage_p, items = None, None, []
    if staged:
        # 一雙擊就先蓋上舞台背板：觀眾看到的是乾淨的檢查清單，不是主控台或被排來排去的視窗
        persist = (policy.load().get("demo") or {}).get("persist", "on") == "on"
        run_dir = new_run_dir(runs_root(rb, persist), "live")
        # 模式由啟動方式決定（使用者：「demo要自動做下去，只有我自己上場才讓我按下個步驟」）：
        # 上場捷徑帶 --presenter → 每一步做完等「下一步」；其他（排練、agent 示範）→ 自動、無人值守
        presenter = "--presenter" in a
        write_control(run_dir, state="playing", advance="step" if presenter else "auto", next_seq=0, hold_first=True,
                      unattended=not presenter)
        br0 = Browser(rb, headed=False, log=None)
        base = {"card": "checklist", "status": "standby", "title": rb.get("title", ""), "owner_pid": os.getpid(),
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
        for _ in range(360):                          # 冷開機（剛開機、第一次開瀏覽器）時開頁、量版面會慢：最多等 3 分鐘才判定沒準備好
            time.sleep(0.5)
            try:
                st_ = json.load(open(os.path.join(run_dir, "now.json"), encoding="utf-8")).get("status")
            except Exception:
                st_ = None
            if st_ in ("ready", "failed", "stopped", "done") or eng.poll() is not None:
                break
        ready = st_ == "ready"
        if staged:
            try:
                fw = json.load(open(os.path.join(run_dir, "now.json"), encoding="utf-8")).get("fit_fail")
            except Exception:
                fw = None
            res.append({"check": "頁面貼齊螢幕底邊、舞台沒有空白帶（實測）", "ok": not fw and ready, "detail": fw or ""})
            if fw:
                say("✗ 畫面版面：" + fw)
                labels.setdefault("畫面版面", {"name": "畫面版面（頁面貼齊螢幕）", "say": fw + "。請 agent 處理後再雙擊一次。"})
                item("畫面版面", False)
                _atomic_json(os.path.join(run_dir, "now.json"),
                             dict(base, card_data={"heading": "還沒準備好：照下面的說明處理後，再雙擊一次", "items": items},
                                  beat={"text": "按右下角「⋯」→「停止示範」可以關掉這個畫面。", "i": 0, "n": 0}))
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
