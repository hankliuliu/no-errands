#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent-browser 小幫手：像 agent 一樣用 snapshot 找元素（角色＋名稱），不寫死 CSS selector。"""
import re, subprocess, time

LINE = re.compile(r'^\s*-\s*(\w+)\s+"([^"]*)".*?\bref=(e\d+)')


class AB:
    def __init__(self, ab, session, profile=None):
        self.ab, self.base = ab, ["--session", session] + (["--profile", profile.replace("\\", "/")] if profile else [])
        self.log = []

    def run(self, *a, timeout=90):
        p = subprocess.run([self.ab] + self.base + list(a), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        self.log.append("%s rc=%d" % (a[0], p.returncode))
        return p

    def snapshot(self):
        out = self.run("snapshot", "-i").stdout
        items = []
        for line in out.splitlines():
            m = LINE.match(line)
            if m:
                items.append({"role": m.group(1), "name": m.group(2), "ref": "@" + m.group(3)})
        return items

    def find(self, role_re, name_re, tries=6):
        for _ in range(tries):
            for it in self.snapshot():
                if re.search(role_re, it["role"], re.I) and re.search(name_re, it["name"], re.I):
                    return it
            time.sleep(1.0)
        return None

    def fill(self, role_re, name_re, text):
        it = self.find(role_re, name_re)
        if not it:
            self.dump()
            raise RuntimeError("找不到欄位 %s / %s" % (role_re, name_re))
        self.run("fill", it["ref"], text)
        return it

    def click(self, role_re, name_re):
        it = self.find(role_re, name_re)
        if not it:
            self.dump()
            raise RuntimeError("找不到按鈕 %s / %s" % (role_re, name_re))
        self.run("click", it["ref"])
        self.run("wait", "--load", "networkidle")
        return it

    def dump(self):
        """找不到元素時印出這一頁有哪些互動元素（測試頁的 UI 字串）。"""
        print("page:", self.url())
        for it in self.snapshot()[:30]:
            print("   ", it["role"], repr(it["name"])[:60])

    def check(self, role_re, name_re):
        it = self.find(role_re, name_re, tries=2)
        if it:
            self.run("check", it["ref"])
        return it

    def text(self, selector="body"):
        return self.run("get", "text", selector).stdout

    def url(self):
        return (self.run("get", "url").stdout.strip().splitlines() or [""])[-1].strip()

    def close(self):
        self.run("close")
