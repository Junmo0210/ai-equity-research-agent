"""기준값 선택 (DESIGN 5장). 합성 데이터."""
from conftest import row

from fde.normalize import expand_rows
from fde.selection import candidate_order, key_id, select_values
from fde.models import AccountKey

ASSETS = key_id(AccountKey(sj_div="BS", standard=True, ident="ifrs-full_Assets"))


def reports(*rows_by_year):
    out = {}
    for by, rows in rows_by_year:
        out[by], _ = expand_rows(rows, fs_div="CFS")
    return out


def test_candidate_order_matches_design_table():
    yrs = [2021, 2022, 2023, 2024, 2025]
    assert candidate_order(2025, yrs) == [(2025, "thstrm")]
    assert candidate_order(2024, yrs) == [(2025, "frmtrm"), (2024, "thstrm")]
    assert candidate_order(2023, yrs) == [(2025, "bfefrmtrm"), (2024, "frmtrm"), (2023, "thstrm")]
    assert candidate_order(2021, yrs) == [(2023, "bfefrmtrm"), (2022, "frmtrm"), (2021, "thstrm")]


def test_single_year_uses_thstrm_only():
    res = select_values(reports((2025, [row(2025, "BS", "ifrs-full_Assets", "자산총계", 300, 200, 100)])), [2025])
    c = res.cells[ASSETS][2025]
    assert (c.amount, c.source_bsns_year, c.source_period, c.fallback, c.restated) == (300, 2025, "thstrm", False, False)
    assert set(res.cells[ASSETS]) == {2025}


def test_latest_report_wins_and_restatement_logged():
    res = select_values(reports(
        (2025, [row(2025, "BS", "ifrs-full_Assets", "자산총계", 300, 210, 100)]),
        (2024, [row(2024, "BS", "ifrs-full_Assets", "자산총계", 200, 100)]),
    ), [2024, 2025])
    c = res.cells[ASSETS][2024]
    assert c.amount == 210 and c.source_bsns_year == 2025 and c.source_period == "frmtrm"
    assert c.restated and not c.fallback
    (entry,) = res.restatements
    assert entry.fiscal_year == 2024 and entry.max_diff == 10 and len(entry.candidates) == 2


def test_fallback_when_primary_blank():
    res = select_values(reports(
        (2025, [row(2025, "BS", "ifrs-full_Assets", "자산총계", 300, None)]),
        (2024, [row(2024, "BS", "ifrs-full_Assets", "자산총계", 200)]),
    ), [2024, 2025])
    c = res.cells[ASSETS][2024]
    assert c.amount == 200 and c.source_bsns_year == 2024 and c.fallback and not c.latest_missing
    assert "FALLBACK_SOURCE" in [i.code for i in res.issues]


def test_latest_missing_flag():
    res = select_values(reports(
        (2025, [row(2025, "BS", "ifrs-full_Equity", "자본총계", 5, 4)]),
        (2024, [row(2024, "BS", "ifrs-full_Assets", "자산총계", 200)]),
    ), [2024, 2025])
    c = res.cells[ASSETS][2024]
    assert c.amount == 200 and c.latest_missing and c.fallback
    assert 2025 not in res.cells[ASSETS]  # FY2025 에는 어디에도 없으므로 셀 없음
    assert "LATEST_REPORT_MISSING" in [i.code for i in res.issues]


def test_all_blank_gives_none_not_zero():
    res = select_values(reports((2025, [row(2025, "BS", "ifrs-full_Assets", "자산총계", None)])), [2025])
    assert res.cells[ASSETS][2025].amount is None


def test_sce_excluded_and_name_change_flagged():
    res = select_values(reports(
        (2025, [row(2025, "BS", "ifrs-full_Assets", "자산 합계", 3, 2), row(2025, "SCE", "ifrs-full_Equity", "자본", 1)]),
        (2024, [row(2024, "BS", "ifrs-full_Assets", "자산총계", 2)]),
    ), [2024, 2025])
    assert all(not k.startswith("SCE|") for k in res.cells)
    assert res.meta[ASSETS].account_nm == "자산 합계"  # 최신 보고서 계정명
    assert "ACCOUNT_NAME_CHANGED" in [i.code for i in res.issues]
