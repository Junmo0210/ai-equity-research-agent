# AI Equity Research Agent

AI를 활용한 기업분석 및 가치평가 자동화 시스템을 단계적으로 개발하는 프로젝트입니다.

| 단계 | 내용 | 상태 |
|---|---|---|
| **A-1** | **Financial Data Engine** — OpenDART 사업보고서 연결재무제표 수집·출처 보존·Excel 정리·검증 | 설계 승인, 개발 전 |
| A | 기업분석 자동화 | 예정 |
| B | 다중 에이전트 기반 AI 애널리스트 | 예정 |
| C | 종합 투자 리서치 플랫폼 | 예정 |

- 설계 문서: [`docs/DESIGN.md`](docs/DESIGN.md)
- AI 개발 세션 작업 원칙: [`CLAUDE.md`](CLAUDE.md)
- 이전 프로토타입 (참고용, 유지보수 안 함): [equity-research-ai](https://github.com/Junmo0210/equity-research-ai)

## 사전 준비 (Windows)

1. Python 3.11 이상, Git 설치
2. OpenDART 인증키 발급: https://opendart.fss.or.kr (무료)
3. 인증키를 환경변수로 설정 (명령 프롬프트 실행 후 **새 창을 열어야** 적용)

```
setx DART_API_KEY "발급받은키"
```

인증키는 소스코드, 출력 파일, 로그, 채팅에 남기지 마세요.

실행 방법은 개발 완료 후 추가됩니다.
