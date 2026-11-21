#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Office 檔案處理（不開視窗；純 stdlib + 本機 Office 或 LibreOffice）。

  office.py recalc  <in.xlsx> [--out <out.xlsx>]        # 公式重算後存檔（有快取值的 xlsx）
  office.py convert <in.ppt|.pptx|.doc|.docx|.xls|.xlsx> --to pdf [--outdir <dir>]
  office.py backends                                    # 列出這台機器可用的後端

後端：Windows 有 Microsoft Office → COM（Visible=False、DisplayAlerts=False；只結束自己開的那個程序）；
否則 LibreOffice `soffice --headless`（三平台）。都沒有 → 回錯誤與安裝建議。
"""
import json, os, shutil, subprocess, sys, tempfile

COM_PS = r"""
param([string]$Kind, [string]$In, [string]$Out)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class NeW { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p); }'
function Kill-Own($hwnd) { if ($hwnd) { [uint32]$p = 0; [void][NeW]::GetWindowThreadProcessId([IntPtr]$hwnd, [ref]$p); if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue } } }
try {
  if ($Kind -eq 'recalc' -or $Kind -eq 'xls2pdf') {
    $app = New-Object -ComObject Excel.Application
    $app.Visible = $false; $app.DisplayAlerts = $false; $app.ScreenUpdating = $false
    $hwnd = $app.Hwnd
    $wb = $app.Workbooks.Open($In, 0, $false)
    $app.CalculateFull()
    if ($Kind -eq 'recalc') { $wb.SaveAs($Out, 51) } else { $wb.ExportAsFixedFormat(0, $Out) }
    $wb.Close($false); $app.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    Start-Sleep -Milliseconds 800; Kill-Own $hwnd
  } elseif ($Kind -eq 'doc2pdf') {
    $app = New-Object -ComObject Word.Application
    $app.Visible = $false; $app.DisplayAlerts = 0
    $doc = $app.Documents.Open($In, $false, $true, $false)
    $doc.ExportAsFixedFormat($Out, 17)
    $doc.Close(0); $app.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
  } elseif ($Kind -eq 'ppt2pdf') {
    $app = New-Object -ComObject PowerPoint.Application
    $pres = $app.Presentations.Open($In, -1, 0, 0)   # ReadOnly, Untitled=false, WithWindow=false
    $pres.SaveAs($Out, 32)
    $pres.Close(); $app.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
  }
  ConvertTo-Json -Compress @{ ok = $true; out = $Out }
} catch {
  ConvertTo-Json -Compress @{ ok = $false; error = $_.Exception.Message }
}
"""


def has_office():
    if os.name != "nt":
        return False
    try:
        import winreg
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CLSID").Close()
        return True
    except Exception:
        return False


def soffice():
    for c in ("soffice", "libreoffice", r"C:\Program Files\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        p = shutil.which(c) or (c if os.path.exists(c) else None)
        if p:
            return p
    return None


def com(kind, src, out):
    with tempfile.TemporaryDirectory() as td:
        ps1 = os.path.join(td, "office.ps1")
        open(ps1, "w", encoding="ascii").write(COM_PS)
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, kind,
                            os.path.abspath(src), os.path.abspath(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": (p.stderr or p.stdout)[-300:]}


def lo(args, outdir):
    so = soffice()
    prof = tempfile.mkdtemp(prefix="ne-lo-")   # 獨立 profile：不碰使用者的 LibreOffice 設定、不和開著的實例衝突
    try:
        p = subprocess.run([so, "-env:UserInstallation=file:///" + prof.replace("\\", "/").lstrip("/"),
                            "--headless", "--norestore"] + args + ["--outdir", outdir],
                           capture_output=True, text=True, timeout=300)
        return p.returncode == 0, (p.stderr or p.stdout)[-300:]
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def out(o, code=0):
    print(json.dumps(o, ensure_ascii=False)); sys.exit(code)


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    if a[0] == "backends":
        out({"ms_office_com": has_office(), "libreoffice": soffice()})
    arg = lambda k, d=None: a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else d
    src = a[1] if len(a) > 1 else None
    if not src or not os.path.exists(src):
        out({"ok": False, "error": "找不到輸入檔"}, 2)
    ext = os.path.splitext(src)[1].lower()
    if a[0] == "recalc":
        dst = arg("--out") or os.path.splitext(src)[0] + ".recalc.xlsx"
        if has_office():
            r = com("recalc", src, dst); r["backend"] = "excel-com"; out(r, 0 if r.get("ok") else 1)
        if soffice():
            td = tempfile.mkdtemp()
            ok, msg = lo(["--convert-to", "xlsx:Calc MS Excel 2007 XML", os.path.abspath(src)], td)
            produced = os.path.join(td, os.path.splitext(os.path.basename(src))[0] + ".xlsx")
            if ok and os.path.exists(produced):
                shutil.move(produced, dst)
                out({"ok": True, "out": dst, "backend": "libreoffice"})
            out({"ok": False, "error": msg, "backend": "libreoffice"}, 1)
        out({"ok": False, "error": "沒有 Microsoft Office 也沒有 LibreOffice", "recovery": "安裝 LibreOffice（使用者層級即可）"}, 1)
    if a[0] == "convert":
        if arg("--to", "pdf") != "pdf":
            out({"ok": False, "error": "目前只支援 --to pdf"}, 2)
        outdir = os.path.abspath(arg("--outdir") or os.path.dirname(os.path.abspath(src)))
        os.makedirs(outdir, exist_ok=True)
        dst = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + ".pdf")
        kind = {".ppt": "ppt2pdf", ".pptx": "ppt2pdf", ".doc": "doc2pdf", ".docx": "doc2pdf",
                ".xls": "xls2pdf", ".xlsx": "xls2pdf"}.get(ext)
        if not kind:
            out({"ok": False, "error": "不支援的副檔名 %s" % ext}, 2)
        if has_office():
            r = com(kind, src, dst); r["backend"] = "office-com"; out(r, 0 if r.get("ok") else 1)
        if soffice():
            ok, msg = lo(["--convert-to", "pdf", os.path.abspath(src)], outdir)
            out({"ok": ok and os.path.exists(dst), "out": dst, "backend": "libreoffice", "log": None if ok else msg},
                0 if ok and os.path.exists(dst) else 1)
        out({"ok": False, "error": "沒有 Microsoft Office 也沒有 LibreOffice"}, 1)
    out({"ok": False, "error": "未知子指令"}, 2)


if __name__ == "__main__":
    main()
