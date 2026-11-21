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
  demo_runbook.py learn     <runbook.json> [--runs N]          # 由最近 N 次紀錄提出劇本修改（差異清單），不自動套用
  demo_runbook.py apply     <runbook.json> <proposal.json> --reason "<使用者同意的原話>"
  demo_runbook.py freeze|unfreeze <runbook.json> --reason "..."
  demo_runbook.py login     <runbook.json> --account <帳號> --secret-cmd "<印出密碼的指令>"
        只限政策檔登記為 agent_login 的示範帳號（policy.py domain add … --agent-login …）；由 neb 的 login 規則填入，
        密碼走 stdin（不進命令列參數）、只在記憶體裡，不輸出、不寫檔。
  demo_runbook.py reset     <runbook.json>                     # 執行還原指令：政策檔要有使用者對「這組指令雜湊」的同意

劇本裡的 preflight／reset 是會在本機執行的 shell 指令：validate 會全部列出；只跑自己寫的或看過的劇本。

所有對目標網站的操作都經過 neb；送出類動作帶 --runbook-approval/--step，neb 只放行與核准清單逐字相符的輸入。
政策 demo.persist=off → 一次性：紀錄寫在暫存資料夾、結束即刪，不更新 Plan B、不提出修改。
紀錄只含結構與數字（步驟、耗時、選取方式、斷言結果）；截圖留本機（可能含系統畫面內容，分享前自己看過）。
"""
import copy, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402

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
    os.replace(tmp, rb["_path"])


def rb_sha(rb):
    """劇本的「送出相關內容」雜湊：步驟的帳號與送出動作。等待預算、選取方式順序、講者台詞改了不影響核准。"""
    return hashlib.sha256(json.dumps(sends_of(rb), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


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
                        ent.update(pattern=pat, shown=shown)
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
    return {"kind": "no-errands-runbook-sends", "version": 1, "title": rb.get("title", ""),
            "target": rb["target"]["host"].lower(), "runbook_sha256": rb_sha(rb), "days": rb.get("approval_days", 7),
            "sends": s}


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
                                     "snapshot_ok", "expect_absent", "scroll"):
                errs.append("%s：未知動作 %s" % (st.get("id"), act.get("do")))
            for v in VAR_RE.findall(json.dumps({k: x for k, x in act.items() if k != "regex"}, ensure_ascii=False)):
                if not ((rb.get("vars") or {}).get(v) or {}).get("pattern"):
                    errs.append("%s：變數 {%s} 沒有在 vars 宣告 pattern" % (st.get("id"), v))
        if st.get("resume_at") is not None and not 0 <= int(st["resume_at"]) <= len(st.get("actions") or []):
            errs.append("%s：resume_at 超出範圍" % st.get("id"))
    return errs


def shell_commands(rb):
    """劇本裡會在本機執行的 shell 指令（preflight／reset）：validate 一律列出來給人看。"""
    return [{"where": "preflight", "name": c.get("name"), "cmd": c.get("cmd")} for c in rb.get("preflight") or []] + \
           [{"where": "reset", "cmd": c} for c in (rb.get("reset") or {}).get("commands") or []]


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

class Browser:
    def __init__(self, rb, headed, log):
        self.rb, self.headed, self.log = rb, headed, log
        self.started = set()

    def prof(self, acct):
        p = self.rb["accounts"][acct]["profile"]
        return p if os.path.isabs(p) else os.path.join(self.rb["_dir"], p)

    def sess(self, acct):
        return self.rb["accounts"][acct].get("session") or ("rb-" + acct)

    def neb(self, acct, *cmd, approval=None, step=None, timeout=90):
        g = ["--session", self.sess(acct), "--profile", self.prof(acct)]
        if self.headed:
            g.append("--headed")
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
        w, h = (self.rb.get("viewport") or [1400, 900])
        self.neb(acct, "open", "about:blank")
        self.neb(acct, "set", "viewport", str(w), str(h))
        self.started.add(acct)

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
                          "near": [i["role"] + ":" + i["name"][:30] for i in items
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


def subst(text, vars_):
    return VAR_RE.sub(lambda m: str(vars_.get(m.group(1), m.group(0))), text)


def run_action(br, rb, st, act, vars_, approval, rec):
    """回 (ok, info)。info 只含結構資訊，不含輸入文字。"""
    acct, d = st["account"], act["do"]
    t0 = time.time()
    info = {"do": d}
    if d == "open":
        rc, o, e = br.neb(acct, "open", rb["target"]["base_url"].rstrip("/") + subst(act["url"], vars_))
        ok = rc == 0
        if not ok:
            info["error"] = err_of(rc, o)
    elif d == "pause":
        time.sleep(float(act.get("s", 1)))
        ok = True
    elif d in ("fill", "click", "check"):
        ref, used = br.find(acct, act["target"])
        if not ref:
            ok, info["error"], info["tried"] = False, "找不到目標", used
        else:
            info["used"] = used
            cmd = [d, "@" + ref] + ([subst(act["text"], vars_)] if d == "fill" else [])
            rc, o, e = br.neb(acct, *cmd, approval=approval, step=st["id"])
            ok = rc == 0
            if not ok:
                info["error"] = err_of(rc, o)
    elif d == "press":
        rc, o, e = br.neb(acct, "press", act["key"], approval=approval, step=st["id"])
        ok = rc == 0
        if not ok:
            info["error"] = err_of(rc, o)
    elif d == "scroll":
        rc, o, e = br.neb(acct, "scroll", act.get("dir", "down"), str(act.get("px", 600)))
        ok = rc == 0
    elif d in ("wait_text", "expect_absent"):
        budget = float(act.get("budget_s", 30))
        scopes = act.get("scopes") or [None]
        texts = [subst(x, vars_) for x in ([act["text"]] if isinstance(act["text"], str) else act["text"])]
        ok, hit = False, None
        while True:
            for sc in scopes:
                body = br.text(acct, sc)
                found = [x for x in texts if x in body]
                if (d == "wait_text" and found) or (d == "expect_absent" and not found and body):
                    ok, hit = True, {"scope": sc, "text_index": texts.index(found[0]) if found else None}
                    break
            if ok or time.time() - t0 >= budget:
                break
            time.sleep(float(act.get("poll_s", 1.5)))
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
    info["ok"], info["ms"] = ok, int((time.time() - t0) * 1000)
    return ok, info


def run_step(br, rb, st, vars_, approval, shots_dir):
    acct = st["account"]
    br.ensure(acct)
    rec = {"id": st["id"], "account": acct, "start": now_s(), "actions": [], "recovered": False,
           "fallback_used": False}
    t0 = time.time()

    def attempt(actions, tag):
        for i, act in enumerate(actions):
            ok, info = run_action(br, rb, st, act, vars_, approval, rec)
            info["i"], info["phase"] = i, tag
            rec["actions"].append(info)
            if not ok and act["do"] in ("fill", "click", "check", "snapshot_ok", "capture"):
                time.sleep(2)                                   # 一次就地重試（畫面可能還在更新）
                ok, info2 = run_action(br, rb, st, act, vars_, approval, rec)
                info2["i"], info2["phase"], info2["retry"] = i, tag, True
                rec["actions"].append(info2)
                if ok:
                    rec["recovered"] = True
            if not ok and not act.get("optional"):
                return False, i
        return True, None

    ok, failed_at = attempt(st.get("actions") or [], "main")
    if not ok and st.get("fallback"):
        rec["fallback_used"] = True
        fok, _ = attempt(st["fallback"], "fallback")
        if fok:
            acts = st.get("actions") or []
            if st.get("resume_at") is not None:          # 退路已取代前面的動作：從指定位置接著跑
                rest = acts[int(st["resume_at"]):]
            else:
                rest = acts[failed_at:] if st.get("retry_from_failed", True) else acts
            ok, _ = attempt(rest, "after_fallback")
            rec["recovered"] = ok
    rec["ok"] = ok
    rec["dur_s"] = round(time.time() - t0, 1)
    if shots_dir:
        p = os.path.join(shots_dir, "%s.png" % st["id"])
        br.neb(acct, "screenshot", p)
        rec["shot"] = os.path.basename(p) if os.path.exists(p) else None
    return rec


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
    ok, why = check_approval(rb, aid)
    if not ok:
        out({"ok": False, "error": why, "next": "請使用者在發件匣核准劇本（demo_runbook.py request-approval）"}, 3)
    resume = arg(a, "--resume")
    if resume:
        run_dir = resume
        state = json.load(open(os.path.join(run_dir, "run.json"), encoding="utf-8"))
    else:
        root = runs_root(rb, persist)
        run_dir = os.path.join(root, time.strftime("%Y%m%d-%H%M%S-") + mode)
        os.makedirs(run_dir)
        state = {"mode": mode, "runbook_sha256": rb_sha(rb), "approval": aid, "started": now_s(),
                 "persist": persist, "vars": {}, "steps": [], "next": 0}
    shots = os.path.join(run_dir, "shots")
    os.makedirs(shots, exist_ok=True)
    steps = rb["steps"]
    ids = [s["id"] for s in steps]
    lo = ids.index(arg(a, "--from")) if arg(a, "--from") in ids else 0
    hi = ids.index(arg(a, "--to")) + 1 if arg(a, "--to") in ids else len(steps)
    i = max(state["next"], lo)
    br = Browser(rb, headed=(mode == "live"), log=None)
    stopped = None
    try:
        while i < hi:
            st = steps[i]
            if mode == "live" and st.get("advance") == "cue" and state.get("cued") != st["id"]:
                state.update(next=i, cued=st["id"])
                stopped = {"waiting_cue": st["id"], "say": st.get("say", "")}
                break
            print(json.dumps({"step": st["id"], "say": st.get("say", "")}, ensure_ascii=False), flush=True)
            rec = run_step(br, rb, st, state["vars"], aid, shots)
            state["steps"].append(rec)
            i += 1
            state["next"] = i
            if not rec["ok"]:
                planb = os.path.join(rb["_dir"], "planb", "%s.png" % st["id"])
                stopped = {"failed": st["id"], "planb": planb if os.path.exists(planb) else None,
                           "fallback_text": st.get("on_fail", "")}
                if mode == "rehearse" and "--continue" in a:
                    stopped = None
                    continue
                break
            if mode == "rehearse" and persist and rec.get("shot"):
                os.makedirs(os.path.join(rb["_dir"], "planb"), exist_ok=True)
                shutil.copyfile(os.path.join(shots, rec["shot"]), os.path.join(rb["_dir"], "planb", rec["shot"]))
    finally:
        if mode != "live" or not stopped or "failed" in (stopped or {}):
            br.close_all()
        state["ended"] = now_s()
        state["stopped"] = stopped
        state["vars_seen"] = sorted(state["vars"])           # 變數名；值（例如案件編號）也留在本機紀錄
        with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    summary = {"ok": stopped is None or "waiting_cue" in stopped, "mode": mode, "run_dir": run_dir if persist else None,
               "steps_run": len(state["steps"]), "steps_ok": sum(1 for s in state["steps"] if s["ok"]),
               "recovered": [s["id"] for s in state["steps"] if s.get("recovered")],
               "failed": [s["id"] for s in state["steps"] if not s["ok"]],
               "durations": {s["id"]: s["dur_s"] for s in state["steps"]}, "stopped": stopped}
    if not persist:
        shutil.rmtree(run_dir, ignore_errors=True)
        summary["note"] = "demo.persist=off：一次性執行，紀錄已刪除"
    if stopped and "waiting_cue" in stopped:
        summary["next"] = "等講者示意；之後執行：demo_runbook.py live <runbook> --approval %s --resume %s" % (aid, run_dir)
    out(summary, 0 if summary["ok"] else 1)


# ---------------------------------------------------------------- preflight

def preflight(rb, a):
    res = []

    def add(name, ok, detail=""):
        res.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})

    for chk in rb.get("preflight") or []:
        try:
            p = subprocess.run(chk["cmd"], shell=True, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=chk.get("timeout_s", 60))
            o = p.stdout.strip()
            ok = p.returncode == 0 and (not chk.get("expect") or re.search(chk["expect"], o, re.M))
            add(chk["name"], ok, o.splitlines()[-1] if o else p.stderr.strip()[-150:])
        except Exception as e:
            add(chk["name"], False, e)
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
            add("登入有效且身分正確：" + acct, ok, ("登入的是 %s" % who) if me else "（劇本沒設 whoami_path，只檢查不在登入頁）")
    finally:
        br.close_all()
    aid = arg(a, "--approval")
    if aid:
        ok, why = check_approval(rb, aid)
        add("劇本核准有效且內容相符", ok, "" if ok else why)
    allok = all(r["ok"] for r in res)
    out({"ok": allok, "checks": res}, 0 if allok else 1)


# ---------------------------------------------------------------- learn / apply

def iter_targets(rb):
    for si, st in enumerate(rb.get("steps", [])):
        for phase in ("actions", "fallback"):
            for ai, act in enumerate(st.get(phase) or []):
                yield si, st, phase, ai, act


def learn(rb, a):
    pol = policy.load()
    dm = pol.get("demo") or {}
    if dm.get("persist", "on") != "on" or dm.get("learn", "propose") != "propose":
        out({"ok": True, "proposal": None, "note": "政策 demo.persist/learn 關閉：不提出修改"})
    root = runs_dir(rb)
    runs = sorted(d for d in os.listdir(root) if os.path.exists(os.path.join(root, d, "run.json"))) if os.path.isdir(root) else []
    runs = runs[-int(arg(a, "--runs", "5")):]
    if not runs:
        out({"ok": False, "error": "還沒有紀錄"}, 1)
    obs = {}   # (step, phase, i) -> {"ms": [...], "used": [...], "fail": n}
    issues = []
    for r in runs:
        st_ = json.load(open(os.path.join(root, r, "run.json"), encoding="utf-8"))
        for s in st_["steps"]:
            for act in s["actions"]:
                k = (s["id"], "actions" if act["phase"] in ("main", "after_fallback") else "fallback", act["i"])
                o = obs.setdefault(k, {"ms": [], "used": [], "fail": 0, "ok": 0})
                if act["ok"]:
                    o["ok"] += 1
                    o["ms"].append(act["ms"])
                    if act.get("used"):
                        o["used"].append(act["used"])
                else:
                    o["fail"] += 1
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
            if o["fail"] or abs(new - cur) / cur > 0.25:
                if o["fail"]:
                    new = int(max(new, cur * 1.5))
                changes.append({"path": base + ".budget_s", "old": cur, "new": new,
                                "why": "觀測 %d 次成功，最長 %.1f 秒；失敗 %d 次" % (len(o["ms"]), hi, o["fail"]),
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
    prop = {"runbook": rb["_path"], "runbook_sha256": rb_sha(rb), "made": now_s(), "from_runs": runs,
            "changes": changes, "known_issues": known, "frozen": bool(rb.get("frozen"))}
    pdir = os.path.join(rb["_dir"], "proposals")
    os.makedirs(pdir, exist_ok=True)
    pp = os.path.join(pdir, time.strftime("%Y%m%d-%H%M%S") + ".json")
    json.dump(prop, open(pp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    diff = ["%s: %s → %s（%s）" % (c["path"], json.dumps(c["old"], ensure_ascii=False),
                                   json.dumps(c["new"], ensure_ascii=False), c["why"]) for c in changes]
    out({"ok": True, "proposal": pp, "n_changes": len(changes), "diff": diff, "known_issues": len(known),
         "frozen": prop["frozen"],
         "next": "把差異清單給使用者看；他同意後才執行 demo_runbook.py apply <runbook> <proposal> --reason \"…\""})


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
    before = sends_sha(sends_of(rb))
    for c in prop["changes"]:
        set_path(rb, c["path"], c["new"])
    ki = rb.setdefault("known_issues", [])
    for k in prop.get("known_issues") or []:
        if k not in ki:
            ki.append(k)
    rb.setdefault("history", []).append({"ts": now_s(), "applied": os.path.basename(a[2]),
                                         "n_changes": len(prop["changes"]), "reason": reason})
    save_rb(rb)
    after = sends_sha(sends_of(rb))
    out({"ok": True, "applied": len(prop["changes"]), "needs_reapproval": before != after})


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
    pw = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
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


def reset(rb, a):
    r = rb.get("reset") or {}
    consent = ((policy.load().get("demo") or {}).get("reset_consent") or {})
    if consent.get("commands_sha256") != reset_sha(rb):
        out({"ok": False, "error": "政策檔沒有使用者對「這組」還原指令的同意（指令改過就要重新同意）",
             "commands": r.get("commands"), "commands_sha256": reset_sha(rb),
             "next": "把指令給使用者看；他同意後：policy.py set demo.reset_consent <sha256> --reason \"<原話>\""}, 3)
    res = []
    for c in r.get("commands") or []:
        p = subprocess.run(c, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300)
        res.append({"rc": p.returncode, "tail": (p.stdout or p.stderr).strip().splitlines()[-1:] })
    ok = all(x["rc"] == 0 for x in res)
    out({"ok": ok, "results": res}, 0 if ok else 1)


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(2)
    sub = a[0]
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
        doc = sends_doc(rb)
        p = os.path.join(rb["_dir"], "sends.json")
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        r = subprocess.run([sys.executable, OUTBOX, "draft", "--service", "runbook", "--to", doc["target"],
                            "--body-file", p, "--why", rb.get("approval_why", "示範劇本：整份一次核准")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, NO_ERRANDS_HOME=policy.HOME))
        sys.stdout.write(r.stdout)
        sys.exit(r.returncode)
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
    out({"ok": False, "error": "未知子指令"}, 2)


if __name__ == "__main__":
    main()
