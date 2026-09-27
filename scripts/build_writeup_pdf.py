# -*- coding: utf-8 -*-
"""Build docs/Supplier_Comparison_Agent_Writeup.pdf from docs/WRITEUP.md.

Renders the Markdown with GitHub's own renderer (``gh api markdown``, so the
PDF matches the repo view), styles it for A4, and prints it with headless
Google Chrome. Needs the ``gh`` CLI (logged in) and Chrome.

Usage:  python3 scripts/build_writeup_pdf.py
"""
import os, pathlib, re, shutil, subprocess, sys, tempfile
import json
ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "WRITEUP.md"
OUT = ROOT / "docs" / "Supplier_Comparison_Agent_Writeup.pdf"
src = SRC.read_text(encoding="utf-8")
# Keep the subtitle's two lines separate.
src = src.replace("Problem: *Supplier Comparison*\n", "Problem: *Supplier Comparison*<br>\n", 1)
# Render with GitHub's own GFM renderer so the PDF matches the repo view.
body = subprocess.run(
    ["gh", "api", "markdown", "--input", "-"],
    input=json.dumps({"text": src, "mode": "markdown"}),
    capture_output=True, text=True, check=True).stdout
# Relative image paths (e.g. screenshots/x.png) are relative to docs/; the HTML
# is printed from a temp dir, so point them at the files on disk.
docs_uri = SRC.parent.as_uri() + "/"
body = re.sub(r'(src|href)="(?!https?:|file:|#|mailto:)([^"]+\.(?:png|jpe?g|gif|svg))"',
              lambda m: f'{m.group(1)}="{docs_uri}{m.group(2)}"', body)
css = """
@page { size: A4; margin: 16mm 15mm 18mm 15mm; }
body { font-family: -apple-system, "Helvetica Neue", "PingFang SC", Arial, sans-serif;
       font-size: 10pt; line-height: 1.45; color: #1a1a1a; }
h1 { font-size: 18pt; margin: 0 0 4pt; color: #0b3d5c; }
h1 + p { color: #555; font-size: 9pt; margin-top: 0; }
h2 { font-size: 13.5pt; color: #0b3d5c; border-bottom: 1.5px solid #0b3d5c;
     padding-bottom: 2pt; margin-top: 16pt; break-after: avoid; }
h3 { font-size: 11pt; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
code { font-family: Menlo, "SF Mono", "PingFang SC", monospace; font-size: 8.6pt;
       background: #f2f4f6; padding: 0 2px; border-radius: 2px; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 4px;
      padding: 7pt 8pt; overflow: hidden; break-inside: avoid; font-size: 6.7pt; line-height: 1.18; }
pre code { background: none; padding: 0; font-size: 6.6pt; line-height: 1.16; white-space: pre; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 8.8pt;
        break-inside: auto; }
th, td { border: 1px solid #d0d7de; padding: 3pt 5pt; text-align: left; vertical-align: top; }
th { background: #eef3f7; }
tr { break-inside: avoid; }
li > p { margin: 2pt 0; }
ul, ol { margin: 4pt 0 8pt; }
.markdown-heading a.anchor, .anchor { display: none; }
img { max-width: 88%; display: block; margin-left: auto; margin-right: auto; border: 1px solid #d0d7de; border-radius: 4px; margin: 6pt 0; }
hr { border: none; border-top: 1px solid #ccc; margin: 8pt 0; }
a { color: #0b5c8a; text-decoration: none; }
strong { color: #111; }
"""
html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Supplier Comparison Agent — Write-up</title><style>{css}</style></head>
<body>{body}</body></html>"""

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]
chrome = next((c for c in CHROME_CANDIDATES if c and os.path.exists(c)), None)
if not chrome:
    sys.exit("Google Chrome / Chromium not found")
with tempfile.TemporaryDirectory() as tmp:
    page = pathlib.Path(tmp) / "writeup.html"
    page.write_text(html, encoding="utf-8")
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={OUT}", page.as_uri()],
                   check=True, capture_output=True)
print(f"wrote {OUT.relative_to(ROOT)}")

