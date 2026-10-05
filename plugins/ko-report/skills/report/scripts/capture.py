"""QA 보고용 화면 캡처 — 앱 화면 영역만, 2배 해상도 PNG.

사용: uv run --with playwright python capture.py shots.txt -o shots/
  (처음 한 번: uv run --with playwright python -m playwright install chromium)

shots.txt 한 줄 = 한 장:  파일이름 | URL | 선택자(생략 시 화면 전체) | 가로폭(기본 1440)
  예) login | https://example.com/login | main | 1440
      admin-home | https://example.com/admin |  | 1600
'#' 로 시작하면 주석. 로그인이 필요한 화면은 --storage 로 저장해 둔 세션(state.json)을 쓴다.

규칙(references/qa.md): 브라우저 테두리·바탕화면 넣지 않음, 개인정보·비밀값이 보이면 찍지 않거나 가림,
너무 넓은 화면은 줄이지 말고 영역을 나눠 여러 장으로.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def run(spec: Path, out: Path, storage: str | None, wait_ms: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for line in spec.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        name, url = parts[0], parts[1]
        sel = parts[2] if len(parts) > 2 and parts[2] else None
        width = int(parts[3]) if len(parts) > 3 and parts[3] else 1440
        rows.append((name, url, sel, width))
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for name, url, sel, width in rows:
            ctx = await browser.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2,
                                            storage_state=storage)
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_timeout(wait_ms)
            target = out / f"{name}.png"
            if sel:
                await page.locator(sel).first.screenshot(path=str(target))
            else:
                await page.screenshot(path=str(target), full_page=False)
            print(f"OK {target}")
            await ctx.close()
        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("-o", "--out", default="shots")
    ap.add_argument("--storage", help="로그인 세션 state.json")
    ap.add_argument("--wait", type=int, default=800, help="로딩 뒤 추가 대기(ms)")
    a = ap.parse_args()
    asyncio.run(run(Path(a.spec), Path(a.out), a.storage, a.wait))


if __name__ == "__main__":
    main()
