"""보고서 원고(md) → 디자인 고정 단일 HTML.

사용: uv run --with markdown python build.py 원고.md [-o 결과.html]

원고 규칙(자세히는 references/components.md):
- 맨 위 front matter: title, type, audience(internal|external), date, author, basis
- ## 로 절을 나눈다. 첫 절은 결론이다(lead 로 강조).
- 블록: :::tiles / :::callout <색> / :::shots / :::defect <ID> | <제목> | <상태> / :::bars <제목>
- 표 칸 글자가 상태 단어와 정확히 같으면 색 딱지가 붙는다.
- 이미지(로컬 파일)는 base64 로 HTML 안에 넣는다 — 결과는 파일 하나.
스타일은 templates/report.css 하나뿐이고 원고에서 바꿀 수 없다.
"""
from __future__ import annotations

import argparse
import base64
import html
import mimetypes
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "templates" / "report.css").read_text(encoding="utf-8")
JS = (ROOT / "templates" / "report.js").read_text(encoding="utf-8")

TYPE_LABEL = {
    "progress": "진행 보고", "completion": "완료 보고", "qa": "QA 보고",
    "proposal": "제안", "analysis": "분석 보고", "incident": "장애 보고", "general": "보고",
}
STATUS = {
    "ok": ["통과", "완료", "해결", "정상", "적용", "반영", "켜져 있음", "유지", "확인됨", "성공"],
    "warn": ["진행", "진행 중", "보류", "주의", "지연", "부분", "확인 필요", "예정", "재현 안 됨", "검토"],
    "bad": ["실패", "미해결", "문제", "위험", "중단", "오류", "차단", "누락"],
    "info": ["정보", "참고", "결정", "결정 필요", "제안"],
}
WORD2CLS = {w: c for c, ws in STATUS.items() for w in ws}
COLORS = {"ok", "warn", "bad", "info"}


def parse_front(text: str) -> tuple[dict, str]:
    meta: dict = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                if ":" in line and not line.strip().startswith("#"):
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            text = text[end + 4:].lstrip("\n")
    return meta, text


def img_src(path: str, base: Path) -> str:
    if re.match(r"^(https?:|data:)", path):
        return path
    p = (base / path).resolve()
    if not p.exists():
        print(f"ERROR 이미지 없음: {path}", file=sys.stderr)
        return path
    data, mime = p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/png"
    if len(data) > 350_000 and mime in ("image/png", "image/jpeg"):
        data, mime = shrink(data, mime)
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def shrink(data: bytes, mime: str) -> tuple[bytes, str]:
    """큰 캡처는 가로 1800px·JPEG 85 로 줄인다(글자가 읽히는 선). Pillow 가 없으면 그대로 둔다."""
    try:
        from io import BytesIO
        from PIL import Image
    except ImportError:
        return data, mime
    im = Image.open(BytesIO(data)).convert("RGB")
    if im.width > 1800:
        im = im.resize((1800, round(im.height * 1800 / im.width)), Image.LANCZOS)
    buf = BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True)
    return (buf.getvalue(), "image/jpeg") if buf.tell() < len(data) else (data, mime)


def md_inline(s: str) -> str:
    out = markdown.markdown(s, extensions=["attr_list"])
    return re.sub(r"^<p>(.*)</p>$", r"\1", out.strip(), flags=re.S)


def figure(alt: str, src: str, base: Path) -> str:
    cap = f"<figcaption>{html.escape(alt)}</figcaption>" if alt else ""
    return f'<figure><img src="{img_src(src, base)}" alt="{html.escape(alt)}" loading="lazy">{cap}</figure>'


def render_block(kind: str, arg: str, body: str, base: Path) -> str:
    lines = [l for l in body.strip("\n").splitlines()]
    if kind == "tiles":
        cells = []
        for l in lines:
            parts = [p.strip() for p in l.split("|")]
            if not parts or not parts[0]:
                continue
            val, label = parts[0], parts[1] if len(parts) > 1 else ""
            cls = parts[2] if len(parts) > 2 and parts[2] in COLORS else ""
            cells.append(f'<div class="tile {cls}"><b>{html.escape(val)}</b><span>{md_inline(label)}</span></div>')
        return f'<div class="tiles">{"".join(cells)}</div>'
    if kind == "callout":
        cls = arg if arg in COLORS else ""
        return f'<div class="callout {cls}">{markdown.markdown(body, extensions=["attr_list"])}</div>'
    if kind == "shots":
        figs = [figure(m.group(1), m.group(2), base) for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", body)]
        return f'<div class="shots">{"".join(figs)}</div>'
    if kind == "defect":
        parts = [p.strip() for p in arg.split("|")]
        did, title = parts[0] if parts else "", parts[1] if len(parts) > 1 else ""
        status = parts[2] if len(parts) > 2 else ""
        cls = WORD2CLS.get(status, "bad")
        pill = f'<span class="pill {cls}">{html.escape(status)}</span>' if status else ""
        rows, imgs = [], []
        for l in lines:
            m = re.match(r"^\s*([^:：]{1,8})[:：]\s*(.*)$", l)
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            for im in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", val):
                imgs.append(figure(im.group(1), im.group(2), base))
            val = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", val).strip()
            if val:
                rows.append(f"<dt>{html.escape(key)}</dt><dd>{md_inline(val)}</dd>")
        return (f'<div class="defect {cls}"><div class="head"><span class="id">{html.escape(did)}</span>'
                f'<span class="ttl">{html.escape(title)}</span>{pill}</div><dl>{"".join(rows)}</dl>{"".join(imgs)}</div>')
    if kind == "bars":
        items = []
        for l in lines:
            parts = [p.strip() for p in l.split("|")]
            if len(parts) < 2:
                continue
            try:
                num = float(re.sub(r"[^0-9.\-]", "", parts[1]) or "0")
            except ValueError:
                num = 0.0
            items.append((parts[0], num, parts[1], parts[2] if len(parts) > 2 and parts[2] in COLORS else ""))
        mx = max((n for _, n, _, _ in items), default=1) or 1
        rows = "".join(
            f'<div class="bar {c}"><span>{html.escape(lbl)}</span><div class="track"><div class="fill" style="width:{max(n / mx * 100, 1):.1f}%"></div></div><span class="v">{html.escape(raw)}</span></div>'
            for lbl, n, raw, c in items)
        cap = f'<div class="cap">{html.escape(arg)}</div>' if arg else ""
        return f'<div class="bars">{cap}{rows}</div>'
    return f"<!-- 알 수 없는 블록 {kind} -->"


def expand_blocks(text: str, base: Path) -> tuple[str, list[str]]:
    """:::블록을 HTML 조각으로 바꾸고 자리표시자를 남긴다(마크다운 변환이 건드리지 않게)."""
    frags: list[str] = []

    def sub(m: re.Match) -> str:
        frags.append(render_block(m.group(1), (m.group(2) or "").strip(), m.group(3), base))
        return f"\n\nKOREPORTFRAG{len(frags) - 1}X\n\n"

    text = re.sub(r"^:::(\w+)[ \t]*(.*?)\n(.*?)^:::[ \t]*$", sub, text, flags=re.S | re.M)
    return text, frags


def decorate(body: str) -> str:
    body = re.sub(r"<table>", '<div class="tbl"><table>', body)
    body = re.sub(r"</table>", "</table></div>", body)

    def pill(m: re.Match) -> str:
        word = m.group(2).strip()
        cls = "warn" if word.startswith("[확인 필요") else WORD2CLS.get(word)
        if not cls:
            return m.group(0)
        return f'{m.group(1)}<span class="pill {cls}">{word}</span></td>'

    body = re.sub(r"(<td[^>]*>)([^<]{1,20})</td>", pill, body)
    body = re.sub(r"(<td[^>]*>)(\s*[-+]?[\d.,]+\s*(?:%|건|개|명|초|분|원|달러|GB|MB|ms|배)?\s*)</td>",
                  lambda m: m.group(1).replace("<td", '<td class="num"', 1) + m.group(2) + "</td>", body)
    # 본문 이미지 → figure
    body = re.sub(r'<p><img alt="([^"]*)" src="([^"]+)" ?/?></p>',
                  lambda m: f'<figure><img src="{m.group(2)}" alt="{m.group(1)}" loading="lazy"><figcaption>{m.group(1)}</figcaption></figure>', body)
    return body


def build(src: Path, out: Path) -> None:
    meta, text = parse_front(src.read_text(encoding="utf-8"))
    base = src.parent
    text, frags = expand_blocks(text, base)
    # 일반 본문 이미지도 base64 로
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", lambda m: f"![{m.group(1)}]({img_src(m.group(2), base)})", text)
    title = meta.get("title", "")
    if text.lstrip().startswith("# "):
        first, text = text.lstrip().split("\n", 1)
        title = title or first[2:].strip()
    html_body = markdown.markdown(text, extensions=["tables", "attr_list", "fenced_code", "sane_lists", "md_in_html"])
    for i, f in enumerate(frags):
        html_body = re.sub(rf"<p>KOREPORTFRAG{i}X</p>|KOREPORTFRAG{i}X", lambda _m, f=f: f, html_body)
    html_body = decorate(html_body)

    parts = re.split(r"(?=<h2>)", html_body)
    secs, toc = [], []
    pre = parts[0] if not parts[0].startswith("<h2>") else ""
    n = 0
    for p in parts:
        m = re.match(r"<h2>(.*?)</h2>", p)
        if not m:
            continue
        n += 1
        name = re.sub(r"^\d+\.\s*", "", re.sub(r"<[^>]+>", "", m.group(1))).strip()
        p = p.replace(m.group(0), f'<h2><span class="no">{n}</span>{name}</h2>', 1)
        toc.append(f'<li><a href="#s{n}">{n} {html.escape(re.sub(r" [(（].*", "", name))}</a></li>')
        secs.append(f'<section class="sec{" lead" if n == 1 else ""}" id="s{n}">\n{p}\n</section>')

    audience = meta.get("audience", "internal")
    badge = '<span class="badge internal">내부용</span>' if audience != "external" else '<span class="badge">대외</span>'
    label = TYPE_LABEL.get(meta.get("type", "general"), "보고")
    meta_line = " · ".join(x for x in [meta.get("date", ""), meta.get("author", ""), meta.get("basis", "")] if x)
    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="generator" content="ko-report">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&display=swap">
<style>
{CSS}
</style>
</head>
<body>
<div class="wrap">
  <main>
    <header class="doc">
      <p class="eyebrow">{label} {badge}</p>
      <h1>{html.escape(title)}</h1>
      {f'<p class="meta">{html.escape(meta_line)}</p>' if meta_line else ""}
      <nav class="toc" aria-label="목차"><p>목차</p><ol>{"".join(toc)}</ol></nav>
      {pre}
    </header>
{chr(10).join(secs)}
    <footer>{html.escape(meta.get("footer", meta_line))}</footer>
  </main>
</div>
<script>
{JS}</script>
</body>
</html>
"""
    out.write_text(page, encoding="utf-8")
    print(f"OK {out} ({len(page) // 1024}KB, 절 {n}개)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    src = Path(a.src)
    out = Path(a.out) if a.out else src.with_suffix(".html")
    build(src, out)


if __name__ == "__main__":
    main()
