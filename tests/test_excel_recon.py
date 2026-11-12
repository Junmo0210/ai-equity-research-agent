"""Excel 작성·재판독과 원본 대사 R1~R3. 합성 데이터."""
from openpyxl import Workbook, load_workbook

from conftest import row

from fde.excel_writer import (SHEETS, STATEMENT_SHEETS, read_statement_cells, save_workbook, write_raw,
                              write_settings, write_statement, write_validation)
from fde.models import NONSTANDARD_ACCOUNT_ID, ValidationStatus as S
from fde.normalize import expand_rows, line_key
from fde.selection import key_id, select_values
from fde.validation import r1_statement_totals, r2_cell_trace, r3_lineage

ROWS = [
    row(2025, "BS", "ifrs-full_Assets", "자산총계", 100, 90, 80, ord_=1),
    row(2025, "BS", NONSTANDARD_ACCOUNT_ID, "합성 비표준", 7, 6, 5, ord_=2),
    row(2025, "IS", "ifrs-full_Revenue", "매출액", 50, 40, 30, ord_=1),
    row(2025, "CIS", "ifrs-full_ProfitLoss", "당기순이익", 9, 8, 7, ord_=1),
    row(2025, "CF", "dart_CashAndCashEquivalentsAtEndOfPeriodCf", "기말현금", None, 3, 2, ord_=1),
    row(2025, "SCE", "ifrs-full_Equity", "자본", 1, 1, 1, ord_=1),
]


def build(tmp_path, amap):
    lines, _ = expand_rows(ROWS, fs_div="CFS", source_file="f.json", source_sha256="x")
    reports = {2025: lines}
    sel = select_values(reports, [2025])
    wb = Workbook()
    wb.remove(wb.active)
    for n in SHEETS:
        wb.create_sheet(n)
    write_settings(wb["Settings"], {"회사명": "합성"}, [])
    write_raw(wb["Raw Data"], lines)
    for sheet, sjs in STATEMENT_SHEETS.items():
        write_statement(wb[sheet], sjs, sel, [2025], amap)
    write_validation(wb["Validation"], [], [], sel, [], [])
    path = tmp_path / "out.xlsx"
    save_workbook(wb, path)
    index = {(key_id(line_key(l)), l.bsns_year, l.period): l for l in lines}
    return path, reports, index


def test_six_sheets_and_roundtrip_r1_r2(tmp_path, amap):
    path, reports, index = build(tmp_path, amap)
    assert load_workbook(path).sheetnames == SHEETS
    cells, by_sj = read_statement_cells(path, [2025])
    assert len(cells) == 5  # SCE 제외
    blank = next(c for c in cells if c["key_id"].startswith("CF|"))
    assert blank["value"] is None  # 공란은 0 이 아니라 빈 셀
    r2 = r2_cell_trace(cells, lambda k, by, p: index.get((k, by, p)))
    assert r2.status == S.PASS
    r1 = r1_statement_totals(reports[2025], by_sj, 2025)
    assert all(r.status == S.PASS for r in r1)


def test_r2_detects_tampered_cell(tmp_path, amap):
    path, reports, index = build(tmp_path, amap)
    wb = load_workbook(path)
    ws = wb["Balance Sheet"]
    hdr = [c.value for c in ws[1]]
    target = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, hdr.index("_key") + 1).value == "BS|S|ifrs-full_Assets")
    ws.cell(target, hdr.index("FY2025") + 1, value=101)
    wb.save(path)
    cells, by_sj = read_statement_cells(path, [2025])
    assert r2_cell_trace(cells, lambda k, by, p: index.get((k, by, p))).status == S.FAIL
    r1 = {r.description: r.status for r in r1_statement_totals(reports[2025], by_sj, 2025)}
    assert r1["[BS] 원본 당기 금액 합계 = 시트 FY2025 합계"] == S.FAIL


def test_r3_currency_and_rcept():
    lines, _ = expand_rows([row(2025, "BS", "a", "a", 1), row(2025, "BS", "b", "b", 1, currency="USD")], fs_div="CFS")
    res = {r.description: r.status for r in r3_lineage(lines, 2025)}
    assert res == {"모든 원본 행에 접수번호 존재": S.PASS, "통화 단일성": S.FAIL}
