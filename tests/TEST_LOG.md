# TEST_LOG — A-1 Financial Data Engine

실행 환경: Windows 11, Python 3.12.10 (`.venv`), requests 2.34.2 · pydantic 2.14.3 · openpyxl 3.1.5 · PyYAML 6.0.3 · pytest 9.2.0

---

## 2026-11-12 T0 — 단위 테스트 (네트워크 없음, 합성 데이터만)

명령
```
.venv\Scripts\python -m pytest -q
```
출력
```
......................................................................   [100%]
70 passed in 0.39s
```

범위 (DESIGN 11장 T0 항목 대응)
| 항목 | 테스트 파일 |
|---|---|
| 금액 파싱 (정수·쉼표·음수, 공란/`-` → None, 소수·괄호 → 오류) | `test_amount.py`, `test_normalize.py` |
| 분류 (표준/비표준 키, 매핑 상태 정확 일치, 중복 키, 핵심 항목 모호) | `test_normalize.py` |
| 기준값 선택 (후보 순서 = 5장 표, 대체 출처, 최신 미존재, 재작성 로그, SCE 제외) | `test_selection.py` |
| 검증 규칙 V1~V6 (PASS/FAIL/REVIEW/N/A, 허용오차, 부재를 PASS 로 처리하지 않음) | `test_validation.py` |
| CFS 미존재 (013 → 검토 대상, OFS 호출 0회) · 정정공시 대사 R4 · 공시목록 페이지 처리 | `test_collector.py` |
| 회사 식별 (정확 일치 상장사 1건만 확정, 복수/0건/비상장 중단, 캐시) | `test_corp_registry.py` |
| 키 마스킹 (쿼리·문자열·로그·예외 체인), 상태코드별 중단, 5xx/네트워크만 3회 재시도, 저장 파일 키 0건 | `test_security.py` |

발견 오류: 없음 (첫 실행 통과).

---

## 2026-11-12 T1 — "삼성전자" 고유번호 확인 (실제 OpenDART 호출)

명령
```
.venv\Scripts\python -m fde.cli resolve 삼성전자
```
출력
```
고유번호 목록: 119,989건 (새로 내려받음: data\cache\corpCode.xml)
[확정] 정확 일치 상장사 1건
  corp_name   : 삼성전자
  corp_eng    : SAMSUNG ELECTRONICS CO,.LTD
  corp_code   : 00126380
  stock_code  : 005930
  modify_date : 20251201
exit=0
```

보조 확인 — 정확 일치가 상장사가 아닌 경우 중단 (D-07)
```
.venv\Scripts\python -m fde.cli resolve 삼성
[중단] 정확히 일치하는 회사가 있으나 상장사가 아닙니다: '삼성'
  - 삼성 | corp_code=00893765 | stock_code=(비상장)
  - 삼성수산 | corp_code=00339063 | stock_code=052560
  ... (후보 최대 20건)
exit=2
```

보안 점검: `data/`, `logs/`, `src/`, `tests/`, `config/` 에서 인증키 문자열 검색 → 0건.

관찰 사항 (사용자 확인 필요)
- corpCode.xml 의 `stock_code` 가 상장폐지된 회사에도 남아 있는지 미확인 (후보 목록에 현재 상장 여부를 확인하지 않은 회사가 종목코드와 함께 나옴). 현재 구현은 "종목코드 보유 = 상장사"로 판정하므로, 동명 회사 중 하나가 상장폐지 회사이면 복수 건으로 중단된다 (안전한 방향의 오류). 삼성전자 확정에는 영향 없음.

---

## 2026-11-12 T2 — FY2025 단일연도 수집 → Excel → 검증 (실제 OpenDART 호출)

추가 개발: `excel_writer.py`(6개 시트, 저장 후 재판독), `validation.py` R1~R4, `cli.py run`. 단위 테스트 73건 통과 (`test_excel_recon.py` 3건 추가).

명령
```
.venv\Scripts\python -m fde.cli run
```
출력
```
[1] 회사 확정: 삼성전자 corp_code=00126380 stock_code=005930
[2-3] FY2025: 공시검색 1건 (최종 20260310002820), 재무제표 status=000 rcept_no=20260310002820 행=229
[4-6] RawLine 687행, 계정 124개(SCE 제외), 금액 셀 124개
[8] 회계 검증 (selected / report_thstrm 두 기준 모두 동일)
   V1 PASS 차이=0 | V2 PASS 차이=0 | V3 PASS 차이=0 (IS)
   V4 REVIEW 부재=['CF:cash_change']
   V5 PASS 차이=0 | V6 PASS 차이=0
    원본 대사
   R4 PASS API=20260310002820 / 공시검색 최종=20260310002820
   R3 PASS 접수번호 687/687, 통화 ['KRW']
   R1 PASS BS 52/52, IS 17/17, CIS 13/13, CF 42/42 (행 수·당기 금액 합계 모두 일치)
   R2 PASS 저장된 Excel 금액 셀 124/124
    확인 대상: {'NONSTANDARD_ACCOUNT': 5, 'CRITICAL_MISSING_REVIEW': 2}
[보안] output/logs/data 인증키 문자열 검색: 0건
[9] Excel: output\00126380_삼성전자_A1_FY2025_20261112_111014.xlsx
```

사후 점검 (별도 스크립트, 최종 저장 파일 기준)
- 6개 시트 확인. 최종 파일 금액 셀 124개를 원본 JSON 과 직접 대조 → 불일치 0.
- V4 진단: `ifrs-full_IncreaseDecreaseInCashAndCashEquivalents`(현금및현금성자산의 증가(감소))로 계산하면 `기초 + 증가(감소) − 기말 = 0`, 여기에 환율효과를 더하면 차이 825,897,000,000. `영업+투자+재무+환율효과 − 증가(감소) = 0`. → 이 계정은 환율효과 **포함** 증감으로 보인다. V4 산식·매핑 변경은 사용자 판단 대상 (미반영).
- 비표준계정 5건: BS 단기차입금, CF 매각예정분류·단기상각후원가금융자산의 순감소(증가)·단기당기손익-공정가치금융자산의 순감소(증가)·단기금융상품의 순감소(증가).
- IS/CIS 행에 `thstrm_add_amount` 필드가 있으나 FY2025 사업보고서에서는 30행 모두 공란. RawLine 에는 펼치지 않고 원본 JSON 에 보존.
- IS 시트 금액 합계(R1)에는 주당이익(원/주) 행이 함께 합산됨 — 대사용 합계일 뿐 의미 있는 금액 아님.

사용자 스팟체크: Validation ⑤ 15개 계정 — 대기 중.

---

## 2026-11-12 T2 재실행 — V4 산식 변경 (D-16, 사용자 승인)

변경
- `docs/DESIGN.md`: D-16 추가, 9.1 V4 규칙 갱신 (개발 전 반영).
- `config/account_map.csv`: `CF, ifrs-full_IncreaseDecreaseInCashAndCashEquivalents → cash_change_total` 추가.
- `validation.py` V4: 환율효과 전 증감이 있으면 산식 A(기초 + 증감 + 환율변동효과), 없으면 산식 B(기초 + 환율효과 포함 증감). 둘 다 있으면 A. 산식 A에서 환율변동효과가 없으면 REVIEW. 사용한 산식은 비고에 표시.
- 단위 테스트: V4 테스트 5건으로 교체 → `77 passed`.

명령
```
.venv\Scripts\python -m pytest -q      → 77 passed
.venv\Scripts\python -m fde.cli run
```
출력 (요약)
```
V1~V6 FY2025 selected / report_thstrm 모두 PASS, 차이=0
   V4: 기초현금 + 현금증감(환율효과 포함) = 기말현금 (산식 B)
R1~R4 모두 PASS (R2 124/124)
확인 대상: {'NONSTANDARD_ACCOUNT': 5}
[보안] output/logs/data 인증키 문자열 검색: 0건
[9] Excel: output\00126380_삼성전자_A1_FY2025_20261112_111338.xlsx
```

사용자 스팟체크: Validation ⑤ 15개 계정 — 대기 중.
