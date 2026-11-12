"""기준값 선택 (DESIGN 5장, D-03).

각 (회계연도, 계정)의 기준값 = 그 회계연도를 담고 있는 가장 최근 사업보고서의 값.
후보 순서: (Y+2 보고서, 전전기) → (Y+1 보고서, 전기) → (Y 보고서, 당기). 수집된 보고서만 대상.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from .models import PERIOD_LABEL_KO, AccountKey, Issue, RawLine, RestatementEntry, SelectedValue
from .normalize import line_key
from .restatement import compare_candidates

STATEMENT_SJ = ("BS", "IS", "CIS", "CF")  # SCE 는 Raw Data 만 (D-11)


class AccountMeta(BaseModel):
    key: AccountKey
    account_id: str
    account_nm: str            # 가장 최근 보고서의 계정명
    sj_nm: str = ""
    ord: Optional[int] = None  # 가장 최근 보고서의 표시순서
    latest_bsns_year: int
    names_seen: list[str] = Field(default_factory=list)


class SelectionResult(BaseModel):
    cells: dict[str, dict[int, SelectedValue]] = Field(default_factory=dict)  # key_id → {fy: cell}
    meta: dict[str, AccountMeta] = Field(default_factory=dict)
    restatements: list[RestatementEntry] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


def key_id(k: AccountKey) -> str:
    return f"{k.sj_div}|{'S' if k.standard else 'N'}|{k.ident}"


def candidate_order(fiscal_year: int, report_years: Iterable[int]) -> list[tuple[int, str]]:
    avail = set(report_years)
    order = [(fiscal_year + 2, "bfefrmtrm"), (fiscal_year + 1, "frmtrm"), (fiscal_year, "thstrm")]
    return [(by, p) for by, p in order if by in avail]


def select_values(reports: dict[int, list[RawLine]], target_years: list[int]) -> SelectionResult:
    res = SelectionResult()
    # (bsns_year, period, key) → 첫 RawLine (중복은 normalize.review_issues_for_report 에서 보고)
    index: dict[tuple[int, str, AccountKey], RawLine] = {}
    keys_in_report: dict[int, set[AccountKey]] = defaultdict(set)
    for by, lines in reports.items():
        for ln in lines:
            if ln.sj_div not in STATEMENT_SJ:
                continue
            k = line_key(ln)
            keys_in_report[by].add(k)
            index.setdefault((by, ln.period, k), ln)
            kid = key_id(k)
            m = res.meta.get(kid)
            if m is None or by > m.latest_bsns_year:
                names = m.names_seen if m else []
                res.meta[kid] = AccountMeta(key=k, account_id=ln.account_id, account_nm=ln.account_nm,
                                            sj_nm=ln.sj_nm, ord=ln.ord, latest_bsns_year=by, names_seen=names)
            if ln.account_nm not in res.meta[kid].names_seen:
                res.meta[kid].names_seen.append(ln.account_nm)

    for kid, meta in res.meta.items():
        k = meta.key
        if not k.standard:
            pass  # 비표준은 normalize 단계에서 확인 대상으로 보고
        elif len(meta.names_seen) > 1:
            res.issues.append(Issue(code="ACCOUNT_NAME_CHANGED",
                                    message=f"[{k.sj_div}] {k.ident}: 보고서 간 계정명 상이 {meta.names_seen}",
                                    context={"key": kid, "names": meta.names_seen}))
        for fy in target_years:
            order = candidate_order(fy, reports.keys())
            if not order:
                continue
            found = [(by, p, index.get((by, p, k))) for by, p in order]
            if all(ln is None for _, _, ln in found):
                continue  # 이 회계연도에 이 계정이 어디에도 없음 → 셀 없음
            primary_by, primary_p = order[0]
            latest_missing = k not in keys_in_report[primary_by]
            chosen = next(((by, p, ln) for by, p, ln in found if ln is not None and ln.amount is not None), None)
            if chosen is None:
                # 행은 있으나 모든 후보 값 없음
                by, p, ln = next((x for x in found if x[2] is not None))
                cell = SelectedValue(key=k, fiscal_year=fy, amount=None, source_rcept_no=ln.rcept_no,
                                     source_bsns_year=by, source_period=p, latest_missing=latest_missing)
            else:
                by, p, ln = chosen
                cell = SelectedValue(key=k, fiscal_year=fy, amount=ln.amount, source_rcept_no=ln.rcept_no,
                                     source_bsns_year=by, source_period=p,
                                     fallback=(by, p) != (primary_by, primary_p),
                                     latest_missing=latest_missing)
            cands = [{"bsns_year": by, "period": p, "rcept_no": ln.rcept_no, "amount": ln.amount}
                     for by, p, ln in found if ln is not None and ln.amount is not None]
            entry = compare_candidates(k, meta.account_nm, fy, cands)
            if entry:
                cell.restated = True
                res.restatements.append(entry)
            ctx = {"key": kid, "account_nm": meta.account_nm, "fiscal_year": fy,
                   "source": f"FY{cell.source_bsns_year} 보고서 {PERIOD_LABEL_KO.get(cell.source_period or '', '')}",
                   "rcept_no": cell.source_rcept_no}
            if cell.latest_missing:
                res.issues.append(Issue(code="LATEST_REPORT_MISSING",
                                        message=f"FY{fy} [{k.sj_div}] {meta.account_nm}: 1순위(FY{primary_by}) 보고서에 계정 없음",
                                        context=ctx))
            if cell.fallback:
                res.issues.append(Issue(code="FALLBACK_SOURCE",
                                        message=f"FY{fy} [{k.sj_div}] {meta.account_nm}: 1순위 값 없음 → 대체 출처 사용",
                                        context=ctx))
            res.cells.setdefault(kid, {})[fy] = cell
    return res
