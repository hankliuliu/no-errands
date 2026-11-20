#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 摘要：把 doctor.json 的各項檢查印成 markdown 清單（給 $GITHUB_STEP_SUMMARY）。"""
import json, os, sys

print("## %s" % os.environ.get("NE_OS", "?"))
for name in ("doctor", "outbox", "b3", "n4"):
    print("- %s: %s" % (name, os.environ.get("NE_" + name.upper(), "n/a") or "n/a"))
if os.path.exists("doctor.json"):
    try:
        d = json.load(open("doctor.json", encoding="utf-8"))
        for k, v in d.get("checks", {}).items():
            err = (v.get("error") or "").replace("\n", " ")[:160]
            print("  - %s %s %s" % ("OK" if v.get("ok") else "FAIL", k, err))
    except Exception as e:
        print("  - doctor.json unreadable: %s" % e)
sys.exit(0)
