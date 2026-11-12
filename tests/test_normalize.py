from conftest import row

from fde.models import NONSTANDARD_ACCOUNT_ID, MappingStatus
from fde.normalize import account_key, expand_rows, item_values, mapping_status, review_issues_for_report


def test_expand_rows_periods_and_fiscal_year():
    lines, issues = expand_rows([row(2025, "BS", "ifrs-full_Assets", "자산총계", 300, 200, 100)], fs_div="CFS",
                                source_file="f.json", source_sha256="abc")
    assert not issues
    by_p = {l.period: l for l in lines}
    assert by_p["thstrm"].fiscal_year == 2025 and by_p["thstrm"].amount == 300
    assert by_p["frmtrm"].fiscal_year == 2024 and by_p["frmtrm"].amount == 200
    assert by_p["bfefrmtrm"].fiscal_year == 2023 and by_p["bfefrmtrm"].amount == 100
    assert all(l.source_file == "f.json" and l.source_sha256 == "abc" and l.fs_div == "CFS" for l in lines)


def test_missing_period_field_creates_no_line():
    lines, _ = expand_rows([row(2025, "IS", "ifrs-full_Revenue", "매출액", 10, 9, include_bf=False)], fs_div="CFS")
    assert {l.period for l in lines} == {"thstrm", "frmtrm"}


def test_blank_amount_is_none_and_raw_kept():
    lines, _ = expand_rows([row(2025, "BS", "ifrs-full_Assets", "자산총계", None, "-", 5)], fs_div="CFS")
    by_p = {l.period: l for l in lines}
    assert by_p["thstrm"].amount is None and by_p["thstrm"].amount_raw == ""
    assert by_p["frmtrm"].amount is None and by_p["frmtrm"].amount_raw == "-"


def test_parse_error_becomes_issue_not_zero():
    lines, issues = expand_rows([row(2025, "BS", "ifrs-full_Assets", "자산총계", "1.5", 1, 1)], fs_div="CFS")
    assert [i.code for i in issues] == ["AMOUNT_PARSE_ERROR"]
    assert next(l for l in lines if l.period == "thstrm").amount is None


def test_account_key_standard_vs_nonstandard():
    k1 = account_key("BS", "ifrs-full_Assets", "자산 총계")
    assert k1.standard and k1.ident == "ifrs-full_Assets"
    k2 = account_key("BS", NONSTANDARD_ACCOUNT_ID, " 기타 자산 ")
    assert not k2.standard and k2.ident == "기타자산"


def test_mapping_status(amap):
    lines, _ = expand_rows([
        row(2025, "BS", "ifrs-full_Assets", "자산총계", 1),
        row(2025, "BS", "ifrs-full_Inventories", "재고자산", 1),
        row(2025, "BS", NONSTANDARD_ACCOUNT_ID, "합성비표준계정", 1),
    ], fs_div="CFS")
    st = {l.account_nm: mapping_status(l, amap) for l in lines if l.period == "thstrm"}
    assert st == {"자산총계": MappingStatus.MAPPED, "재고자산": MappingStatus.UNMAPPED,
                  "합성비표준계정": MappingStatus.NONSTANDARD_REVIEW}


def test_mapping_is_exact_match_only(amap):
    # 계정명이 같아도 ID 가 다르면 매핑하지 않는다 (유사도 매핑 금지)
    lines, _ = expand_rows([row(2025, "BS", "ifrs-full_AssetsX", "자산총계", 1)], fs_div="CFS")
    assert mapping_status(lines[0], amap) == MappingStatus.UNMAPPED


def test_review_issues_nonstandard_and_duplicate(amap):
    lines, _ = expand_rows([
        row(2025, "BS", NONSTANDARD_ACCOUNT_ID, "합성비표준", 1, 1, 1),
        row(2025, "CF", "ifrs-full_X", "중복A", 1, ord_=1),
        row(2025, "CF", "ifrs-full_X", "중복B", 2, ord_=2),
        row(2025, "SCE", "ifrs-full_Y", "자본변동중복", 1),
        row(2025, "SCE", "ifrs-full_Y", "자본변동중복", 2),
    ], fs_div="CFS")
    codes = sorted(i.code for i in review_issues_for_report(lines, amap))
    assert codes == ["DUPLICATE_ACCOUNT_KEY", "NONSTANDARD_ACCOUNT"]  # SCE 제외, 비표준은 기간별 1회


def test_item_values_ambiguous(amap):
    lines, _ = expand_rows([
        row(2025, "BS", "ifrs-full_Assets", "자산총계", 10),
        row(2025, "BS", "ifrs-full_Assets", "자산총계(중복)", 11),
        row(2025, "BS", "ifrs-full_Equity", "자본총계", 4),
    ], fs_div="CFS")
    vals, issues = item_values(lines, amap)
    assert vals[("BS", "total_assets")] is None and vals[("BS", "total_equity")] == 4
    assert [i.code for i in issues] == ["AMBIGUOUS_MAPPED_ITEM"]
