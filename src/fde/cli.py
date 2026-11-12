"""명령행 진입점.

  python -m fde.cli resolve 삼성전자            # T1: 고유번호 확인
  python -m fde.cli run [--years 2025]         # T2/T3: 수집 → Excel → 검증
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from .corp_registry import CompanyResolutionError, load_registry, resolve_company
from .dart_client import DartClient, DartError, SecretMaskingFilter, load_api_key
from .excel_writer import OutputLockedError
from .models import Issue
from .normalize import AccountMap

ROOT = Path(__file__).resolve().parents[2]


def load_settings(path: Optional[Path] = None) -> dict[str, Any]:
    path = path or ROOT / "config" / "settings.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(logs_dir: Path, secret: Optional[str]) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"fde_{datetime.now():%Y%m%d}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(SecretMaskingFilter(secret))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # requests/urllib3 디버그 로그에는 전체 URL(키 포함)이 찍힐 수 있으므로 WARNING 으로 제한
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return log_path


def cmd_resolve(args: argparse.Namespace, settings: dict[str, Any]) -> int:
    name = args.name or settings["company"]
    key = load_api_key()
    setup_logging(ROOT / settings["paths"]["logs"], key)
    client = DartClient(api_key=key)
    cache_dir = ROOT / settings["paths"]["data"] / "cache"
    companies, cache_path, downloaded = load_registry(
        cache_dir, client, max_age_days=0 if args.refresh else settings.get("corp_cache_max_age_days", 7))
    print(f"고유번호 목록: {len(companies):,}건 ({'새로 내려받음' if downloaded else '캐시 사용'}: {cache_path.relative_to(ROOT)})")
    try:
        c = resolve_company(name, companies)
    except CompanyResolutionError as e:
        print(f"[중단] {e.reason}: '{e.query}'")
        for cand in e.candidates:
            print(f"  - {cand.corp_name} | corp_code={cand.corp_code} | stock_code={cand.stock_code or '(비상장)'}")
        return 2
    print("[확정] 정확 일치 상장사 1건")
    print(f"  corp_name   : {c.corp_name}")
    print(f"  corp_eng    : {c.corp_eng_name}")
    print(f"  corp_code   : {c.corp_code}")
    print(f"  stock_code  : {c.stock_code}")
    print(f"  modify_date : {c.modify_date}")
    return 0


def _selected_values(sel, amap: AccountMap, fy: int) -> dict[tuple[str, str], Optional[int]]:
    out: dict[tuple[str, str], Optional[int]] = {}
    for kid, by_year in sel.cells.items():
        c = by_year.get(fy)
        if c is None or not c.key.standard:
            continue
        item = amap.item_for(c.key.sj_div, c.key.ident)
        if item:
            out[(c.key.sj_div, item)] = c.amount
    return out


def scan_for_secret(dirs: list[Path], secret: str) -> list[Path]:
    needle = secret.encode("utf-8")
    hits = []
    for d in dirs:
        if d.exists():
            hits += [p for p in d.rglob("*") if p.is_file() and needle in p.read_bytes()]
    return hits


def cmd_run(args: argparse.Namespace, settings: dict[str, Any]) -> int:
    from openpyxl import Workbook, load_workbook

    from . import __version__
    from .collector import fetch_filings, fetch_statements, latest_filing, reconcile_rcept_no
    from .excel_writer import (SHEETS, STATEMENT_SHEETS, read_statement_cells, save_workbook, write_raw,
                               write_settings, write_statement, write_validation)
    from .normalize import review_issues_for_report
    from .selection import key_id, select_values
    from .validation import (critical_missing_issues, r1_statement_totals, r2_cell_trace, r3_lineage, r4_result,
                             run_accounting_checks)
    from .normalize import item_values

    years = [int(y) for y in (args.years or settings["years"])]
    reprt_code, fs_div = str(settings["reprt_code"]), settings["fs_div"]
    tol = int(settings.get("tolerance", 0))
    key = load_api_key()
    log_path = setup_logging(ROOT / settings["paths"]["logs"], key)
    log = logging.getLogger("fde.run")
    client = DartClient(api_key=key)
    amap = AccountMap.from_csv(ROOT / settings["paths"]["account_map"])
    data_dir = ROOT / settings["paths"]["data"]

    companies, _, _ = load_registry(data_dir / "cache", client, settings.get("corp_cache_max_age_days", 7))
    try:
        company = resolve_company(args.name or settings["company"], companies)
    except CompanyResolutionError as e:
        print(f"[중단] {e.reason}: '{e.query}' — 후보: " + ", ".join(f"{c.corp_name}({c.corp_code})" for c in e.candidates))
        return 2
    print(f"[1] 회사 확정: {company.corp_name} corp_code={company.corp_code} stock_code={company.stock_code}")

    issues: list[Issue] = []
    recon: list = []
    reports: dict[int, list] = {}
    year_rows: list[dict[str, Any]] = []
    all_lines: list = []
    for y in years:
        filings, _ = fetch_filings(client, company.corp_code, y)
        final = latest_filing(filings)
        st = fetch_statements(client, company.corp_code, y, data_dir / "raw", reprt_code, fs_div)
        issues += st.issues
        print(f"[2-3] FY{y}: 공시검색 {len(filings)}건 (최종 {final.rcept_no if final else '-'}), "
              f"재무제표 status={st.status} rcept_no={st.rcept_no} 행={st.row_count}")
        if st.status == "000":
            reports[y] = st.lines
            all_lines += st.lines
            issues += review_issues_for_report(st.lines, amap)
            iss = reconcile_rcept_no(st, filings)
            if iss:
                issues.append(iss)
            recon.append(r4_result(y, iss, st.rcept_no, final.rcept_no if final else None))
        year_rows.append({"사업연도": y, "상태": st.status, "재무제표 API 접수번호": st.rcept_no,
                          "공시검색 최종 접수번호": final.rcept_no if final else None,
                          "보고서명": final.report_nm if final else None,
                          "정정 여부": ("정정 이력 있음" if any(f.is_correction for f in filings) else "없음") if filings else None,
                          "원본 행 수": st.row_count, "원본 파일": st.source_file, "SHA-256": st.source_sha256})

    sel = select_values(reports, years)
    issues += sel.issues
    checks = []
    for fy in years:
        checks += run_accounting_checks(_selected_values(sel, amap, fy), fy, "selected", tol)
        if fy in reports:
            vals, amb = item_values(reports[fy], amap, "thstrm")
            issues += amb
            checks += run_accounting_checks(vals, fy, "report_thstrm", tol)
    issues += critical_missing_issues(checks)
    if all_lines:
        recon += r3_lineage(all_lines, max(years))

    # 스팟체크 목록
    spot = []
    for fy in sorted(years, reverse=True):
        for sj, aid in settings.get("spot_check", []):
            kid = f"{sj}|S|{aid}"
            c = sel.cells.get(kid, {}).get(fy)
            m = sel.meta.get(kid)
            if c is None:
                issues.append(Issue(code="SPOT_CHECK_ACCOUNT_MISSING", message=f"FY{fy} 스팟체크 계정 없음: [{sj}] {aid}"))
                continue
            spot.append({"fiscal_year": fy, "sj_div": sj, "account_nm": m.account_nm, "account_id": aid,
                         "amount": c.amount, "rcept_no": c.source_rcept_no})

    info = {
        "회사명": company.corp_name, "고유번호(corp_code)": company.corp_code, "종목코드": company.stock_code,
        "대상 연도": ", ".join(str(y) for y in years), "보고서 코드": f"{reprt_code} (사업보고서)",
        "재무제표 구분 정책": "연결(CFS)만 — 없으면 별도(OFS) 미조회, 검토 대상 보고",
        "기준값 정책": "해당 연도를 담은 가장 최근 사업보고서 값 (DESIGN 5장)",
        "금액 단위": "API 원본 단위 그대로 (통화: " + ", ".join(sorted({l.currency for l in all_lines})) + ")",
        "허용오차": tol, "생성 시각": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "엔진 버전": __version__,
        "인증키 상태": "환경변수 사용, 값 비공개",
    }

    wb = Workbook()
    wb.remove(wb.active)
    for name in SHEETS:
        wb.create_sheet(name)
    write_settings(wb["Settings"], info, year_rows)
    write_raw(wb["Raw Data"], all_lines)
    for sheet, sjs in STATEMENT_SHEETS.items():
        write_statement(wb[sheet], sjs, sel, years, amap)
    recon_pre = [r for r in recon]
    pos = write_validation(wb["Validation"], checks, recon_pre, sel, issues, spot)
    yr = f"FY{years[0]}" if len(years) == 1 else f"FY{min(years)}-{max(years)}"
    out = ROOT / settings["paths"]["output"] / f"{company.corp_code}_{company.corp_name}_A1_{yr}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    save_workbook(wb, out)

    # 저장 후 재판독 → R1, R2
    cells, by_sj = read_statement_cells(out, years)
    index = {}
    for by, lines in reports.items():
        for ln in lines:
            from .normalize import line_key
            index.setdefault((key_id(line_key(ln)), by, ln.period), ln)
    post = [r2_cell_trace(cells, lambda k, by, p: index.get((k, by, p)))]
    latest = max(reports) if reports else None
    if latest is not None:
        post = r1_statement_totals(reports[latest], by_sj, latest) + post
    recon_all = recon_pre + post
    wb = load_workbook(out)
    del wb["Validation"]
    ws = wb.create_sheet("Validation")
    write_validation(ws, checks, recon_all, sel, issues, spot)
    save_workbook(wb, out)

    # 콘솔 요약
    print(f"[4-6] RawLine {len(all_lines)}행, 계정 {len(sel.meta)}개(SCE 제외), 금액 셀 {len(cells)}개")
    print("[8] 회계 검증")
    for v in checks:
        print(f"   {v.rule_id} FY{v.fiscal_year} {v.basis:13s} {v.status.value:6s} {v.description}"
              + (f" | 차이={v.diff:,}" if v.diff is not None else "") + (f" | 부재={v.missing}" if v.missing else ""))
    print("    원본 대사")
    for v in recon_all:
        print(f"   {v.rule_id} FY{v.fiscal_year} {v.status.value:6s} {v.description}"
              + (f" | {v.lhs}/{v.rhs}" if v.lhs is not None or v.rhs is not None else "") + (f" | {v.note}" if v.note else ""))
    from collections import Counter
    print("    확인 대상:", dict(Counter(i.code for i in issues)))
    hits = scan_for_secret([ROOT / settings["paths"][k] for k in ("output", "logs", "data")], key)
    print(f"[보안] output/logs/data 인증키 문자열 검색: {len(hits)}건")
    print(f"[9] Excel: {out.relative_to(ROOT)}")
    print(f"    로그: {log_path.relative_to(ROOT)}")
    log.info("run 완료: %s", out.name)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    p = argparse.ArgumentParser(prog="fde", description="A-1 Financial Data Engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="회사명 → 고유번호 확인 (T1)")
    r.add_argument("name", nargs="?", help="회사명 (기본: settings.yaml 의 company)")
    r.add_argument("--refresh", action="store_true", help="고유번호 캐시 강제 갱신")
    rn = sub.add_parser("run", help="수집 → Excel → 검증 (T2/T3)")
    rn.add_argument("name", nargs="?", help="회사명 (기본: settings.yaml 의 company)")
    rn.add_argument("--years", nargs="+", type=int, help="대상 사업연도 (기본: settings.yaml 의 years)")
    args = p.parse_args(argv)
    settings = load_settings()
    try:
        if args.cmd == "resolve":
            return cmd_resolve(args, settings)
        if args.cmd == "run":
            return cmd_run(args, settings)
    except OutputLockedError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    except DartError as e:  # 메시지는 이미 키가 제거된 상태
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
