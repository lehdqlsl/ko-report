"""빌드한 보고서 HTML → A4 PDF (Chromium 인쇄). 꼬리말에 제목과 쪽 번호.

사용: uv run --with playwright python pdf.py 보고서.html [-o 보고서.pdf] [--pngs]
  --pngs: 쪽마다 PNG 를 만들어 눈으로 확인한다(pypdfium2 필요: uv run --with playwright --with pypdfium2 ...)
"""
from __future__ import annotations

import argparse
import asyncio
import html
from pathlib import Path

from playwright.async_api import async_playwright

FOOTER = ('<div style="width:100%;font-size:8px;color:#5A6472;padding:0 15mm;display:flex;justify-content:space-between;'
          'font-family:\'Apple SD Gothic Neo\',\'Malgun Gothic\',sans-serif">'
          '<span>{title}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


async def run(src: Path, out: Path) -> int:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        await pg.goto(src.resolve().as_uri(), wait_until="networkidle")
        await pg.emulate_media(media="print", color_scheme="light")
        title = await pg.title()
        await pg.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True,
                     display_header_footer=True, header_template="<span></span>",
                     footer_template=FOOTER.format(title=html.escape(title)))
        await b.close()
    try:
        from pypdf import PdfReader  # noqa: PLC0415
        return len(PdfReader(str(out)).pages)
    except ImportError:
        return -1


def pngs(pdf: Path) -> None:
    import pypdfium2 as pdfium  # noqa: PLC0415
    for old in pdf.parent.glob(f"{pdf.stem}_p*.png"):
        old.unlink()
    doc = pdfium.PdfDocument(str(pdf))
    for i in range(len(doc)):
        img = doc[i].render(scale=1.4).to_pil()
        path = pdf.with_name(f"{pdf.stem}_p{i + 1}.png")
        img.save(path)
        print(f"쪽 이미지 {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    ap.add_argument("--pngs", action="store_true")
    a = ap.parse_args()
    src = Path(a.src)
    out = Path(a.out) if a.out else src.with_suffix(".pdf")
    n = asyncio.run(run(src, out))
    print(f"OK {out}" + (f" ({n}쪽)" if n > 0 else ""))
    if a.pngs:
        pngs(out)


if __name__ == "__main__":
    main()
