# -*- coding: utf-8 -*-
"""Build docs/Supplier_Comparison_Agent_Proposal.pdf from docs/PROPOSAL.md.

Renders the Markdown locally with the ``markdown`` package (no ``gh`` needed),
styles it for A4 like the write-up, and prints it with headless Google Chrome.

Usage:  pip install markdown && python3 scripts/build_proposal_pdf.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "PROPOSAL.md"
OUT = ROOT / "docs" / "Supplier_Comparison_Agent_Proposal.pdf"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]

CSS = """
@page { size: A4; margin: 15mm 15mm 17mm 15mm; }
body { font-family: -apple-system, "Helvetica Neue", "PingFang SC", Arial, sans-serif;
       font-size: 9.8pt; line-height: 1.45; color: #1a1a1a; }
h1 { font-size: 19pt; margin: 0 0 4pt; color: #0b3d5c; }
h1 + p { color: #555; font-size: 8.8pt; margin-top: 0; }
h2 { font-size: 13.5pt; color: #0b3d5c; border-bottom: 1.5px solid #0b3d5c;
     padding-bottom: 2pt; margin-top: 15pt; break-after: avoid; }
h3 { font-size: 11pt; margin-bottom: 4pt; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 10pt 0; }
code { font-family: Menlo, "SF Mono", monospace; font-size: 8.5pt;
       background: #f2f4f6; padding: 0 2px; border-radius: 2px; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 8.8pt;
        break-inside: auto; }
tr { break-inside: avoid; }
th, td { border: 1px solid #d0d7de; padding: 3.5pt 6pt; vertical-align: top; text-align: left; }
th { background: #eef3f7; }
a { color: #0b3d5c; }
"""


def main() -> int:
    chrome = next((c for c in CHROME_CANDIDATES if c and pathlib.Path(c).exists()), None)
    if not chrome:
        print("Google Chrome not found.", file=sys.stderr)
        return 1
    body = markdown.markdown(SRC.read_text(encoding="utf-8"), extensions=["tables", "sane_lists"])
    html = f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>'
    with tempfile.TemporaryDirectory() as tmp:
        page = pathlib.Path(tmp) / "proposal.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={OUT}", page.as_uri()],
                       check=True, capture_output=True)
    print(f"Wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
