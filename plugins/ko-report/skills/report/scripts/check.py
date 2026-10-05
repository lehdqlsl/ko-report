"""보고서 원고(md) 게이트 — 짧고, 사실 중심이고, 대외 규칙을 지켰는지 기계로 센다.

사용: python3 check.py 원고.md [--external]
결과: ERROR 가 하나라도 있으면 exit 1. WARN 은 볼 곳, GAP 은 [확인 필요] 자리.
규칙 출처: references/writing.md. 기준값은 아래 LIMITS 한 곳에서 바꾼다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LIMITS = {
    "lead_chars": 450,        # 첫 절(결론) 산문 글자 수
    "lead_bullets": 5,        # 첫 절 목록 항목 수
    "section_chars": 700,     # 절 하나의 산문 글자 수(표·블록 제외). 넘으면 WARN
    "section_chars_err": 1200,
    "total_chars": 6000,      # 문서 전체 산문. 넘으면 WARN
    "sentence_chars": 110,    # 한 문장 길이. 넘으면 WARN
    "bold_per_section": 4,
}

# AI 티·군말 — im-not-ai 진단 상위 패턴 중 보고서에 자주 나오는 것만 추렸다(전수 윤문은 /humanize).
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
    (r"[\U0001F300-\U0001FAFF☀-➿]", "이모지 → 상태 딱지(표 칸 상태 단어)"),
]
EXTERNAL = [
    (r"/Users/|/home/|[A-Za-z]:\\\\", "로컬 경로"),
    (r"\b[\w./-]+\.(py|md|ts|tsx|sql|sh|yml|yaml|json)\b", "파일 경로·이름"),
    (r"\b[0-9a-f]{7,40}\b", "커밋 해시"),
    (r"\b\d{1,3}(\.\d{1,3}){3}\b", "IP 주소"),
    (r"\b(PR|pr) ?#\d+|#\d{2,}", "PR 번호"),
    (r"→|▶", "화살표"),
    (r"§", "절 기호"),
]
PLACEHOLDER = r"\[확인 필요[^\]]*\]|\[DATA NEEDED[^\]]*\]|TODO|TBD"


def strip_front(text: str) -> tuple[dict, str]:
    meta = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        for line in text[4:end].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        text = text[end + 4:]
    return meta, text


def prose_of(block: str) -> str:
    """산문만 남긴다: 표, ::: 블록, 코드, 이미지, 제목 줄 제거."""
    block = re.sub(r"^:::.*?^:::[ \t]*$", "", block, flags=re.S | re.M)
    block = re.sub(r"^```.*?^```", "", block, flags=re.S | re.M)
    lines = [l for l in block.splitlines() if not l.lstrip().startswith(("|", "#", "!["))]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--external", action="store_true", help="대외 규칙까지 검사(front matter audience: external 이면 자동)")
    a = ap.parse_args()
    meta, text = strip_front(Path(a.src).read_text(encoding="utf-8"))
    external = a.external or meta.get("audience") == "external"
    errs, warns, gaps = [], [], []

    for k in ("title", "type", "date"):
        if not meta.get(k):
            errs.append(f"front matter '{k}' 없음")
    if meta.get("type") not in (None, "", "progress", "completion", "qa", "proposal", "analysis", "incident", "general"):
        errs.append(f"type '{meta.get('type')}' 은 정해진 종류가 아님")
    if not meta.get("basis"):
        warns.append("basis(기준: 어느 서버·판·시점) 없음 — 수치가 있으면 반드시 적는다")

    sections = re.split(r"(?m)^## ", text)[1:]
    if not sections:
        errs.append("## 절이 없음")
    else:
        first_title = sections[0].splitlines()[0]
        if not re.search(r"결론|요약|한 줄|결과", first_title):
            warns.append(f"첫 절 '{first_title}' — 결론을 맨 앞에 둔다(결론·요약)")
        lead = prose_of(sections[0].split("\n", 1)[1] if "\n" in sections[0] else "")
        lead_chars = len(re.sub(r"\s", "", lead))
        bullets = len(re.findall(r"(?m)^\s*[-*] ", lead))
        if lead_chars > LIMITS["lead_chars"]:
            errs.append(f"결론 절 산문 {lead_chars}자 > {LIMITS['lead_chars']}자 — 핵심만")
        if bullets > LIMITS["lead_bullets"]:
            errs.append(f"결론 절 항목 {bullets}개 > {LIMITS['lead_bullets']}개")

    total = 0
    for s in sections:
        title = s.splitlines()[0].strip()
        body = s.split("\n", 1)[1] if "\n" in s else ""
        pr = prose_of(body)
        c = len(re.sub(r"\s", "", pr))
        total += c
        if c > LIMITS["section_chars_err"]:
            errs.append(f"[{title}] 산문 {c}자 > {LIMITS['section_chars_err']}자 — 표로 바꾸거나 쪼갠다")
        elif c > LIMITS["section_chars"]:
            warns.append(f"[{title}] 산문 {c}자 > {LIMITS['section_chars']}자")
        b = len(re.findall(r"\*\*[^*]+\*\*", body))
        if b > LIMITS["bold_per_section"]:
            warns.append(f"[{title}] 굵게 {b}곳 > {LIMITS['bold_per_section']} — 강조는 절마다 몇 곳만")
        for sent in (x for ln in pr.splitlines() for x in re.split(r"(?<=[.!?])\s+", ln)):
            sc = len(sent.strip())
            if sc > LIMITS["sentence_chars"]:
                warns.append(f"[{title}] 긴 문장 {sc}자: {sent.strip()[:40]}…")
    if total > LIMITS["total_chars"]:
        warns.append(f"문서 산문 {total}자 > {LIMITS['total_chars']}자 — 분량을 줄이거나 부록으로")

    blocks = "\n".join(m.group(1) for m in re.finditer(r"^:::\w+[^\n]*\n(.*?)^:::", text, flags=re.S | re.M))
    body_all = prose_of(text) + "\n" + blocks + "\n" + "\n".join(l for l in text.splitlines() if l.lstrip().startswith("|"))
    for pat, why in AI_TELLS:
        for m in re.finditer(pat, body_all, flags=re.M):
            line = body_all[: m.start()].count("\n") + 1
            ctx = body_all[max(0, m.start() - 15): m.end() + 15].replace("\n", " ")
            warns.append(f"AI 티 ({why}): …{ctx}…")
    # 평서형 "~했다·~한다·~였다" — 보고서는 명사형 또는 합쇼체(references/writing.md 3)
    plain = []
    for line in body_all.splitlines():
        for cell in re.split(r"\|", line):
            for sent in re.split(r"(?<=[.!?])\s+", cell.strip()):
                sent = re.sub(r"^[-*]\s+|^\s*[^:：]{1,8}[:：]\s*", "", sent).strip()
                if re.search(r"(?<!니)(?<!습)다[.!]?$", sent) and not re.search(r"(?:니|습)다[.!]?$", sent):
                    plain.append(sent)
    if plain:
        warns.append(f"평서형 '~다' {len(plain)}곳 — 명사형(완료·필요) 또는 '~습니다'로: " + " / ".join(p[-24:] for p in plain[:4]))
    if external:
        full = text
        for pat, why in EXTERNAL:
            for m in re.finditer(pat, full):
                errs.append(f"대외 금지 — {why}: {m.group(0)}")
    for m in re.finditer(PLACEHOLDER, text):
        gaps.append(m.group(0))

    if meta.get("type") == "qa":
        if not re.search(r"!\[[^\]]*\]\([^)]+\)", text):
            errs.append("QA 보고에 캡처가 하나도 없음")
        for d in re.finditer(r"^:::defect[ \t]*(.*?)\n(.*?)^:::", text, flags=re.S | re.M):
            body = d.group(2)
            for need in ("관찰", "기대", "증거"):
                if not re.search(rf"^\s*{need}\s*[:：]", body, flags=re.M):
                    errs.append(f"결함 '{d.group(1).strip()}' 에 '{need}' 없음")

    for e in errs:
        print("ERROR", e)
    for w in warns:
        print("WARN ", w)
    for g in gaps:
        print("GAP  ", g)
    verdict = "READY" if not errs else "FAIL"
    print(f"{verdict} — 오류 {len(errs)} · 경고 {len(warns)} · 빈칸 {len(gaps)} · 산문 {total}자 · 절 {len(sections)}개")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
