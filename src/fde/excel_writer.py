"""Excel 6개 시트 작성과 저장 후 재판독 (DESIGN 7장, D-08, R2)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import PERIOD_LABEL_KO, Issue, RawLine, ValidationResult
from .normalize import AccountMap, mapping_status
from .selection import SelectionResult

SHEETS = ["Settings", "Raw Data", "Balance Sheet", "Income Statement", "Cash Flow", "Validation"]
STATEMENT_SHEETS = {"Balance Sheet": ("BS",), "Income Statement": ("IS", "CIS"), "Cash Flow": ("CF",)}
SJ_TITLE = {"BS": "재무상태표 (BS)", "IS": "손익계산서 (IS)", "CIS": "포괄손익계산서 (CIS)", "CF": "현금흐름표 (CF)"}
KEY_HEADER = "_key"
NUM_FMT = "#,##0;-#,##0"
EXCEL_EXACT_INT_LIMIT = 2 ** 53  # Excel 은 숫자를 double 로 저장 — 이 범위를 넘으면 정밀도 손실

BOLD = Font(bold=True)
HEAD_FILL = PatternFill("solid", fgColor="DDE4EE")
SECTION_FILL = PatternFill("solid", fgColor="F2F2F2")
STATUS_FILL = {"PASS": "C6EFCE", "FAIL": "FFC7CE", "REVIEW": "FFEB9C", "N/A": "EDEDED"}

RAW_COLUMNS = ["corp_code", "bsns_year", "reprt_code", "rcept_no", "fs_div", "sj_div", "sj_nm", "account_id",
               "account_nm", "account_detail", "ord", "period", "period_nm", "fiscal_year", "amount", "amount_raw",
               "currency", "source_file", "source_sha256"]


def dart_link(rcept_no: Optional[str]) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""


def _header(ws, row: int, values: list[str]) -> None:
    for i, v in enumerate(values, 1):
        c = ws.cell(row=row, column=i, value=v)
        c.font, c.fill = BOLD, HEAD_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)


def _widths(ws, widths: dict[int, float]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def _safe_amount(v: Optional[int]) -> Any:
    if v is not None and abs(v) >= EXCEL_EXACT_INT_LIMIT:
        raise ValueError(f"금액 {v} 가 Excel 정밀 정수 범위를 넘습니다 — 문자열 저장 정책 필요")
    return v


def _section_title(ws, row: int, text: str, ncols: int) -> None:
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = SECTION_FILL
    ws.cell(row=row, column=2, value=text).font = BOLD


# ---- 시트별 작성 ---------------------------------------------------------------

def write_settings(ws, info: dict[str, Any], year_rows: list[dict[str, Any]]) -> None:
    ws.append(["항목", "값"])
    _header(ws, 1, ["항목", "값"])
    for k, v in info.items():
        ws.append([k, v])
    ws.append([])
    start = ws.max_row + 1
    cols = ["사업연도", "상태", "재무제표 API 접수번호", "공시검색 최종 접수번호", "보고서명", "정정 여부",
            "원본 행 수", "원본 파일", "SHA-256"]
    ws.append(cols)
    _header(ws, start, cols)
    for r in year_rows:
        ws.append([r.get(c) for c in cols])
    _widths(ws, {1: 26, 2: 40, 3: 22, 4: 22, 5: 30, 6: 10, 7: 10, 8: 30, 9: 66})


def write_raw(ws, lines: list[RawLine]) -> None:
    ws.append(RAW_COLUMNS)
    _header(ws, 1, RAW_COLUMNS)
    for ln in lines:
        d = ln.model_dump()
        ws.append([_safe_amount(d[c]) if c == "amount" else d[c] for c in RAW_COLUMNS])
    amt_col = RAW_COLUMNS.index("amount") + 1
    for row in ws.iter_rows(min_row=2, min_col=amt_col, max_col=amt_col):
        row[0].number_format = NUM_FMT
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    _widths(ws, {8: 40, 9: 30, 15: 20, 16: 20, 18: 28, 19: 20})


def write_statement(ws, sjs: tuple[str, ...], sel: SelectionResult, years: list[int], amap: AccountMap) -> None:
    cols = (["계정ID", "계정명", "표준 여부", "매핑 상태", "매핑 항목"] + [f"FY{y}" for y in years]
            + [f"출처 FY{y}" for y in years] + ["플래그", "원본 표시순서", KEY_HEADER])
    _header(ws, 1, cols)
    ncols = len(cols)
    first_amt = 6
    r = 2
    for sj in sjs:
        metas = sorted((m for kid, m in sel.meta.items() if m.key.sj_div == sj),
                       key=lambda m: (m.ord if m.ord is not None else 10 ** 9, m.account_nm))
        _section_title(ws, r, SJ_TITLE[sj], ncols)
        r += 1
        for m in metas:
            kid = f"{m.key.sj_div}|{'S' if m.key.standard else 'N'}|{m.key.ident}"
            cells = sel.cells.get(kid, {})
            flags: list[str] = []
            if any(c.restated for c in cells.values()):
                flags.append("재작성")
            if any(c.fallback for c in cells.values()):
                flags.append("대체 출처")
            if any(c.latest_missing for c in cells.values()):
                flags.append("최신 보고서 미존재")
            if len(m.names_seen) > 1:
                flags.append("계정명 변경")
            st = mapping_status(m.key, amap)
            item = amap.item_for(sj, m.key.ident) if m.key.standard else None
            row = [m.account_id, m.account_nm, "표준" if m.key.standard else "비표준", st.value, item or ""]
            for y in years:
                c = cells.get(y)
                row.append(_safe_amount(c.amount) if c else None)
            for y in years:
                c = cells.get(y)
                row.append(f"{c.source_rcept_no} · FY{c.source_bsns_year} 보고서 {PERIOD_LABEL_KO.get(c.source_period, '')}"
                           if c and c.source_rcept_no else "")
            row += [", ".join(flags), m.ord, kid]
            for col, v in enumerate(row, 1):
                ws.cell(row=r, column=col, value=v)
            for i in range(len(years)):
                ws.cell(row=r, column=first_amt + i).number_format = NUM_FMT
            r += 1
        r += 1
    ws.freeze_panes = "C2"
    ws.column_dimensions[get_column_letter(ncols)].hidden = True
    _widths(ws, {1: 44, 2: 34, 3: 8, 4: 24, 5: 16, **{first_amt + i: 22 for i in range(len(years))},
                 **{first_amt + len(years) + i: 40 for i in range(len(years))}, ncols - 2: 22})


def _write_table(ws, start_row: int, title: str, cols: list[str], rows: list[list[Any]]) -> int:
    ws.cell(row=start_row, column=1, value=title).font = Font(bold=True, size=12)
    _header(ws, start_row + 1, cols)
    r = start_row + 2
    for row in rows:
        for col, v in enumerate(row, 1):
            ws.cell(row=r, column=col, value=v)
        r += 1
    if not rows:
        ws.cell(row=r, column=1, value="(해당 없음)")
        r += 1
    return r + 1


def _status_fill(ws, col: int, first: int, last: int) -> None:
    for r in range(first, last):
        c = ws.cell(row=r, column=col)
        if c.value in STATUS_FILL:
            c.fill = PatternFill("solid", fgColor=STATUS_FILL[c.value])


def write_validation(ws, checks: list[ValidationResult], recon: list[ValidationResult], sel: SelectionResult,
                     issues: list[Issue], spot: list[dict[str, Any]]) -> dict[str, int]:
    """Validation 시트. 각 구역 시작 행을 반환 (R2 결과 갱신용)."""
    pos: dict[str, int] = {}
    vcols = ["ID", "회계연도", "기준", "상태", "규칙", "좌변", "우변", "차이", "부재 계정", "비고"]

    def vrow(v: ValidationResult) -> list[Any]:
        return [v.rule_id, v.fiscal_year, v.basis, v.status.value, v.description, v.lhs, v.rhs, v.diff,
                ", ".join(v.missing), v.note]

    pos["checks"] = 1
    r = _write_table(ws, 1, "① 회계 검증 (V1~V6)", vcols, [vrow(v) for v in checks])
    _status_fill(ws, 4, 3, r)
    pos["recon"] = r
    r2 = _write_table(ws, r, "② 원본 대사 (R1~R4)", vcols, [vrow(v) for v in recon])
    _status_fill(ws, 4, r + 2, r2)
    for row in ws.iter_rows(min_row=3, max_row=r2, min_col=6, max_col=8):
        for c in row:
            c.number_format = NUM_FMT
    r = r2
    rs_rows = []
    for e in sel.restatements:
        for c in e.candidates:
            rs_rows.append([e.fiscal_year, e.key.sj_div, e.key.ident, e.account_nm, c["bsns_year"],
                            PERIOD_LABEL_KO.get(c["period"], c["period"]), c["rcept_no"], c["amount"], e.max_diff])
    r = _write_table(ws, r, "③ 재작성 로그", ["회계연도", "재무제표", "계정 키", "계정명", "출처 보고서", "기간",
                                          "접수번호", "금액", "최대 차이"], rs_rows)
    r = _write_table(ws, r, "④ 확인 대상 목록", ["코드", "심각도", "내용", "상세"],
                     [[i.code, i.severity.value, i.message,
                       "; ".join(f"{k}={v}" for k, v in i.context.items() if k != "history")] for i in issues])
    pos["spot"] = r
    scols = ["회계연도", "재무제표", "계정명", "계정ID", "시트 금액", "출처 접수번호", "DART 원문 링크",
             "DART 원문 금액 (사용자 기입)", "대조 결과", "비고 (사용자)"]
    ws.cell(row=r, column=1, value="⑤ 사용자 스팟체크 — DART 원문의 연결재무제표 금액을 H열에 입력하면 I열이 자동 판정").font = Font(bold=True, size=12)
    _header(ws, r + 1, scols)
    rr = r + 2
    for s in spot:
        ws.cell(row=rr, column=1, value=s["fiscal_year"])
        ws.cell(row=rr, column=2, value=s["sj_div"])
        ws.cell(row=rr, column=3, value=s["account_nm"])
        ws.cell(row=rr, column=4, value=s["account_id"])
        c = ws.cell(row=rr, column=5, value=_safe_amount(s["amount"]))
        c.number_format = NUM_FMT
        ws.cell(row=rr, column=6, value=s["rcept_no"])
        link = dart_link(s["rcept_no"])
        lc = ws.cell(row=rr, column=7, value=link)
        if link:
            lc.hyperlink, lc.font = link, Font(color="0563C1", underline="single")
        ws.cell(row=rr, column=8).number_format = NUM_FMT
        ws.cell(row=rr, column=9, value=f'=IF(H{rr}="","",IF(H{rr}=E{rr},"일치","불일치"))')
        rr += 1
    _widths(ws, {1: 12, 2: 10, 3: 30, 4: 34, 5: 46, 6: 22, 7: 22, 8: 22, 9: 30, 10: 60})
    return pos


# ---- 저장·재판독 ---------------------------------------------------------------

class OutputLockedError(Exception):
    pass


def save_workbook(wb: Workbook, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError:
        raise OutputLockedError(
            f"Excel 저장 실패(PermissionError): {path} — 같은 파일이 Excel 에서 열려 있으면 닫고 다시 실행하세요.") from None


def read_statement_cells(path: Path, years: list[int]) -> tuple[list[dict], dict[str, list[tuple[str, Optional[int]]]]]:
    """저장된 파일을 다시 열어 금액 셀과 출처를 읽는다 (R1·R2 용)."""
    wb = load_workbook(path, read_only=True, data_only=True)
    cells: list[dict] = []
    by_sj: dict[str, list[tuple[str, Optional[int]]]] = {}
    for sheet in STATEMENT_SHEETS:
        rows = list(wb[sheet].iter_rows(values_only=True))
        hdr = list(rows[0])
        ki = hdr.index(KEY_HEADER)
        amt_idx = {y: hdr.index(f"FY{y}") for y in years}
        src_idx = {y: hdr.index(f"출처 FY{y}") for y in years}
        for rnum, row in enumerate(rows[1:], start=2):
            kid = row[ki] if ki < len(row) else None
            if not kid:
                continue
            sj = kid.split("|", 1)[0]
            for y in years:
                src = row[src_idx[y]]
                if not src:
                    continue
                # "rcept · FY2025 보고서 당기"
                rest = src.split(" · ", 1)[1]
                by = int(rest[2:6])
                plabel = rest.rsplit(" ", 1)[1]
                period = {v: k for k, v in PERIOD_LABEL_KO.items()}[plabel]
                cells.append({"sheet": sheet, "row": rnum, "key_id": kid, "fiscal_year": y, "value": row[amt_idx[y]],
                              "source_bsns_year": by, "source_period": period})
            latest = max(years)
            if row[src_idx[latest]]:  # 최신 보고서 당기에서 온 행만 R1 집계 대상
                by_sj.setdefault(sj, []).append((kid, row[amt_idx[latest]]))
    wb.close()
    return cells, by_sj
