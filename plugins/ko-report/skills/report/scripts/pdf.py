"""빌드한 보고서 HTML → A4 PDF (Chromium 인쇄). 꼬리말에 제목과 쪽 번호. 만든 뒤 쪽 품질을 잰다.

사용: uv run --with playwright --with pypdf --with pypdfium2 python pdf.py 보고서.html [-o 보고서.pdf] [--pngs] [--pages-dir 폴더] [--strict]
  --pngs:      쪽마다 PNG 를 <이름>_pages/ 에 만든다(눈으로 확인용). --pages-dir 로 폴더를 바꾼다.
  --strict:    쪽 품질 경고가 있으면 exit 1.
쪽 품질(WARN): 마지막이 아닌 쪽이 60% 미만으로 참(큰 빈자리 = 쪽 나눔 문제), 마지막 쪽이 12% 미만(거의 빈 쪽),
              쪽 수가 종류별 예산을 넘음(spec 은 그림 수에 따라 늘어남). pypdfium2·Pillow 가 없으면 쪽 수만 본다.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import math
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

LOAD_ALL = """async () => {
  // 그림은 loading="lazy" 라 화면 밖 것은 아직 안 불렸다. 모두 즉시 불러 다 그린 뒤 찍는다.
  const imgs = [...document.images];
  imgs.forEach(i => { i.loading = 'eager'; });
  await Promise.all(imgs.map(i => (i.complete && i.naturalWidth) ? null
    : new Promise(r => { i.addEventListener('load', r, {once: true}); i.addEventListener('error', r, {once: true}); })));
  await Promise.all(imgs.map(i => i.decode ? i.decode().catch(() => null) : null));
  return imgs.filter(i => !i.naturalWidth).length;
}"""

FOOTER = ('<div style="width:100%;font-size:8px;color:#5A6472;padding:0 14mm;display:flex;justify-content:space-between;'
          'font-family:\'Apple SD Gothic Neo\',\'Malgun Gothic\',sans-serif">'
          '<span>{title}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')
# 종류별 쪽 예산(넘으면 WARN). check.py 의 TYPE_LIMITS 와 짝이다.
PAGE_BUDGET = {"progress": 3, "completion": 3, "incident": 3, "proposal": 5, "analysis": 5, "qa": 6,
               "policy": 12, "spec": 12}
MARGIN_MM = (14, 16)          # report.css @page 위·아래 여백
A4_MM = (210, 297)
FILL_MIN, LAST_MIN = 0.60, 0.12


async def run(src: Path, out: Path) -> tuple[int, str, int]:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        await pg.goto(src.resolve().as_uri(), wait_until="networkidle")
        await pg.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        await pg.evaluate(LOAD_ALL)
        await pg.emulate_media(media="print", color_scheme="light")
        title = await pg.title()
        rtype = await pg.evaluate("(document.querySelector('meta[name=ko-report-type]') || {}).content || ''")
        shots = await pg.evaluate("document.querySelectorAll('main figure img').length")
        await pg.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True,
                     display_header_footer=True, header_template="<span></span>",
                     footer_template=FOOTER.format(title=html.escape(title)))
        await b.close()
    try:
        from pypdf import PdfReader  # noqa: PLC0415
        return len(PdfReader(str(out)).pages), rtype, shots
    except ImportError:
        return -1, rtype, shots


def page_images(pdf: Path, scale: float):
    import pypdfium2 as pdfium  # noqa: PLC0415
    doc = pdfium.PdfDocument(str(pdf))
    for i in range(len(doc)):
        yield i, doc[i].render(scale=scale).to_pil()


def fill_ratio(img) -> float:
    """본문 영역(위·아래 여백 제외)에서 마지막으로 글자·선이 있는 높이의 비율."""
    g = img.convert("L")
    w, h = g.size
    top = round(h * MARGIN_MM[0] / A4_MM[1])
    bot = round(h * (1 - MARGIN_MM[1] / A4_MM[1]))
    band = g.crop((0, top, w, bot)).point(lambda v: 255 if v < 238 else 0)
    box = band.getbbox()
    return 0.0 if not box else box[3] / (bot - top)


def quality(pdf: Path, n: int, rtype: str, shots: int) -> list[str]:
    warns = []
    budget = PAGE_BUDGET.get(rtype)
    if budget and rtype == "spec":   # 화면 설계는 그림 두 장에 한 쪽 남짓을 더 준다
        budget = max(budget, math.ceil(shots * 0.6) + 2)
    if budget and n > budget:
        warns.append(f"쪽 수 {n} > 예산 {budget}쪽({rtype}{f', 그림 {shots}장' if rtype == 'spec' else ''}) — 부록으로 빼거나 나눈다")
    try:
        fills = [fill_ratio(im) for _, im in page_images(pdf, 0.5)]
    except ImportError:
        return warns + ["(pypdfium2 없음: 쪽 채움 검사는 건너뜀)"]
    for i, f in enumerate(fills, 1):
        if i < len(fills) and f < FILL_MIN:
            warns.append(f"{i}쪽이 {f:.0%}만 참 — 다음 쪽으로 넘어간 큰 덩어리(표·캡처·카드)가 있는지 본다")
        if i == len(fills) and len(fills) > 1 and f < LAST_MIN:
            warns.append(f"마지막 {i}쪽이 {f:.0%}만 참 — 거의 빈 쪽. 앞 쪽으로 당기거나 줄인다")
    print("쪽 채움 " + " · ".join(f"{i}:{f:.0%}" for i, f in enumerate(fills, 1)))
    return warns


def pngs(pdf: Path, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{pdf.stem}_p*.png"):
        old.unlink()
    for i, img in page_images(pdf, 1.4):
        path = folder / f"{pdf.stem}_p{i + 1}.png"
        img.save(path)
    print(f"쪽 이미지 {folder}/{pdf.stem}_p*.png")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    ap.add_argument("--pngs", action="store_true", help="쪽마다 PNG 를 <이름>_pages/ 에")
    ap.add_argument("--pages-dir", help="쪽 PNG 폴더(지정하면 --pngs 를 켠다)")
    ap.add_argument("--strict", action="store_true", help="쪽 품질 경고가 있으면 exit 1")
    a = ap.parse_args()
    src = Path(a.src)
    out = Path(a.out) if a.out else src.with_suffix(".pdf")
    n, rtype, shots = asyncio.run(run(src, out))
    print(f"OK {out}" + (f" ({n}쪽)" if n > 0 else ""))
    warns = quality(out, n, rtype, shots)
    for w in warns:
        print("WARN ", w)
    print("쪽 품질 " + ("문제 없음" if not warns else f"경고 {len(warns)}"))
    if a.pngs or a.pages_dir:
        pngs(out, Path(a.pages_dir) if a.pages_dir else out.with_name(f"{out.stem}_pages"))
    return 1 if a.strict and any(not re.match(r"^\(", w) for w in warns) else 0


if __name__ == "__main__":
    sys.exit(main())
