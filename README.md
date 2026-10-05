# ko-report

한국어 업무 보고서를 **짧고, 늘 같은 모양으로** 만드는 Claude Code 스킬.

- 종류 6가지: 진행 · 완료 · QA(캡처·결함 카드) · 제안·결정 요청 · 분석 · 장애
- 원고는 마크다운, 결과는 디자인이 고정된 HTML 한 파일(이미지 포함). 같은 HTML 을 A4 PDF 로도 낸다(쪽 번호 포함)
- 검사 스크립트가 결론 우선·분량·AI 티 표현·대외 금칙(경로·커밋·IP·화살표)을 세고, 통과해야 끝난다
- 말투는 개조식 명사형("수정 완료")과 합쇼체("~습니다")만. 한글 윤문은 [im-not-ai](https://github.com/epoko77-ai/im-not-ai) 의 `/humanize` 를 산문 문단에만 쓴다

## 설치

```
/plugin marketplace add lehdqlsl/ko-report
/plugin install ko-report@ko-report
```

필요한 것: Python 3.10+, [uv](https://docs.astral.sh/uv/). 캡처를 쓰면 `uv run --with playwright python -m playwright install chromium` 한 번.

## 쓰는 법

"QA 보고서 만들어줘", "이번 주 진행 보고 정리해줘", "장애 보고서 써줘" 처럼 요청하면 스킬이 종류를 고르고 원고 → 검사 → 빌드까지 진행한다. 직접 돌릴 때:

```bash
python3 skills/report/scripts/check.py 원고.md
uv run --with markdown --with pillow python skills/report/scripts/build.py 원고.md -o 원고.html
uv run --with playwright --with pypdf --with pypdfium2 python skills/report/scripts/pdf.py 원고.html --pngs
```

원고 문법은 `skills/report/references/components.md`, 글쓰기 규칙은 `writing.md`.

## 참고한 것

- [tw93/Kami](https://github.com/tw93/Kami): 템플릿 복사·CSS 무접촉, 빈 자료 표시, AI 문서 실패 패턴
- [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins): 진행·장애·결정 요청 보고서 구성
- [garrytan/gstack](https://github.com/garrytan/gstack) qa-only: 결함 증거 규율
- [nicobailon/visual-explainer](https://github.com/nicobailon/visual-explainer): 결론 우선·간결성 규칙
- [epoko77-ai/im-not-ai](https://github.com/epoko77-ai/im-not-ai): 한국어 AI 티 패턴

## 라이선스

MIT
