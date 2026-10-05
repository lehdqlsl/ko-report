"""빌드한 보고서 HTML 을 화면 폭 두 가지(1280·390)로 찍고, 깨진 곳을 기계로 찾는다.

사용: uv run --with playwright python preview.py 보고서.html [-o 폴더] [--widths 1280,390] [--dark]
  결과: <폴더>/<이름>_<폭>.png (전체 쪽). 폴더 기본값은 <이름>_preview/
찾는 것(WARN): 쪽 전체 가로 넘침, 화면 밖으로 나간 요소, 낱말 중간에서 줄이 바뀌거나 너무 좁아 글자가 세로로 쌓인 표 칸,
              가로 스크롤이 생긴 표(390px 에서는 정보로만).
찍은 그림은 Read 로 직접 본다. 이 스크립트는 눈으로 볼 곳을 좁혀 줄 뿐이다.
"""
from __future__ import annotations

import argparse
import asyncio
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


PROBE = r"""() => {
  const vw = document.documentElement.clientWidth, out = {overflow: 0, wide: [], narrow: [], scroll: []};
  out.overflow = document.documentElement.scrollWidth - vw;
  const name = el => (el.className && typeof el.className === 'string' ? el.tagName.toLowerCase() + '.' + el.className.split(' ')[0] : el.tagName.toLowerCase());
  const scroller = el => el.closest('.tbl, .plot, pre');
  for (const el of document.querySelectorAll('main *')) {
    const r = el.getBoundingClientRect();
    if (r.width && r.right > vw + 1 && !scroller(el)) out.wide.push(name(el) + ' (' + Math.round(r.right - vw) + 'px)');
  }
  // 낱말 중간에서 줄이 바뀐 표 칸("상품요 / 약서", 세로로 쌓인 글자). 띄어쓰기·가운뎃점·쉼표 뒤 줄바꿈은 정상.
  for (const td of document.querySelectorAll('td, th')) {
    const walker = document.createTreeWalker(td, NodeFilter.SHOW_TEXT);
    const cs = getComputedStyle(td), lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.5;
    let bad = 0, node, at = '';
    while ((node = walker.nextNode()) && !bad) {
      if (node.parentElement.closest('.tok, code')) continue;   // 긴 식별자는 일부러 아무 데서나 끊는다
      const t = node.textContent; let prevTop = null;
      for (let i = 0; i < t.length; i++) {
        const rg = document.createRange(); rg.setStart(node, i); rg.setEnd(node, i + 1);
        const r = rg.getBoundingClientRect(); if (!r.width) { prevTop = null; continue; }
        const top = Math.round(r.top);
        if (prevTop !== null && top > prevTop + lh * 0.6 && /\S/.test(t[i]) && /[^\s·,\/\-)(]/.test(t[i - 1]) && !/[(\[「"'·+=~&]/.test(t[i])) { bad = 1; at = t.slice(Math.max(0, i - 4), i) + '|' + t.slice(i, i + 4); break; }
        prevTop = /\s/.test(t[i]) ? null : top;
      }
    }
    if (bad) { out.narrow.push('"' + at + '" (' + td.clientWidth + 'px)'); continue; }
    // 너무 좁은 칸: 글자 4개 폭이 안 되는데 세 줄 넘게 쌓임
    const rg = document.createRange(); rg.selectNodeContents(td);
    const lines = new Set([...rg.getClientRects()].filter(r => r.width > 0).map(r => Math.round(r.top / (lh * 0.6)))).size;
    if (td.clientWidth < parseFloat(cs.fontSize) * 4 && lines >= 3) out.narrow.push(td.innerText.trim().slice(0, 12) + ' (' + td.clientWidth + 'px, ' + lines + '줄)');
  }
  for (const t of document.querySelectorAll('.tbl')) if (t.scrollWidth > t.clientWidth + 1)
    out.scroll.push((t.querySelector('th') || {}).innerText || '표');
  out.wide = [...new Set(out.wide)].slice(0, 8);
  return out;
}"""


async def run(src: Path, folder: Path, widths: list[int], dark: bool) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    problems = 0
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for w in widths:
            ctx = await b.new_context(viewport={"width": w, "height": 900}, device_scale_factor=1,
                                      color_scheme="dark" if dark else "light")
            pg = await ctx.new_page()
            await pg.goto(src.resolve().as_uri(), wait_until="networkidle")
            broken = await pg.evaluate(LOAD_ALL)
            await pg.wait_for_timeout(300)
            if broken:
                problems += broken
                print(f"WARN  불러오지 못한 그림 {broken}개")
            shot = folder / f"{src.stem}_{w}{'_dark' if dark else ''}.png"
            await pg.screenshot(path=str(shot), full_page=True)
            r = await pg.evaluate(PROBE)
            print(f"== {w}px  {shot}")
            if r["overflow"] > 1:
                problems += 1
                print(f"WARN  쪽 전체가 가로로 {r['overflow']}px 넘침")
            for x in r["wide"]:
                problems += 1
                print(f"WARN  화면 밖으로 나감: {x}")
            for x in r["narrow"][:10]:
                problems += 1
                print(f"WARN  표 칸 글자가 세로로 쌓임: {x}")
            if r["scroll"]:
                tag = "INFO " if w < 600 else "WARN "
                problems += 0 if w < 600 else len(r["scroll"])
                print(f"{tag} 가로 스크롤 표 {len(r['scroll'])}개: " + " / ".join(s[:12] for s in r["scroll"][:5]))
            await ctx.close()
        await b.close()
    print("미리보기 " + ("문제 없음" if not problems else f"경고 {problems}") + " — 그림은 직접 열어 본다")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out", help="그림 폴더(기본 <이름>_preview/)")
    ap.add_argument("--widths", default="1280,390")
    ap.add_argument("--dark", action="store_true", help="어두운 화면으로 찍기")
    a = ap.parse_args()
    src = Path(a.src)
    folder = Path(a.out) if a.out else src.with_name(f"{src.stem}_preview")
    asyncio.run(run(src, folder, [int(x) for x in a.widths.split(",")], a.dark))
    return 0


if __name__ == "__main__":
    sys.exit(main())
