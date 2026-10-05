"""보고서 원고(md) → 디자인 고정 단일 HTML.

사용: uv run --with markdown --with pillow python build.py 원고.md [-o 결과.html] [--layout web]

원고 규칙(자세히는 references/components.md):
- 맨 위 front matter: title, type, audience(internal|external), date, author, basis, layout(doc|web),
  label(머리 표시 바꾸기), numbering(off 면 절 번호 없음)
- ## 로 절을 나눈다. 첫 절은 결론이다(lead 로 강조). "## 긴 제목 | 짧은 이름" 이면 목차에는 짧은 이름.
- 블록: :::tiles / :::callout <색> / :::shots / :::defect <ID> | <제목> | <상태> / :::bars <제목>
        :::chart <bar|line|donut> <제목> / :::steps <제목> / :::screen <번호> | <화면 이름> | <메뉴>
        :::scene <장면 N> | <화면 X>
- 이미지가 없으면 HTML 은 만들되 exit 1.
- 상태 딱지는 머리글에 상태·결과·판정·진행·평가가 들어간 열에서만 붙는다.
- [확인 필요: …] 는 어디에 있든 노란 표시로 남는다.
- 이미지(로컬 파일)는 base64 로 HTML 안에 넣는다. 결과는 파일 하나.
스타일은 templates/report.css 하나뿐이고 원고에서 바꿀 수 없다.
"""
from __future__ import annotations

import argparse
import base64
import html
import math
import mimetypes
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "templates" / "report.css").read_text(encoding="utf-8")
JS = (ROOT / "templates" / "report.js").read_text(encoding="utf-8")

TYPE_LABEL = {
    "progress": "진행 보고", "completion": "완료 보고", "qa": "QA 보고", "proposal": "제안",
    "analysis": "분석 보고", "incident": "장애 보고", "policy": "정책", "spec": "화면 설계",
}
STATUS = {
    "ok": ["통과", "완료", "해결", "정상", "적용", "반영", "켜져 있음", "유지", "확인됨", "성공",
           "수리", "수정 완료", "변경 불요"],
    "warn": ["진행", "진행 중", "보류", "주의", "지연", "부분", "확인 필요", "예정", "재현 안 됨", "검토",
             "대기", "재검수", "검수 중", "미배정", "미정", "다음 판", "수정 중"],
    "bad": ["실패", "미해결", "문제", "위험", "중단", "오류", "차단", "누락"],
    "info": ["정보", "참고", "결정", "결정 필요", "제안", "해당 없음"],
}
WORD2CLS = {w: c for c, ws in STATUS.items() for w in ws}
STATUS_RE = re.compile(r"^(" + "|".join(sorted(map(re.escape, WORD2CLS), key=len, reverse=True)) + r")(\s*[(（·,].*)?$", re.S)
PILL_HEADERS = ("상태", "결과", "판정", "진행", "평가")
COLORS = {"ok", "warn", "bad", "info"}
CARD_KEYS = {
    "defect": ["관찰", "기대", "증거", "조치", "원인", "재현", "추정"],
}
SCREEN_KEYS = ["하는 일", "보이는 것", "누르면", "누름", "그러면", "이후", "규칙", "권한", "비고", "메모"]
SCENE_KEYS = {"누름", "그러면", "이후"}
IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
KEY_COL_MAX = 20  # 첫 열이 전부 이 글자 수 이하이면 이름 열로 보고 굵게
ERRORS: list[str] = []   # 빌드는 끝까지 하되 하나라도 있으면 exit 1


def parse_front(text: str) -> tuple[dict, str]:
    meta: dict = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                if ":" in line and not line.strip().startswith("#"):
                    k, v = line.split(":", 1)
                    meta[k.strip()] = re.sub(r"\s{2,}#\s.*$", "", v).strip()  # 줄 끝 설명 주석
            text = text[end + 4:].lstrip("\n")
    return meta, text


def img_src(path: str, base: Path) -> str:
    if re.match(r"^(https?:|data:)", path):
        return path
    p = (base / path).resolve()
    if not p.exists():
        ERRORS.append(f"이미지 없음: {path}")
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


def md_rich(s: str, base: Path) -> str:
    """카드 칸 하나: 인라인 문법 + 이미지(figure) + 펜스 코드. 한 문단이면 <p> 를 벗긴다."""
    figs: list[str] = []

    def keep(m: re.Match) -> str:
        figs.append(figure(m.group(1), m.group(2), base))
        return f"\n\nKRFIG{len(figs) - 1}X\n\n"

    out = markdown.markdown(IMG_RE.sub(keep, s).strip(), extensions=["fenced_code", "attr_list", "sane_lists"]).strip()
    if out.count("<p>") == 1 and out.startswith("<p>") and out.endswith("</p>"):
        out = out[3:-4]
    for i, f in enumerate(figs):
        out = re.sub(rf"<p>KRFIG{i}X</p>|KRFIG{i}X", lambda _m, f=f: f, out)
    return out


def card_rows(body: str, keys: list[str]) -> tuple[list[str], list[list]]:
    """카드 본문을 [앞부분 줄], [[키, [줄…]], …] 로 나눈다.
    정해진 키로 시작하는 줄만 새 칸이 되고, 나머지 줄과 펜스 코드는 앞 칸에 이어 붙는다."""
    key_re = re.compile(r"^\s*(" + "|".join(map(re.escape, keys)) + r")\s*[:：]\s?(.*)$")
    head: list[str] = []
    rows: list[list] = []
    fence = None
    for line in body.strip("\n").splitlines():
        target = rows[-1][1] if rows else head
        if fence:
            target.append(line)
            if line.strip().startswith(fence):
                target.append("")
                fence = None
            continue
        fm = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fm:
            fence = fm.group(1)
            target.extend(["", line.strip()])
            continue
        m = key_re.match(line)
        if m:
            rows.append([m.group(1), [m.group(2)]])
        else:
            target.append(line)
    return head, rows


def dl(rows: list[list], base: Path) -> str:
    return "".join(f"<dt>{html.escape(k)}</dt><dd>{md_rich(chr(10).join(v), base)}</dd>" for k, v in rows)


def first_num(s: str) -> float:
    """'17~33' → 17, '100% (CM 83%)' → 100, '1,284' → 1284. 숫자가 없으면 0."""
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s)
    return float(m.group().replace(",", "")) if m else 0.0


def text_em(s: str) -> float:
    """대략적인 글자 폭(em). 한글 1, 숫자·영문 0.6."""
    return sum(0.6 if ord(ch) < 0x2E80 else 1.0 for ch in s)


def rows_of(lines: list[str]) -> list[tuple[str, float, str, str]]:
    items = []
    for l in lines:
        parts = [p.strip() for p in l.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        items.append((parts[0], first_num(parts[1]), parts[1], parts[2] if len(parts) > 2 and parts[2] in COLORS else ""))
    return items


def render_block(kind: str, arg: str, body: str, base: Path) -> str:
    lines = [l for l in body.strip("\n").splitlines()]
    if kind == "tiles":
        cells, longest = [], 0.0
        for l in lines:
            parts = [p.strip() for p in l.split("|")]
            if not parts or not parts[0]:
                continue
            val, label = parts[0], parts[1] if len(parts) > 1 else ""
            cls = parts[2] if len(parts) > 2 and parts[2] in COLORS else ""
            longest = max(longest, text_em(val))
            cells.append(f'<div class="tile {cls}"><b>{html.escape(val)}</b><span>{md_inline(label)}</span></div>')
        long = " long xlong" if longest > 5.0 else " long" if longest > 4.0 else ""  # 한 줄 안에서는 같은 크기로
        return f'<div class="tiles n{len(cells)}{long}">{"".join(cells)}</div>'
    if kind == "callout":
        color, _, ttl = arg.partition(" ")
        if color not in COLORS:   # ":::callout 제목" 처럼 색 없이
            color, ttl = "", arg
        ttl_h = f'<p class="ttl">{html.escape(ttl.strip())}</p>' if ttl.strip() else ""
        return f'<div class="callout {color}">{ttl_h}{markdown.markdown(body, extensions=["attr_list", "tables"])}</div>'
    if kind == "shots":
        figs = [figure(m.group(1), m.group(2), base) for m in IMG_RE.finditer(body)]
        cols = f" c{arg}" if arg in ("1", "2", "3") else ""   # ":::shots 1" = 한 줄에 한 장
        return f'<div class="shots n{len(figs)}{cols}">{"".join(figs)}</div>'
    if kind in ("screen", "scene"):
        return render_screen(kind, arg, body, base)
    if kind == "defect":
        parts = [p.strip() for p in arg.split("|")]
        did, title = parts[0] if parts else "", parts[1] if len(parts) > 1 else ""
        head, rows = card_rows(body, CARD_KEYS[kind])
        lead = md_rich("\n".join(head), base) if "".join(head).strip() else ""
        status = parts[2] if len(parts) > 2 else ""
        cls = WORD2CLS.get(status, "bad")
        pill = f'<span class="pill {cls}">{html.escape(status)}</span>' if status else ""
        return (f'<div class="defect {cls}"><div class="head"><span class="id">{html.escape(did)}</span>'
                f'<span class="ttl">{html.escape(title)}</span>{pill}</div>{lead}<dl>{dl(rows, base)}</dl></div>')
    if kind == "bars":
        items = rows_of(lines)
        mx = max((n for _, n, _, _ in items), default=1) or 1
        vw = max((text_em(raw) for _, _, raw, _ in items), default=2) + 0.3
        rows = "".join(
            f'<div class="bar {c}"><span>{md_inline(lbl)}</span><div class="track"><div class="fill" style="width:{max(n / mx * 100, 1):.1f}%"></div></div><span class="v">{html.escape(raw)}</span></div>'
            for lbl, n, raw, c in items)
        cap = f'<div class="cap">{html.escape(arg)}</div>' if arg else ""
        return f'<div class="bars" style="--vw:{vw:.1f}em">{cap}{rows}</div>'
    if kind == "chart":
        ctype, _, title = arg.partition(" ")
        return chart(ctype.strip(), title.strip(), rows_of(lines))
    if kind == "steps":
        items = []
        for l in lines:
            parts = [p.strip() for p in l.split("|", 1)]
            if parts[0]:
                items.append((parts[0], parts[1] if len(parts) > 1 else ""))
        horiz = len(items) <= 5 and all(text_em(a) + text_em(b) <= 46 for a, b in items)
        lis = "".join(f'<li><span class="n">{i}</span><div><b>{md_inline(a)}</b>{f"<p>{md_inline(b)}</p>" if b else ""}</div></li>'
                      for i, (a, b) in enumerate(items, 1))
        cap = f'<div class="cap">{html.escape(arg)}</div>' if arg else ""
        return f'<div class="steps {"h" if horiz else "v"}">{cap}<ol style="--n:{len(items)}">{lis}</ol></div>'
    return f"<!-- 알 수 없는 블록 {kind} -->"


def render_screen(kind: str, arg: str, body: str, base: Path) -> str:
    """화면 카드(:::screen 번호 | 이름 | 메뉴)와 장면 카드(:::scene 장면 N | 화면 X).
    본문 줄: 키 줄(하는 일·보이는 것·누르면·누름·그러면·이후·규칙·권한·비고·메모), 그림 줄, 표 줄(| 로 시작).
    화면: 머리 → 하는 일 한 줄 → 그림 → 요소 표 → 나머지 칸 → 메모. 장면: 머리 → [그림 | 누름·그러면] → 표."""
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) == 1:  # ":::screen 화면 이름" 처럼 번호 없이
        parts = ["", parts[0]]
    sid, title = parts[0], parts[1] if len(parts) > 1 else ""
    menu = parts[2] if len(parts) > 2 else ""
    key_re = re.compile(r"^\s*(" + "|".join(map(re.escape, SCREEN_KEYS)) + r")\s*[:：]\s?(.*)$")
    pics: list[str] = []
    tables: list[list[str]] = []
    rows: list[list] = []
    pre: list[str] = []    # 그림·표 앞 글(하는 일 다음에 놓임)
    post: list[str] = []   # 그림·표 뒤 글
    cur = None
    fence = None
    in_table = False
    for line in body.strip("\n").splitlines():
        st = line.strip()
        extra = post if (pics or tables) else pre
        if not st and not fence:   # 빈 줄은 칸·표를 끝낸다
            cur, in_table = None, False
            extra.append("")
            continue
        if fence:
            (cur if cur is not None else extra).append(line)
            if st.startswith(fence):
                fence = None
            continue
        if re.match(r"^(`{3,}|~{3,})", st):
            fence = st[:3]
            (cur if cur is not None else extra).extend(["", line])
            in_table = False
            continue
        if st.startswith("|"):
            if not in_table:
                tables.append([])
                in_table = True
            tables[-1].append(st)
            cur = None
            continue
        in_table = False
        if IMG_RE.fullmatch(st):
            pics.append(st)
            cur = None
            continue
        m = key_re.match(line)
        if m:
            rows.append([m.group(1), [m.group(2)]])
            cur = rows[-1][1]
        elif st:
            (cur if cur is not None else extra).append(line)
    scene = kind == "scene" or any(k in SCENE_KEYS for k, _ in rows)
    does = next((v for k, v in rows if k == "하는 일"), None)
    memo = next((v for k, v in rows if k == "메모"), None)
    other = [r for r in rows if r[0] not in ("하는 일", "메모")]
    head = (f'<div class="head"><span class="id">{html.escape(sid)}</span><h3 class="ttl">{html.escape(title)}</h3>'
            + (f'<span class="menu">{html.escape(menu)}</span>' if menu else "") + "</div>")
    does_h = f'<p class="does">{md_inline(chr(10).join(does))}</p>' if does else ""
    pic_h = f'<div class="pic">{"".join(md_rich(p, base) for p in pics)}</div>' if pics else '<div class="pic"></div>'
    tbl_h = ""
    for t in tables:
        ncol = len([c for c in t[0].strip("|").split("|")])
        tbl_h += f'<div class="elements cols{ncol}">' + markdown.markdown("\n".join(t), extensions=["tables"]) + "</div>"
    dl_h = f"<dl>{dl(other, base)}</dl>" if other else ""
    pre_h = md_rich("\n".join(pre), base) if "".join(pre).strip() else ""
    extra_h = md_rich("\n".join(post), base) if "".join(post).strip() else ""
    memo_h = f'<p class="memo"><b>메모</b>{md_inline(chr(10).join(memo))}</p>' if memo else ""
    if scene:
        return (f'<div class="screen scene">{head}{does_h}{pre_h}<div class="body">{pic_h}{dl_h}</div>'
                f'{tbl_h}{extra_h}{memo_h}</div>')
    side = " side" if dl_h and not tables else ""   # 그림 옆에 설명(넓은 화면)
    if side:
        return f'<div class="screen{side}">{head}{does_h}{pre_h}<div class="body">{pic_h}{dl_h}</div>{extra_h}{memo_h}</div>'
    return f'<div class="screen">{head}{does_h}{pre_h}{pic_h}{tbl_h}{dl_h}{extra_h}{memo_h}</div>'


# ---------- :::chart (인라인 SVG, 색은 CSS 토큰) ----------

def nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v < hi - 1e-9:
        ticks.append(v)
        v += step
    ticks.append(v)
    return [round(t, 10) for t in ticks]


def fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v - round(v)) < 1e-9 else f"{v:,.1f}"


def wrap_label(s: str, max_em: float) -> list[str]:
    if text_em(s) <= max_em:
        return [s]
    words = s.split(" ")
    if len(words) > 1:
        for i in range(len(words) - 1, 0, -1):
            a, b = " ".join(words[:i]), " ".join(words[i:])
            if text_em(a) <= max_em and text_em(b) <= max_em:
                return [a, b]
    out, cur = [], ""
    for ch in s:
        if text_em(cur + ch) > max_em:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return out[:2] if len(out) <= 2 else [out[0], out[1][:-1] + "…"]


def chart(ctype: str, title: str, items: list[tuple[str, float, str, str]], W: int = 640) -> str:
    """W 는 그리는 폭(viewBox). 글자 12px 이 화면에서도 비슷한 크기로 보이게, 두 단 묶음 안에서는 좁게 그린다."""
    cap = f'<div class="cap">{html.escape(title)}</div>' if title else ""
    if not items:
        return f'<div class="chart">{cap}</div>'
    label = html.escape(title or ctype)
    if ctype == "donut":
        return f'<div class="chart k-donut">{cap}{donut(items, label)}</div>'
    H, FS = 250 if W >= 600 else 230, 12
    L, R, T, B = 44, 12, 22, 40
    vals = [n for _, n, _, _ in items]
    lo = min(0.0, min(vals))
    if ctype == "line" and lo == 0 and min(vals) > 0 and (max(vals) - min(vals)) < 0.25 * max(vals):
        lo = min(vals)
    ticks = nice_ticks(lo, max(vals) if max(vals) > lo else lo + 1)
    y0, y1 = ticks[0], ticks[-1]
    L = round(max(text_em(fmt(t)) for t in ticks) * FS + 14)  # 눈금 글자 폭만큼
    pw, ph = W - L - R, H - T - B

    def y(v: float) -> float:
        return T + ph - (v - y0) / (y1 - y0) * ph

    g = []
    for t in ticks:
        g.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                 f'<text class="tick" x="{L - 8}" y="{y(t) + 4:.1f}" text-anchor="end">{fmt(t)}</text>')
    n = len(items)
    slot = pw / n
    step_lbl = 1 if ctype == "bar" else max(1, math.ceil(n / 8))
    marks = []
    two_line = False
    for i, (lbl, v, raw, c) in enumerate(items):
        cx = L + slot * (i + 0.5)
        if i % step_lbl == 0 or i == n - 1:
            lines = wrap_label(lbl, max(slot * step_lbl / FS - 0.4, 2.5))
            two_line = two_line or len(lines) > 1
            tspans = "".join(f'<tspan x="{cx:.1f}" dy="{0 if k == 0 else 14}">{html.escape(t)}</tspan>' for k, t in enumerate(lines))
            marks.append(f'<text class="xl" x="{cx:.1f}" y="{T + ph + 18}" text-anchor="middle">{tspans}</text>')
    def shown(raw: str, v: float) -> str:
        return raw if text_em(raw) <= 7 else fmt(v)

    if ctype == "line":
        pts = [(L + slot * (i + 0.5), y(v)) for i, (_, v, _, _) in enumerate(items)]
        path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{yy:.1f}" for i, (x, yy) in enumerate(pts))
        area = path + f" L{pts[-1][0]:.1f},{y(y0):.1f} L{pts[0][0]:.1f},{y(y0):.1f} Z"
        marks.insert(0, f'<path class="area" d="{area}"/><path class="line" d="{path}"/>')
        imax = max(range(n), key=lambda i: items[i][1])
        for i, ((x, yy), (lbl, v, raw, c)) in enumerate(zip(pts, items)):
            marks.append(f'<circle class="dot {c}" cx="{x:.1f}" cy="{yy:.1f}" r="4.5"><title>{html.escape(lbl)}: {html.escape(raw)}</title></circle>')
            if i in (imax, n - 1) or c:
                marks.append(f'<text class="val" x="{x:.1f}" y="{yy - 10:.1f}" text-anchor="middle">{html.escape(shown(raw, v))}</text>')
    else:
        bw = min(28.0, slot * 0.6)
        base_y = y(max(0.0, y0))
        for i, (lbl, v, raw, c) in enumerate(items):
            cx = L + slot * (i + 0.5)
            top, bot = (y(v), base_y) if v >= 0 else (base_y, y(v))
            h = max(bot - top, 1.0)
            r = min(4.0, h, bw / 2)
            x0, x1 = cx - bw / 2, cx + bw / 2
            if v >= 0:  # 위쪽 끝만 둥글게
                d = f"M{x0:.1f},{bot:.1f} V{top + r:.1f} Q{x0:.1f},{top:.1f} {x0 + r:.1f},{top:.1f} H{x1 - r:.1f} Q{x1:.1f},{top:.1f} {x1:.1f},{top + r:.1f} V{bot:.1f} Z"
            else:
                d = f"M{x0:.1f},{top:.1f} V{bot - r:.1f} Q{x0:.1f},{bot:.1f} {x0 + r:.1f},{bot:.1f} H{x1 - r:.1f} Q{x1:.1f},{bot:.1f} {x1:.1f},{bot - r:.1f} V{top:.1f} Z"
            marks.append(f'<path class="col {c}" d="{d}"><title>{html.escape(lbl)}: {html.escape(raw)}</title></path>')
            ty = top - 7 if v >= 0 else bot + 15
            marks.append(f'<text class="val" x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle">{html.escape(shown(raw, v))}</text>')
        marks.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{base_y:.1f}" y2="{base_y:.1f}"/>')
    svg = (f'<svg viewBox="0 0 {W} {H + (14 if two_line else 0)}" '
           f'role="img" aria-label="{label}">{"".join(g)}{"".join(marks)}</svg>')
    return f'<div class="chart k-{html.escape(ctype)}">{cap}<div class="plot">{svg}</div></div>'


def donut(items: list[tuple[str, float, str, str]], label: str) -> str:
    total = sum(max(v, 0) for _, v, _, _ in items) or 1
    R, r, C = 80, 52, 90
    segs, legend, a = [], [], -math.pi / 2
    for i, (lbl, v, raw, c) in enumerate(items):
        cls = c or f"c{i % 8 + 1}"
        frac = max(v, 0) / total
        b = a + frac * 2 * math.pi
        if frac >= 0.9999:
            segs.append(f'<circle class="seg {cls}" cx="{C}" cy="{C}" r="{(R + r) / 2}" fill="none" stroke-width="{R - r}"><title>{html.escape(lbl)}: {html.escape(raw)}</title></circle>')
        elif frac > 0:
            large = 1 if b - a > math.pi else 0
            p = lambda rad, ang: f"{C + rad * math.cos(ang):.2f},{C + rad * math.sin(ang):.2f}"  # noqa: E731
            d = f"M{p(R, a)} A{R},{R} 0 {large} 1 {p(R, b)} L{p(r, b)} A{r},{r} 0 {large} 0 {p(r, a)} Z"
            segs.append(f'<path class="seg {cls}" d="{d}"><title>{html.escape(lbl)}: {html.escape(raw)}</title></path>')
        legend.append(f'<li><i class="sw {cls}"></i><span>{md_inline(lbl)}</span><b>{html.escape(raw)}</b><em>{frac * 100:.0f}%</em></li>')
        a = b
    center = f'<text class="ctr" x="{C}" y="{C + 6}" text-anchor="middle">{fmt(total)}</text>'
    svg = f'<svg viewBox="0 0 {2 * C} {2 * C}" role="img" aria-label="{label}">{"".join(segs)}{center}</svg>'
    return f'<div class="plot">{svg}<ul class="legend">{"".join(legend)}</ul></div>'


# ---------- 조립 ----------

def expand_blocks(text: str, base: Path) -> tuple[str, list[str]]:
    """:::블록을 HTML 조각으로 바꾸고 자리표시자를 남긴다(마크다운 변환이 건드리지 않게).
    연달아 놓인 강조 상자·차트는 한 묶음으로 감싼다(넓은 화면에서 두 단)."""
    frags: list[str] = []
    kinds: list[str] = []
    specs: list = []

    def sub(m: re.Match) -> str:
        frags.append(render_block(m.group(1), (m.group(2) or "").strip(), m.group(3), base))
        kinds.append(m.group(1))
        ctype, _, ctitle = (m.group(2) or "").strip().partition(" ")
        specs.append((ctype.strip(), ctitle.strip(), rows_of(m.group(3).strip("\n").splitlines())) if m.group(1) == "chart" else None)
        return f"\n\nKOREPORTFRAG{len(frags) - 1}X\n\n"

    text = re.sub(r"^:::(\w+)[ \t]*(.*?)\n(.*?)^:::[ \t]*$", sub, text, flags=re.S | re.M)

    def group(m: re.Match) -> str:
        ids = [int(i) for i in re.findall(r"KOREPORTFRAG(\d+)X", m.group(0))]
        runs: list[list[int]] = []
        for i in ids:  # 같은 종류끼리 이어진 덩어리로 자른다
            if runs and kinds[runs[-1][-1]] == kinds[i]:
                runs[-1].append(i)
            else:
                runs.append([i])
        out = []
        for run in runs:
            if len(run) > 1 and kinds[run[0]] in ("callout", "chart"):
                inner = [chart(*specs[i], W=440) if kinds[i] == "chart" and specs[i] else frags[i] for i in run]
                frags.append(f'<div class="group {kinds[run[0]]}s">' + "".join(inner) + "</div>")
                for i in run:
                    frags[i] = ""
                run = [len(frags) - 1]
            out += [f"KOREPORTFRAG{i}X" for i in run]
        return "\n\n" + "\n\n".join(out) + "\n\n"

    text = re.sub(r"(?:KOREPORTFRAG\d+X\s*){2,}", group, text)
    return text, frags


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def deco_table(m: re.Match) -> str:
    t = m.group(0)
    heads = [strip_tags(h) for h in re.findall(r"<th(?:\s[^>]*)?>(.*?)</th>", t, re.S)]
    pillcols = {i for i, h in enumerate(heads) if any(k in h for k in PILL_HEADERS)}
    firsts: list[str] = []

    def row(rm: re.Match) -> str:
        idx = -1

        def cell(cm: re.Match) -> str:
            nonlocal idx
            idx += 1
            open_, inner = cm.group(1), cm.group(2)
            txt = strip_tags(inner)
            if idx == 0:
                firsts.append(txt)
            if idx in pillcols and "<" not in inner:
                sm = STATUS_RE.match(txt)
                if sm:
                    inner = f'<span class="pill {WORD2CLS[sm.group(1)]}">{sm.group(1)}</span>{html.escape(sm.group(2) or "")}'
            if re.fullmatch(r"\s*[-+]?[\d.,]+\s*(?:%|건|개|명|초|분|원|달러|GB|MB|ms|배|쪽|자)?\s*", txt):
                open_ = open_.replace("<td", '<td class="num"', 1)
            return f"{open_}{inner}</td>"

        return re.sub(r"(<td[^>]*>)(.*?)</td>", cell, rm.group(0), flags=re.S)

    t = re.sub(r"<tr>.*?</tr>", row, t, flags=re.S)
    # 숫자만 있는 열은 머리글도 오른쪽 정렬
    body_rows = re.findall(r"<tr>(.*?)</tr>", t.split("</thead>")[-1], re.S)
    cols = [re.findall(r"<td([^>]*)>(.*?)</td>", r, re.S) for r in body_rows]
    numcols = {i for i in range(len(heads))
               if any(i < len(c) and strip_tags(c[i][1]) for c in cols)
               and all(i >= len(c) or not strip_tags(c[i][1]) or 'class="num"' in c[i][0] for c in cols)}
    hi = -1

    def head(hm: re.Match) -> str:
        nonlocal hi
        hi += 1
        return hm.group(0).replace("<th", '<th class="num"', 1) if hi in numcols else hm.group(0)

    t = re.sub(r"<th(?:\s[^>]*)?>", head, t)
    key = firsts and max(len(f) for f in firsts) <= KEY_COL_MAX
    return f'<div class="tbl{" keycol" if key else ""}">{t}</div>'


def decorate(body: str) -> str:
    body = re.sub(r"<table>.*?</table>", deco_table, body, flags=re.S)
    # 본문 이미지 → figure
    body = re.sub(r'<p><img alt="([^"]*)" src="([^"]+)" ?/?></p>',
                  lambda m: f'<figure><img src="{m.group(2)}" alt="{m.group(1)}" loading="lazy"><figcaption>{m.group(1)}</figcaption></figure>', body)
    # 태그 밖 글자만 손본다(코드 안은 그대로)
    parts = re.split(r"(<[^>]+>)", body)
    in_code, in_cell = 0, 0
    for i, part in enumerate(parts):
        if i % 2:
            tag = re.match(r"</?(\w+)", part)
            name = tag.group(1).lower() if tag else ""
            if name in ("code", "pre"):
                in_code += -1 if part.startswith("</") else 1
            elif name in ("td", "th"):
                in_cell = 0 if part.startswith("</") else 1
            continue
        if in_code:
            continue
        t = re.sub(r"\[확인 필요[^\]]*\]",
                   lambda m: f'<mark class="gap{" short" if len(m.group(0)) <= 16 else ""}">{m.group(0)}</mark>', part)
        t = re.sub(r"(?<=\S)·", "\u2060·", t)   # 가운뎃점 앞에서 줄이 바뀌지 않게(낱말 잇기 문자)
        if in_cell:   # 표 칸의 긴 영문 식별자는 아무 데서나 끊어 표가 넓어지지 않게
            t = re.sub(r"[A-Za-z0-9_./:\-]{18,}", lambda m: f'<span class="tok">{m.group(0)}</span>', t)
        parts[i] = t
    return "".join(parts)


def build(src: Path, out: Path, layout: str | None = None) -> None:
    meta, text = parse_front(src.read_text(encoding="utf-8"))
    if layout:
        meta["layout"] = layout
    base = src.parent
    web = meta.get("layout", "doc") == "web"
    numbered = meta.get("numbering", "on") != "off"
    text, frags = expand_blocks(text, base)
    # 일반 본문 이미지도 base64 로
    text = IMG_RE.sub(lambda m: f"![{m.group(1)}]({img_src(m.group(2), base)})", text)
    title = meta.get("title", "")
    if text.lstrip().startswith("# "):
        first, text = text.lstrip().split("\n", 1)
        title = title or first[2:].strip()
    html_body = markdown.markdown(text, extensions=["tables", "attr_list", "fenced_code", "sane_lists", "md_in_html"])
    for i in range(len(frags) - 1, -1, -1):  # 묶음이 앞 조각을 품으므로 뒤에서부터
        html_body = re.sub(rf"<p>KOREPORTFRAG{i}X</p>|KOREPORTFRAG{i}X", lambda _m, f=frags[i]: f, html_body)
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
        raw = re.sub(r"^\d+\.\s*", "", m.group(1)).strip()
        name, _, short = raw.partition(" | ")
        short = strip_tags(short) or re.sub(r" [(（].*", "", strip_tags(name))
        no = f'<span class="no">{n}</span>' if numbered else ""
        h2 = f'<h2>{no}{name}</h2>'
        toc.append(f'<li><a href="#s{n}">{f"{n}&nbsp;" if numbered else ""}{html.escape(short)}</a></li>')
        content = p.replace(m.group(0), "", 1)
        appendix = web and n > 1 and strip_tags(name).startswith("부록")
        if appendix:
            secs.append(f'<section class="sec appendix" id="s{n}">\n{h2}\n<details><summary aria-label="부록 펼치기"></summary>\n{content}\n</details>\n</section>')
        else:
            secs.append(f'<section class="sec{" lead" if n == 1 else ""}" id="s{n}">\n{h2}\n{content}\n</section>')

    rtype = meta.get("type", "")
    audience = meta.get("audience", "internal")
    badge = '<span class="badge internal">내부용</span>' if audience != "external" else '<span class="badge">대외</span>'
    label = html.escape(meta.get("label") or TYPE_LABEL.get(rtype, "보고"))
    meta_line = " · ".join(x for x in [meta.get("date", ""), meta.get("author", ""), meta.get("basis", "")] if x)
    toc_html = "".join(toc)
    # web 넓은 화면: 날짜·작성·기준을 이름 붙여 나눠 보인다(가운뎃점으로 이은 한 줄 대신)
    meta_grid = "" if not web else '<dl class="meta-grid">' + "".join(
        f"<div><dt>{k}</dt><dd>{html.escape(meta[f])}</dd></div>"
        for k, f in (("날짜", "date"), ("작성", "author"), ("기준", "basis")) if meta.get(f)) + "</dl>"
    side = f'<nav class="side" aria-label="목차"><p>목차</p><ol>{toc_html}</ol></nav>' if web else ""
    footer = f'<footer>{html.escape(meta["footer"])}</footer>' if meta.get("footer") else ""
    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="generator" content="ko-report">
<meta name="ko-report-type" content="{html.escape(rtype)}">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&display=swap">
<style>
{CSS}
</style>
</head>
<body class="layout-{"web" if web else "doc"} type-{html.escape(rtype or "none")}">
<div class="wrap">
  {side}
  <main>
    <header class="doc">
      <p class="eyebrow">{label} {badge}</p>
      <h1>{html.escape(title)}</h1>
      {f'<p class="meta">{html.escape(meta_line)}</p>' if meta_line else ""}
      {meta_grid}
      <nav class="toc" aria-label="목차"><p>목차</p><ol>{toc_html}</ol></nav>
      {pre}
    </header>
{chr(10).join(secs)}
    {footer}
  </main>
</div>
<script>
{JS}</script>
</body>
</html>
"""
    out.write_text(page, encoding="utf-8")
    print(f"OK {out} ({out.stat().st_size // 1024}KB, 절 {n}개{', web' if web else ''})")
    for e in ERRORS:
        print(f"ERROR {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    ap.add_argument("--layout", choices=["doc", "web"], help="front matter 의 layout 을 이번 빌드만 바꾼다")
    a = ap.parse_args()
    src = Path(a.src)
    out = Path(a.out) if a.out else src.with_suffix(".html")
    build(src, out, a.layout)
    if ERRORS:
        sys.exit(1)


if __name__ == "__main__":
    main()
