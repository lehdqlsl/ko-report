"""보고서 원고(md) 게이트 — 짧고, 사실 중심이고, 대외 규칙을 지켰는지 기계로 센다.

사용: python3 check.py 원고.md [--external]
결과: ERROR 가 하나라도 있으면 exit 1. WARN 은 볼 곳, GAP 은 [확인 필요] 자리.
규칙 출처: references/writing.md. 기준값은 아래 LIMITS·TYPE_LIMITS 에서 바꾼다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TYPES = ("progress", "completion", "qa", "proposal", "analysis", "incident", "policy", "spec")
LIMITS = {
    "lead_chars": 450,        # 첫 절(결론) 산문 글자 수
    "lead_bullets": 5,        # 첫 절 목록 항목 수
    "section_chars": 700,     # 절 하나의 산문 글자 수(표·블록 제외). 넘으면 WARN
    "section_chars_err": 1200,
    "total_chars": 6000,      # 문서 전체 산문. 넘으면 WARN
    "sentence_chars": 110,    # 한 문장 길이. 넘으면 WARN
    "bold_per_section": 4,
}
# 종류별 분량 상한(WARN): 보이는 글자 전체(표·상자·카드 포함), 표 행 수. pdf.py 의 쪽 예산과 짝이다.
TYPE_LIMITS = {
    "progress": (4500, 45), "completion": (4500, 45), "incident": (4000, 40),
    "proposal": (7000, 70), "analysis": (7000, 70), "qa": (9000, 90),
    "policy": (16000, 170), "spec": (20000, 200),
}

# AI 티·군말 — im-not-ai 진단 상위 패턴 중 보고서에 자주 나오는 것만 추렸다(전수 윤문은 /humanize).
# 이모지: 그림 문자 영역과 기본이 그림으로 보이는 기호만. ★ ○ △ ☆ ● ■ ✓ 같은 글자 기호는 잡지 않는다.
EMOJI = ("[\U0001F000-\U0001FAFF⌚⌛⏩-⏳⏸-⏺☔☕♈-♓♿⚓"
         "⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲-⛵⛺⛽✅✨"
         "❌❎❓-❕❗➕-➗➰➿⬛⬜⭐⭕️]")
AI_TELLS = [
    (r"를 통해|을 통해", "'~를 통해' → 수단을 동사로(로, 해서)"),
    (r"다양한", "'다양한' → 무엇이 몇 개인지"),
    (r"효율적으로|효과적으로", "형용 부사 → 수치나 구체 동작"),
    (r"중요합니다|중요한 역할", "평가어 → 왜 중요한지 사실 한 줄"),
    (r"할 수 있습니다\.", "'~할 수 있습니다' 남발 → 합니다/됩니다"),
    (r"라고 할 수 있|것으로 보입니다", "단정 회피 → 근거와 함께 단정하거나 '추정'이라고 명시"),
    (r"다음과 같습니다|살펴보겠습니다|알아보겠습니다", "안내 문장 → 바로 내용"),
    (r"결론적으로|요약하자면|종합하면", "결론 표지 → 결론 절이 따로 있으니 삭제"),
    (r"뿐만 아니라|그뿐만 아니라", "나열 접속 → 문장 분리"),
    (r"^\s*또한,", "'또한,' 문두 → 삭제"),
    (r"본질적으로|근본적으로", "추상어 → 삭제"),
    (r"최적화|고도화|극대화|혁신", "기획서 어휘 → 실제로 바뀐 것"),
    (r"—|――", "긴 대시 → 쉼표·괄호·문장 분리"),
    (EMOJI, "이모지 → 상태 딱지(표 칸 상태 단어)"),
]
# 대외 금칙 중 문체 규칙(화살표·절 기호)은 따옴표·코드 안의 글자(화면 문구 인용)에는 적용하지 않는다.
# 경로·커밋·IP·PR 번호는 따옴표 안이어도 막는다.
STYLE_ONLY = {"화살표", "절 기호"}
EXTERNAL = [
    (r"/Users/|/home/|[A-Za-z]:\\\\", "로컬 경로"),
    (r"\b[\w./-]+\.(py|md|ts|tsx|sql|sh|yml|yaml|json)\b", "파일 경로·이름"),
    (r"\b[0-9a-f]{7,40}\b", "커밋 해시"),
    (r"\b\d{1,3}(\.\d{1,3}){3}\b", "IP 주소"),
    (r"\b(PR|pr) ?#\d+|#\d{2,}", "PR 번호"),
    (r"→|▶", "화살표"),
    (r"§", "절 기호"),
]
PLACEHOLDER = r"\[확인 필요[^\]]*\]|\[DATA NEEDED[^\]]*\]|\bTODO\b|\bTBD\b"
SKELETON = r"<[가-힣][^<>\n]{0,80}>|\(없으면 지움\)"
CARD_KEYS = ("관찰", "기대", "증거", "조치", "원인", "재현", "추정")


def strip_front(text: str) -> tuple[dict, str]:
    meta = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        for line in text[4:end].splitlines():
            if ":" in line and not line.strip().startswith("#"):
                k, v = line.split(":", 1)
                meta[k.strip()] = re.sub(r"\s{2,}#\s.*$", "", v).strip()
        text = text[end + 4:]
    return meta, text


def prose_of(block: str) -> str:
    """산문만 남긴다: 표, ::: 블록, 코드, 이미지, 제목 줄 제거."""
    block = re.sub(r"^:::.*?^:::[ \t]*$", "", block, flags=re.S | re.M)
    block = re.sub(r"^```.*?^```", "", block, flags=re.S | re.M)
    lines = [l for l in block.splitlines() if not l.lstrip().startswith(("|", "#", "!["))]
    return "\n".join(lines)


def visible_of(block: str) -> str:
    """화면에 보이는 글자: 표·상자·카드·코드 안 글자까지. 문법 기호와 이미지 경로는 뺀다."""
    out = []
    for l in block.splitlines():
        s = l.strip()
        if re.fullmatch(r"\|?[\s:|-]+\|?", s) and "-" in s:      # 표 구분선
            continue
        if s.startswith("```"):
            continue
        s = re.sub(r"^:::\w+", "", s)                            # 블록 이름(인자 글자는 남김)
        s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)
        s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
        s = re.sub(r"^#+\s|^[-*]\s|^\d+\.\s", "", s)
        s = re.sub(r"[|*`]", "", s)
        out.append(s)
    return "\n".join(out)


def table_rows(block: str) -> int:
    lines = [l.strip() for l in block.splitlines()]
    seps = sum(1 for l in lines if l.startswith("|") and re.fullmatch(r"\|[\s:|-]+\|?", l))
    return max(sum(1 for l in lines if l.startswith("|")) - 2 * seps, 0)


def card_rows(body: str) -> dict[str, str]:
    """결함 카드: 정해진 키로 시작하는 줄만 칸, 나머지 줄과 펜스 코드는 앞 칸에 붙는다."""
    rows: dict[str, str] = {}
    key, fence = None, None
    for line in body.splitlines():
        if fence:
            if key:
                rows[key] += "\n" + line
            if line.strip().startswith(fence):
                fence = None
            continue
        fm = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fm:
            fence = fm.group(1)
            if key:
                rows[key] += "\n" + line
            continue
        m = re.match(r"^\s*(" + "|".join(CARD_KEYS) + r")\s*[:：]\s?(.*)$", line)
        if m:
            key = m.group(1)
            rows[key] = m.group(2)
        elif key:
            rows[key] += "\n" + line
    return rows


def unquote(t: str) -> str:
    """문체 검사에서 뺄 부분(인용·코드)을 지운다: “…” "…" ‘…’ 「…」 `…` 와 펜스 코드."""
    t = re.sub(r"^```.*?^```", "", t, flags=re.S | re.M)
    # 지운 자리에 "인용"을 남겨 문장 끝 판정(평서형 '~다')이 앞말에 걸리지 않게 한다
    return re.sub(r"“[^”\n]*”|\"[^\"\n]*\"|‘[^’\n]*’|「[^」\n]*」|`[^`\n]*`", "인용", t)


def table_errors(text: str) -> list[str]:
    """표 행의 칸 수가 머리글보다 많으면 넘친 칸이 조용히 사라진다."""
    errs, lines = [], text.splitlines()
    for i, l in enumerate(lines):
        if i + 1 < len(lines) and l.lstrip().startswith("|") and re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lines[i + 1]) and "-" in lines[i + 1]:
            ncol = len(re.split(r"(?<!\\)\|", l.strip().strip("|")))
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                n = len(re.split(r"(?<!\\)\|", lines[j].strip().strip("|")))
                if n > ncol:
                    errs.append(f"표 행의 칸 {n}개 > 머리글 {ncol}개(넘친 칸은 사라짐): {lines[j].strip()[:40]}…")
                j += 1
    return errs


def sec_title(s: str) -> str:
    return s.splitlines()[0].split(" | ")[0].strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--external", action="store_true", help="대외 규칙까지 검사(front matter audience: external 이면 자동)")
    a = ap.parse_args()
    raw = Path(a.src).read_text(encoding="utf-8")
    meta, text = strip_front(raw)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)   # 틀의 안내 주석은 화면에 안 보인다
    rtype = meta.get("type", "")
    external = a.external or meta.get("audience") == "external"
    errs, warns, gaps = [], [], []

    for k in ("title", "type", "date"):
        if not meta.get(k):
            errs.append(f"front matter '{k}' 없음")
    if rtype and rtype not in TYPES:
        errs.append(f"type '{rtype}' 은 정해진 종류가 아님({' · '.join(TYPES)})")
    if meta.get("numbering", "on") not in ("on", "off"):
        errs.append(f"numbering '{meta.get('numbering')}' 은 on 또는 off")
    if meta.get("layout", "doc") not in ("doc", "web"):
        errs.append(f"layout '{meta.get('layout')}' 은 doc 또는 web")
    if meta.get("evidence") and (rtype != "qa" or meta["evidence"] != "code"):
        errs.append("evidence 는 QA 보고에서 'evidence: code' 로만 쓴다")
    if not meta.get("basis"):
        warns.append("basis(기준: 어느 서버·판·시점) 없음 — 수치가 있으면 반드시 적는다")

    for m in re.finditer(SKELETON, re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)):
        errs.append(f"틀 자리표시자가 남음: {m.group(0)[:40]}")

    sections = re.split(r"(?m)^## ", text)[1:]
    if not sections:
        errs.append("## 절이 없음")
    else:
        first_title = sec_title(sections[0])
        if not re.search(r"결론|요약|한 줄|결과", first_title):
            warns.append(f"첫 절 '{first_title}' — 결론을 맨 앞에 둔다(결론·요약)")
        lead = prose_of(sections[0].split("\n", 1)[1] if "\n" in sections[0] else "")
        lead_chars = len(re.sub(r"\s", "", lead))
        bullets = len(re.findall(r"(?m)^\s*[-*] ", lead))
        if lead_chars > LIMITS["lead_chars"]:
            errs.append(f"결론 절 산문 {lead_chars}자 > {LIMITS['lead_chars']}자 — 핵심만")
        if bullets > LIMITS["lead_bullets"]:
            errs.append(f"결론 절 항목 {bullets}개 > {LIMITS['lead_bullets']}개")

    # 정책·화면 설계는 절이 화면 묶음이라 길다. 분량·굵게 상한은 ### 소단위마다 센다.
    chunked = rtype in ("policy", "spec")
    units = []
    for s in sections:
        title = sec_title(s)
        body = s.split("\n", 1)[1] if "\n" in s else ""
        if chunked and re.search(r"(?m)^### ", body):
            head, *subs = re.split(r"(?m)^### ", body)
            units.append((title, head))
            units += [(f"{title} › {x.splitlines()[0].strip()}", x.split("\n", 1)[1] if "\n" in x else "") for x in subs]
        else:
            units.append((title, body))
    total = 0
    for title, body in units:
        pr = prose_of(body)
        c = len(re.sub(r"\s", "", pr))
        total += c
        if c > LIMITS["section_chars_err"]:
            errs.append(f"[{title}] 산문 {c}자 > {LIMITS['section_chars_err']}자 — 표로 바꾸거나 쪼갠다")
        elif c > LIMITS["section_chars"]:
            warns.append(f"[{title}] 산문 {c}자 > {LIMITS['section_chars']}자")
        b = len(re.findall(r"\*\*[^*]+\*\*", re.sub(r"^:::.*?^:::[ \t]*$", "", body, flags=re.S | re.M)))
        if b > LIMITS["bold_per_section"]:
            warns.append(f"[{title}] 굵게 {b}곳 > {LIMITS['bold_per_section']} — 강조는 절마다 몇 곳만")
        for sent in (x for ln in pr.splitlines() for x in re.split(r"(?<=[.!?])\s+", ln)):
            sc = len(sent.strip())
            if sc > LIMITS["sentence_chars"]:
                warns.append(f"[{title}] 긴 문장 {sc}자: {sent.strip()[:40]}…")
    if total > LIMITS["total_chars"]:
        warns.append(f"문서 산문 {total}자 > {LIMITS['total_chars']}자 — 분량을 줄이거나 부록으로")

    # 분량 상한은 본문만 센다(## 부록… 절은 뺀다). 결과 줄에는 부록 포함 전체도 보인다.
    body_only = "\n".join("## " + x for x in sections if not sec_title(x).startswith("부록"))
    visible = len(re.sub(r"\s", "", visible_of(body_only)))
    rows = table_rows(body_only)
    visible_all = len(re.sub(r"\s", "", visible_of(text)))
    errs += table_errors(text)
    vmax, rmax = TYPE_LIMITS.get(rtype, (7000, 70))
    if visible > vmax:
        warns.append(f"본문 보이는 글자 {visible:,}자 > {vmax:,}자({rtype or '기본'}) — 표·상자까지 합친 분량(부록 제외). 부록으로 빼거나 나눈다")
    if rows > rmax:
        warns.append(f"본문 표 {rows}행 > {rmax}행({rtype or '기본'}, 부록 제외) — 본문에는 요지만, 전체 목록은 부록으로")

    blocks = "\n".join(m.group(1) for m in re.finditer(r"^:::\w+[^\n]*\n(.*?)^:::", text, flags=re.S | re.M))
    body_all = unquote(prose_of(text) + "\n" + blocks + "\n" + "\n".join(l for l in text.splitlines() if l.lstrip().startswith("|")))
    for pat, why in AI_TELLS:
        for m in re.finditer(pat, body_all, flags=re.M):
            ctx = body_all[max(0, m.start() - 15): m.end() + 15].replace("\n", " ")
            warns.append(f"AI 티 ({why}): …{ctx}…")
    # 평서형 "~했다·~한다·~였다" — 보고서는 명사형 또는 합쇼체(references/writing.md 3)
    plain = []
    for line in re.sub(r"^```.*?^```", "", body_all, flags=re.S | re.M).splitlines():
        for cell in re.split(r"\|", line):
            for sent in re.split(r"(?<=[.!?])\s+", cell.strip()):
                sent = re.sub(r"^[-*]\s+|^\s*[^:：]{1,8}[:：]\s*", "", sent).strip()
                if re.search(r"(?<!니)(?<!습)다[.!]?$", sent) and not re.search(r"(?:니|습)다[.!]?$", sent):
                    plain.append(sent)
    if plain:
        warns.append(f"평서형 '~다' {len(plain)}곳 — 명사형(완료·필요) 또는 '~습니다'로: " + " / ".join(p[-24:] for p in plain[:4]))
    if external:
        for pat, why in EXTERNAL:
            for m in re.finditer(pat, unquote(text) if why in STYLE_ONLY else text):
                errs.append(f"대외 금지 — {why}: {m.group(0)}")
    for m in re.finditer(PLACEHOLDER, raw):     # front matter·표 칸 포함 전체
        gaps.append(m.group(0))

    if rtype == "qa":
        code_ev = meta.get("evidence") == "code"
        if not code_ev and not re.search(r"!\[[^\]]*\]\([^)]+\)", text):
            errs.append("QA 보고에 캡처가 하나도 없음 — 코드 검수라면 front matter 에 'evidence: code'")
        for d in re.finditer(r"^:::defect[ \t]*(.*?)\n(.*?)^:::[ \t]*$", text, flags=re.S | re.M):
            did = d.group(1).split("|")[0].strip()
            rows_ = card_rows(d.group(2))
            for need in ("관찰", "기대", "증거"):
                if not rows_.get(need, "").strip():
                    errs.append(f"결함 '{did}' 에 '{need}' 없음")
            ev = rows_.get("증거", "")
            if ev.strip() and not re.search(r"!\[|`", ev):
                (errs if code_ev else warns).append(
                    f"결함 '{did}' 증거에 캡처·코드가 없음 — 캡처 ![..](..) 또는 코드 `..`·```블록```")
    if rtype == "spec":
        for s in re.finditer(r"^:::(?:screen|scene)[ \t]*(.*?)\n(.*?)^:::[ \t]*$", text, flags=re.S | re.M):
            if not re.search(r"!\[", s.group(2)):
                warns.append(f"화면 '{s.group(1).strip()}' 에 그림 없음 — 없으면 이유를 '비고:' 에")
        if not re.search(r"!\[", text):
            warns.append("화면 설계 문서에 그림이 하나도 없음")

    for e in errs:
        print("ERROR", e)
    for w in warns:
        print("WARN ", w)
    for g in gaps:
        print("GAP  ", g)
    verdict = "READY" if not errs else "FAIL"
    print(f"{verdict} — 오류 {len(errs)} · 경고 {len(warns)} · 빈칸 {len(gaps)} · "
          f"산문 {total:,}자 · 보이는 글자 {visible:,}자 · 표 {rows}행 · 절 {len(sections)}개"
          + (f" (부록 포함 {visible_all:,}자)" if visible_all != visible else ""))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
