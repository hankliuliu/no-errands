#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""office.py 測試：產生「公式沒有快取值」的 xlsx → 重算 → 確認值出現；docx → pdf；
有 LibreOffice 時另做 txt → 舊版 .doc → pdf。不開視窗（COM Visible=False / soffice --headless）。
沒有任何後端時印 SKIP（不算失敗）。"""
import json, os, re, shutil, subprocess, sys, tempfile, zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OF = os.path.join(REPO, "skills", "no-errands", "scripts", "office.py")
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


def make_xlsx(p):
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        # A3 = A1+A2 with NO cached <v> (what context-gather hits when a generator writes formulas)
        "xl/worksheets/sheet1.xml": '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>2</v></c></row><row r="2"><c r="A2"><v>3</v></c></row><row r="3"><c r="A3"><f>A1+A2</f></c></row></sheetData></worksheet>',
    }
    with zipfile.ZipFile(p, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)


def make_docx(p):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>no-errands office test</w:t></w:r></w:p></w:body></w:document>')


def run(*a):
    p = subprocess.run([sys.executable, OF] + list(a), capture_output=True, text=True, encoding="utf-8", timeout=400)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "raw": p.stdout[-200:], "err": p.stderr[-200:]}


def main():
    be = run("backends")
    print("backends:", be)
    if not be.get("ms_office_com") and not be.get("libreoffice"):
        print("SKIP: no Office backend"); sys.exit(0)
    td = tempfile.mkdtemp(prefix="ne-office-")
    try:
        x = os.path.join(td, "nocache.xlsx"); make_xlsx(x)
        o = run("recalc", x, "--out", os.path.join(td, "out.xlsx"))
        check("recalc 成功（%s）" % o.get("backend"), o.get("ok"), o)
        if o.get("ok"):
            with zipfile.ZipFile(o["out"]) as z:
                sheet = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")][0]
                xml = z.read(sheet).decode("utf-8")
            m = re.search(r'<c r="A3"[^>]*>.*?<v>([^<]+)</v>', xml, re.S)
            check("重算後 A3 有值 5", m and float(m.group(1)) == 5.0, xml[:300])
        d = os.path.join(td, "t.docx"); make_docx(d)
        o = run("convert", d, "--to", "pdf", "--outdir", td)
        check("docx → pdf（%s）" % o.get("backend"), o.get("ok") and open(o["out"], "rb").read(4) == b"%PDF", o)
        if be.get("libreoffice") and not be.get("ms_office_com"):
            t = os.path.join(td, "legacy.txt"); open(t, "w").write("legacy doc test\n")
            subprocess.run([be["libreoffice"], "--headless", "--convert-to", "doc", t, "--outdir", td], capture_output=True, timeout=300)
            doc = os.path.join(td, "legacy.doc")
            if os.path.exists(doc):
                o = run("convert", doc, "--to", "pdf", "--outdir", td)
                check("舊版 .doc → pdf（libreoffice）", o.get("ok"), o)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
