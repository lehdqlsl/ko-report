"""QA 보고용 화면 캡처 — 앱 화면 영역만, 2배 해상도 PNG.

사용: uv run --with playwright python capture.py shots.txt -o shots/ [--storage state.json]
                                                [--hide 선택자 ...] [--unstick] [--wait ms]
  (처음 한 번: uv run --with playwright python -m playwright install chromium)

shots.txt 한 줄 = 한 장:  파일이름 | URL | 선택자(생략 시 화면 전체) | 가로폭(기본 1440) | 동작(생략 가능)
  예) login | https://example.com/login | main | 1440
      admin-home | https://example.com/admin |  | 1600
      dlg-apply | https://example.com/admin/docs | .dialog | 1440 | click=#apply;wait=400
  동작은 찍기 전에 차례로 한다. ';' 로 나눈다:
      click=<선택자>  hover=<선택자>  fill=<선택자>=<값>  press=<키>  wait=<ms>  js=<자바스크립트>
  js= 는 줄 끝까지 한 덩어리로 읽으므로 맨 뒤에 둔다(안에 ';'·'|' 를 써도 된다).
'#' 로 시작하면 주석. 로그인이 필요한 화면은 --storage 로 저장해 둔 세션(state.json)을 쓴다.

--hide 선택자   찍기 전에 숨긴다(떠 있는 안내·배지·채팅 버튼 등). 여러 번 쓸 수 있다.
--unstick       고정(fixed·sticky) 머리·바닥을 제자리로 풀어 요소 캡처에 겹치지 않게 한다.

규칙(references/qa.md): 브라우저 테두리·바탕화면 넣지 않음, 개인정보·비밀값이 보이면 찍지 않거나 가림,
너무 넓은 화면은 줄이지 말고 영역을 나눠 여러 장으로.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

UNSTICK = """() => { for (const el of document.querySelectorAll('body *')) {
  const p = getComputedStyle(el).position; if (p === 'fixed' || p === 'sticky') el.style.setProperty('position', p === 'fixed' ? 'absolute' : 'static', 'important'); } }"""


def parse_actions(s: str) -> list[tuple[str, str]]:
    acts = []
    m = re.search(r"(?:^|;)\s*js=", s)
    js = None
    if m:
        js = s[m.end():]
        s = s[: m.start()]
    for part in filter(None, (x.strip() for x in s.split(";"))):
        k, _, v = part.partition("=")
        acts.append((k.strip(), v.strip()))
    if js is not None:
        acts.append(("js", js.strip()))
    return acts


async def act(page, actions: list[tuple[str, str]]) -> None:
    for k, v in actions:
        if k == "click":
            await page.locator(v).first.click()
        elif k == "hover":
            await page.locator(v).first.hover()
        elif k == "fill":
            sel, _, val = v.partition("=")
            await page.locator(sel).first.fill(val)
        elif k == "press":
            await page.keyboard.press(v)
        elif k == "wait":
            await page.wait_for_timeout(int(v))
        elif k == "js":
            await page.evaluate(f"() => {{ {v} }}")
        else:
            raise SystemExit(f"알 수 없는 동작: {k}")


async def run(spec: Path, out: Path, storage: str | None, wait_ms: int, hide: list[str], unstick: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for line in spec.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 4)]
        name, url = parts[0], parts[1]
        sel = parts[2] if len(parts) > 2 and parts[2] else None
        width = int(parts[3]) if len(parts) > 3 and parts[3] else 1440
        actions = parse_actions(parts[4]) if len(parts) > 4 and parts[4] else []
        rows.append((name, url, sel, width, actions))
    css = "".join(f"{h} {{ display: none !important; }}" for h in hide)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for name, url, sel, width, actions in rows:
            ctx = await browser.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2,
                                            storage_state=storage)
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_timeout(wait_ms)
            if css:
                await page.add_style_tag(content=css)
            await act(page, actions)
            if unstick and sel:
                await page.evaluate(UNSTICK)
            target = out / f"{name}.png"
            if sel:
                await page.locator(sel).first.screenshot(path=str(target))
            else:
                await page.screenshot(path=str(target), full_page=False)
            print(f"OK {target}" + (f"  ({json.dumps([a for a, _ in actions], ensure_ascii=False)})" if actions else ""))
            await ctx.close()
        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("-o", "--out", default="shots")
    ap.add_argument("--storage", help="로그인 세션 state.json")
    ap.add_argument("--wait", type=int, default=800, help="로딩 뒤 추가 대기(ms)")
    ap.add_argument("--hide", action="append", default=[], help="찍기 전에 숨길 선택자(여러 번)")
    ap.add_argument("--unstick", action="store_true", help="fixed·sticky 요소를 제자리로 풀고 찍기")
    a = ap.parse_args()
    asyncio.run(run(Path(a.spec), Path(a.out), a.storage, a.wait, a.hide, a.unstick))


if __name__ == "__main__":
    main()
