"""회계 검증 V1~V6 (DESIGN 9.1). 계정 부재를 PASS 로 처리하지 않는다."""
from __future__ import annotations

from typing import Optional

from .models import Issue, ValidationResult, ValidationStatus

Values = dict[tuple[str, str], Optional[int]]  # (sj_div, item) → 금액


class _Getter:
    def __init__(self, values: Values):
        self.values = values
        self.missing: list[str] = []

    def get(self, item: str, sjs: tuple[str, ...]) -> Optional[int]:
        for sj in sjs:
            v = self.values.get((sj, item))
            if v is not None:
                return v
        self.missing.append(f"{'/'.join(sjs)}:{item}")
        return None

    def peek(self, item: str, sjs: tuple[str, ...]) -> Optional[int]:
        for sj in sjs:
            v = self.values.get((sj, item))
            if v is not None:
                return v
        return None


def _compare(rule_id: str, fy: int, basis: str, desc: str, lhs: Optional[int], rhs: Optional[int],
             missing: list[str], tolerance: int, absent_status: ValidationStatus, note: str = "") -> ValidationResult:
    if missing or lhs is None or rhs is None:
        return ValidationResult(rule_id=rule_id, fiscal_year=fy, basis=basis, status=absent_status,
                                description=desc, lhs=lhs, rhs=rhs, missing=missing,
                                note=note or "필요 계정 부재 — 판단 불가")
    diff = lhs - rhs
    status = ValidationStatus.PASS if abs(diff) <= tolerance else ValidationStatus.FAIL
    return ValidationResult(rule_id=rule_id, fiscal_year=fy, basis=basis, status=status, description=desc,
                            lhs=lhs, rhs=rhs, diff=diff, note=note)


def run_accounting_checks(values: Values, fiscal_year: int, basis: str, tolerance: int = 0) -> list[ValidationResult]:
    R, NA = ValidationStatus.REVIEW, ValidationStatus.NA
    out: list[ValidationResult] = []
    BS, ISX, CF = ("BS",), ("IS", "CIS"), ("CF",)

    g = _Getter(values)
    a, l, e = g.get("total_assets", BS), g.get("total_liabilities", BS), g.get("total_equity", BS)
    out.append(_compare("V1", fiscal_year, basis, "자산총계 = 부채총계 + 자본총계",
                        a, None if l is None or e is None else l + e, g.missing, tolerance, R))

    g = _Getter(values)
    e, p, n = g.get("total_equity", BS), g.get("equity_parent", BS), g.get("equity_nci", BS)
    out.append(_compare("V2", fiscal_year, basis, "자본총계 = 지배기업 소유주지분 + 비지배지분",
                        e, None if p is None or n is None else p + n, g.missing, tolerance, R))

    # V3: 같은 재무제표(IS 우선, 없으면 CIS) 안에서 비교
    v3 = None
    for sj in ("IS", "CIS"):
        g = _Getter(values)
        gp, rev, cos = g.get("gross_profit", (sj,)), g.get("revenue", (sj,)), g.get("cost_of_sales", (sj,))
        if not g.missing:
            v3 = _compare("V3", fiscal_year, basis, f"매출총이익 = 매출액 − 매출원가 ({sj})",
                          gp, rev - cos, [], tolerance, NA)
            break
    if v3 is None:
        g = _Getter(values)
        g.get("gross_profit", ISX); g.get("revenue", ISX); g.get("cost_of_sales", ISX)
        v3 = _compare("V3", fiscal_year, basis, "매출총이익 = 매출액 − 매출원가", None, None,
                      g.missing, tolerance, NA, note="매출총이익·매출액·매출원가가 한 재무제표에 모두 있지 않음")
    out.append(v3)

    # V4 (D-16): 환율효과 전 증감이 있으면 + 환율변동효과, 없으면 환율효과 포함 증감으로 비교
    g = _Getter(values)
    beg, end = g.get("cash_begin", CF), g.get("cash_end", CF)
    chg_before = g.peek("cash_change", CF)
    chg_total = g.peek("cash_change_total", CF)
    if chg_before is not None:
        fx = g.get("cash_fx_effect", CF)
        lhs = None if beg is None or fx is None else beg + chg_before + fx
        desc = "기초현금 + 현금증감(환율효과 전) + 환율변동효과 = 기말현금"
        note = "산식 A: 환율효과 전 증감 + 환율변동효과"
    elif chg_total is not None:
        lhs = None if beg is None else beg + chg_total
        desc = "기초현금 + 현금증감(환율효과 포함) = 기말현금"
        note = "산식 B: 환율효과 전 증감 계정 없음 → 환율효과 포함 증감 사용 (D-16)"
    else:
        g.missing.append("CF:cash_change|cash_change_total")
        lhs, desc, note = None, "기초현금 + 현금증감 (+ 환율변동효과) = 기말현금", ""
    out.append(_compare("V4", fiscal_year, basis, desc, lhs, end, g.missing, tolerance, R,
                        note=note if not g.missing else ""))

    g = _Getter(values)
    cf_end, bs_cash = g.get("cash_end", CF), g.get("cash_bs", BS)
    out.append(_compare("V5", fiscal_year, basis, "현금흐름표 기말현금 = 재무상태표 현금및현금성자산",
                        cf_end, bs_cash, g.missing, tolerance, R))

    g = _Getter(values)
    pi, pc = g.get("profit_is", ("IS",)), g.get("profit_cis", ("CIS",))
    out.append(_compare("V6", fiscal_year, basis, "손익계산서 당기순이익 = 포괄손익계산서 당기순이익",
                        pi, pc, g.missing, tolerance, NA,
                        note="" if not g.missing else "IS·CIS 중 하나에만 존재 — 비교 대상 아님"))
    return out


# ---- 원본 대사 R1~R4 (DESIGN 9.2) ---------------------------------------------

def _res(rule_id: str, fy: int, desc: str, ok: bool, lhs=None, rhs=None, note: str = "") -> ValidationResult:
    diff = (lhs - rhs) if isinstance(lhs, int) and isinstance(rhs, int) else None
    return ValidationResult(rule_id=rule_id, fiscal_year=fy, basis="reconciliation",
                            status=ValidationStatus.PASS if ok else ValidationStatus.FAIL,
                            description=desc, lhs=lhs, rhs=rhs, diff=diff, note=note)


def r1_statement_totals(report_lines: list, sheet_rows: dict[str, list[tuple[str, Optional[int]]]],
                        bsns_year: int, statements: tuple[str, ...] = ("BS", "IS", "CIS", "CF")) -> list[ValidationResult]:
    """R1: 보고서 당기 원본 행 수·금액 합계 = 시트에 기록된 해당 연도 행 수·합계.

    sheet_rows: sj_div → [(key_id, FY 금액)] — 저장된 시트에서 다시 읽은 값.
    """
    out = []
    for sj in statements:
        raw = [ln for ln in report_lines if ln.sj_div == sj and ln.period == "thstrm"]
        rows = sheet_rows.get(sj, [])
        raw_sum = sum(ln.amount for ln in raw if ln.amount is not None)
        sh_sum = sum(v for _, v in rows if v is not None)
        out.append(_res("R1", bsns_year, f"[{sj}] 원본 행 수 = 시트 행 수", len(raw) == len(rows), len(raw), len(rows),
                        note="원본 중복 키가 있으면 시트에서 1행으로 합쳐져 불일치" if len(raw) != len(rows) else ""))
        out.append(_res("R1", bsns_year, f"[{sj}] 원본 당기 금액 합계 = 시트 FY{bsns_year} 합계", raw_sum == sh_sum, raw_sum, sh_sum))
    return out


def r2_cell_trace(sheet_cells: list[dict], lookup) -> ValidationResult:
    """R2: 저장된 Excel 의 모든 금액 셀 = 출처 RawLine 값.

    sheet_cells: [{sheet, row, key_id, fiscal_year, value, source_bsns_year, source_period}]
    lookup(key_id, source_bsns_year, source_period) → RawLine | None
    """
    bad = []
    for c in sheet_cells:
        ln = lookup(c["key_id"], c["source_bsns_year"], c["source_period"])
        expected = None if ln is None else ln.amount
        if ln is None or c["value"] != expected:
            bad.append({**c, "expected": expected})
    fy = max((c["fiscal_year"] for c in sheet_cells), default=0)
    note = "" if not bad else "불일치 예: " + "; ".join(
        f"{b['sheet']}!행{b['row']} {b['key_id']} FY{b['fiscal_year']} 시트={b['value']} 원본={b['expected']}" for b in bad[:5])
    return _res("R2", fy, f"저장된 Excel 금액 셀 {len(sheet_cells)}개 = 출처 원본 값", not bad,
                len(sheet_cells) - len(bad), len(sheet_cells), note=note)


def r3_lineage(lines: list, fy: int) -> list[ValidationResult]:
    missing = sum(1 for ln in lines if not ln.rcept_no)
    currencies = sorted({ln.currency for ln in lines})
    return [
        _res("R3", fy, "모든 원본 행에 접수번호 존재", missing == 0, len(lines) - missing, len(lines)),
        _res("R3", fy, "통화 단일성", len(currencies) == 1, note=f"통화: {currencies}"),
    ]


def r4_result(fy: int, issue: Optional[Issue], api_rcept: Optional[str], final_rcept: Optional[str]) -> ValidationResult:
    return _res("R4", fy, "재무제표 API 접수번호 = 공시검색 최종본 접수번호", issue is None,
                note=f"API={api_rcept} / 공시검색 최종={final_rcept}" + (f" — {issue.message}" if issue else ""))


def critical_missing_issues(results: list[ValidationResult]) -> list[Issue]:
    """REVIEW 상태(필수 계정 부재) → CRITICAL_MISSING_REVIEW 확인 대상."""
    return [
        Issue(code="CRITICAL_MISSING_REVIEW",
              message=f"FY{r.fiscal_year} {r.rule_id} ({r.basis}) 판단 불가 — 부재: {', '.join(r.missing)}",
              context={"rule_id": r.rule_id, "fiscal_year": r.fiscal_year, "basis": r.basis, "missing": r.missing})
        for r in results if r.status == ValidationStatus.REVIEW and r.missing
    ]
