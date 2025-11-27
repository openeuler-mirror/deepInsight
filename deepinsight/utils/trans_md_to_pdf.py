# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

"""
Minimal Markdown -> PDF conversion helper.

Tries multiple backends:
- weasyprint (markdown to HTML + render)
- pdfkit (requires wkhtmltopdf)
- reportlab (plain text fallback)

Notes on garbled text (CJK/Unicode):
- Ensure UTF-8 is declared and a CJK-capable font is used.
- We embed a safe default font stack via CSS for WeasyPrint/pdfkit paths.
- If falling back to reportlab, plain text may not render CJK unless a
  suitable TTF is registered; we avoid complex font setup here and prefer
  WeasyPrint.

If none are available, raises RuntimeError.
"""

import os
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# Default CSS injected into HTML to improve CJK rendering and general layout
_DEFAULT_CSS = """
@page { size: A4; margin: 20mm; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei",
               "Noto Sans CJK SC", "WenQuanYi Zen Hei",
               "SimHei", sans-serif;
  line-height: 1.6;
  font-size: 12pt;
  color: #111;
}
h1, h2, h3, h4 { color: #111; }
code, pre, kbd, samp {
  font-family: SFMono-Regular, Menlo, Monaco, Consolas,
               "Liberation Mono", "Courier New", monospace;
  font-size: 10pt;
}
table { border-collapse: collapse; }
table, th, td { border: 1px solid #ddd; }
th, td { padding: 6px 8px; }
img { max-width: 100%; }
"""


def _markdown_to_html(md: str) -> str:
    try:
        import markdown as mdlib  # python-markdown
        html = mdlib.markdown(md, extensions=["extra", "codehilite", "tables"])
        return (
            "<html><head>"
            "<meta charset='utf-8'>"
            f"<style>{_DEFAULT_CSS}</style>"
            "</head><body>"
            f"{html}"
            "</body></html>"
        )
    except Exception:
        # Very naive fallback: wrap as <pre>
        safe = (md or "").replace("<", "&lt;").replace(">", "&gt;")
        return (
            "<html><head>"
            "<meta charset='utf-8'>"
            f"<style>{_DEFAULT_CSS}</style>"
            "</head><body><pre>"
            f"{safe}"
            "</pre></body></html>"
        )


def save_markdown_as_pdf(markdown_content: str, output_filename: str, *, base_url: Optional[str] = None) -> None:
    os.makedirs(os.path.dirname(output_filename), exist_ok=True)

    # Try weasyprint
    try:
        from weasyprint import HTML, CSS  # type: ignore
        html = _markdown_to_html(markdown_content)
        HTML(string=html, base_url=base_url).write_pdf(
            output_filename,
            stylesheets=[CSS(string=_DEFAULT_CSS)],
        )
        logger.info("PDF generated using WeasyPrint")
        return
    except Exception as e:
        logger.error(f"WeasyPrint failed; trying pdfkit... error: {e}")

    # Try pdfkit
    try:
        import pdfkit  # type: ignore
        html = _markdown_to_html(markdown_content)
        options = {
            "encoding": "UTF-8",
            # Allow relative images when base_url is used externally
            # Users may also configure wkhtmltopdf path via pdfkit configuration
        }
        pdfkit.from_string(html, output_filename, options=options)  # requires wkhtmltopdf
        logger.info("PDF generated using pdfkit/wkhtmltopdf")
        return
    except Exception as e:
        logger.error(f"pdfkit failed; trying reportlab fallback... error: {e}") 

    # Fallback: reportlab (plain text dump)
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(output_filename, pagesize=letter)
        width, height = letter
        # naive text writer: wrap lines
        y = height - 40
        for line in (markdown_content or "").splitlines():
            c.drawString(40, y, line[:1000])
            y -= 14
            if y < 40:
                c.showPage()
                y = height - 40
        c.save()
        logger.info("PDF generated using reportlab fallback (plain text)")
        return
    except Exception as e:
        logger.error(f"reportlab fallback failed; no PDF backend available error: {e}")

    raise RuntimeError("No PDF backend available. Please install 'weasyprint' or 'pdfkit' or 'reportlab'.")