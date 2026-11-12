"""연도별 수집, CFS 정책, 정정공시 대사 (DESIGN 6장 [2]~[4], D-02·D-06)."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from .dart_client import STATUS_NO_DATA, save_raw_json
from .models import Filing, Issue, Severity, StatementFetch
from .normalize import expand_rows

_FY_RE = re.compile(r"\((\d{4})\.(\d{2})\)")
ANNUAL_REPORT_NAME = "사업보고서"


def parse_report_fiscal_year(report_nm: str) -> Optional[int]:
    m = _FY_RE.search(report_nm or "")
    return int(m.group(1)) if m else None


def fetch_filings(client, corp_code: str, fiscal_year: int, *, last_reprt_at: str = "N",
                  today: Optional[date] = None, page_count: int = 100) -> tuple[list[Filing], dict]:
    """공시검색: 해당 사업연도 사업보고서(정정 포함) 목록. (filings, 조회조건) 반환."""
    today = today or date.today()
    end = min(today, date(fiscal_year + 2, 12, 31))
    params = {
        "corp_code": corp_code,
        "bgn_de": f"{fiscal_year + 1}0101",
        "end_de": end.strftime("%Y%m%d"),
        "pblntf_ty": "A",
        "pblntf_detail_ty": "A001",
        "last_reprt_at": last_reprt_at,
        "page_count": page_count,
    }
    filings: list[Filing] = []
    page = 1
    while True:
        status, data = client.get_json("list.json", {**params, "page_no": page})
        if status == STATUS_NO_DATA:
            break
        for r in data.get("list", []):
            nm = r.get("report_nm", "")
            fy = parse_report_fiscal_year(nm)
            if ANNUAL_REPORT_NAME not in nm or fy != fiscal_year:
                continue
            filings.append(Filing(
                corp_code=r.get("corp_code", corp_code), corp_name=r.get("corp_name", ""),
                report_nm=nm, rcept_no=r.get("rcept_no", ""), rcept_dt=r.get("rcept_dt", ""),
                flr_nm=r.get("flr_nm", ""), rm=r.get("rm", ""),
                is_correction="정정" in nm, fiscal_year=fy,
            ))
        total_page = int(data.get("total_page", 1) or 1)
        if page >= total_page:
            break
        page += 1
    filings.sort(key=lambda f: (f.rcept_dt, f.rcept_no))
    return filings, params


def latest_filing(filings: list[Filing]) -> Optional[Filing]:
    return filings[-1] if filings else None


def fetch_statements(client, corp_code: str, bsns_year: int, raw_dir: Path,
                     reprt_code: str = "11011", fs_div: str = "CFS") -> StatementFetch:
    """연결재무제표 수집. 013(데이터 없음)이면 검토 대상 기록 후 반환 — OFS 는 호출하지 않는다 (D-02)."""
    if fs_div != "CFS":
        raise ValueError("A-1 은 연결재무제표(CFS)만 수집합니다 (D-02).")
    endpoint = "fnlttSinglAcntAll.json"
    params = {"corp_code": corp_code, "bsns_year": str(bsns_year), "reprt_code": reprt_code, "fs_div": fs_div}
    status, data = client.get_json(endpoint, params)
    out = StatementFetch(corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code, fs_div=fs_div, status=status)
    if status == STATUS_NO_DATA:
        out.issues.append(Issue(
            code="CFS_MISSING", severity=Severity.REVIEW,
            message=f"FY{bsns_year} 사업보고서 연결재무제표(CFS) 조회 결과 없음 — 별도(OFS)는 조회·혼합하지 않음",
            context={"corp_code": corp_code, "bsns_year": bsns_year, "message": data.get("message", "")},
        ))
        return out
    rows = data.get("list", []) or []
    rcepts = sorted({str(r.get("rcept_no", "")) for r in rows})
    if len(rcepts) != 1:
        out.issues.append(Issue(code="MULTIPLE_RCEPT_NO", message=f"응답 내 접수번호가 {len(rcepts)}개: {rcepts}",
                                context={"bsns_year": bsns_year}))
    out.rcept_no = rcepts[-1] if rcepts else None
    path, digest = save_raw_json(raw_dir, corp_code, bsns_year, out.rcept_no or "unknown", endpoint, params, data)
    out.source_file, out.source_sha256, out.row_count = path.name, digest, len(rows)
    lines, issues = expand_rows(rows, fs_div=fs_div, source_file=path.name, source_sha256=digest)
    out.lines, out.issues = lines, out.issues + issues
    return out


def reconcile_rcept_no(fetch: StatementFetch, filings: list[Filing]) -> Issue | None:
    """R4: 재무제표 API 접수번호 = 공시검색 기준 최종 정정본 접수번호. 일치하면 None."""
    final = latest_filing(filings)
    if final is None:
        return Issue(code="R4_NO_FILING", message=f"FY{fetch.bsns_year} 공시검색에서 사업보고서를 찾지 못함",
                     context={"bsns_year": fetch.bsns_year})
    if fetch.rcept_no != final.rcept_no:
        return Issue(code="R4_RCEPT_MISMATCH",
                     message=f"FY{fetch.bsns_year} 재무제표 API 접수번호 {fetch.rcept_no} ≠ 최종 공시 {final.rcept_no} ({final.report_nm})",
                     context={"bsns_year": fetch.bsns_year, "api_rcept_no": fetch.rcept_no,
                              "final_rcept_no": final.rcept_no, "history": [f.model_dump() for f in filings]})
    return None
