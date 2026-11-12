"""회계 검증 V1~V6. 합성 수치."""
from fde.models import ValidationStatus as S
from fde.validation import critical_missing_issues, run_accounting_checks

BASE = {
    ("BS", "total_assets"): 100, ("BS", "total_liabilities"): 60, ("BS", "total_equity"): 40,
    ("BS", "equity_parent"): 35, ("BS", "equity_nci"): 5, ("BS", "cash_bs"): 12,
    ("IS", "revenue"): 50, ("IS", "cost_of_sales"): 30, ("IS", "gross_profit"): 20,
    ("IS", "profit_is"): 7, ("CIS", "profit_cis"): 7,
    ("CF", "cash_begin"): 10, ("CF", "cash_change"): 3, ("CF", "cash_fx_effect"): -1, ("CF", "cash_end"): 12,
}


def by_id(values, tol=0):
    return {r.rule_id: r for r in run_accounting_checks(values, 2025, "selected", tol)}


def test_all_pass():
    assert {k: r.status for k, r in by_id(BASE).items()} == {f"V{i}": S.PASS for i in range(1, 7)}


def test_fail_reports_diff():
    r = by_id({**BASE, ("BS", "total_assets"): 103})["V1"]
    assert r.status == S.FAIL and r.diff == 3


def test_tolerance():
    v = {**BASE, ("BS", "total_assets"): 101}
    assert by_id(v, tol=0)["V1"].status == S.FAIL
    assert by_id(v, tol=1)["V1"].status == S.PASS


def test_missing_is_review_never_pass():
    v = dict(BASE); del v[("BS", "equity_nci")]
    r = by_id(v)["V2"]
    assert r.status == S.REVIEW and r.missing == ["BS:equity_nci"]
    (iss,) = critical_missing_issues([r])
    assert iss.code == "CRITICAL_MISSING_REVIEW"


def test_none_value_treated_as_missing():
    r = by_id({**BASE, ("CF", "cash_end"): None})
    assert r["V4"].status == S.REVIEW and r["V5"].status == S.REVIEW


def test_v3_na_when_missing_and_cis_fallback():
    v = dict(BASE); del v[("IS", "gross_profit")]
    assert by_id(v)["V3"].status == S.NA
    v2 = {k: x for k, x in BASE.items() if k[1] not in ("revenue", "cost_of_sales", "gross_profit")}
    v2.update({("CIS", "revenue"): 9, ("CIS", "cost_of_sales"): 4, ("CIS", "gross_profit"): 5})
    assert by_id(v2)["V3"].status == S.PASS


def test_v4_formula_a_before_fx_plus_fx():
    r = by_id(BASE)["V4"]
    assert r.status == S.PASS and r.note.startswith("산식 A") and r.lhs == 12


def test_v4_formula_a_requires_fx():
    v = dict(BASE); del v[("CF", "cash_fx_effect")]
    r = by_id(v)["V4"]
    assert r.status == S.REVIEW and r.missing == ["CF:cash_fx_effect"]


def test_v4_formula_b_total_change_when_before_fx_absent():
    v = dict(BASE); del v[("CF", "cash_change")]
    v[("CF", "cash_change_total")] = 2  # 환율효과 포함 증감 (합성: 3 + (-1))
    r = by_id(v)["V4"]
    assert r.status == S.PASS and r.note.startswith("산식 B") and r.lhs == 12
    v[("CF", "cash_change_total")] = 3  # 환율효과를 다시 더하지 않음 → 차이 발생
    assert by_id(v)["V4"].status == S.FAIL


def test_v4_prefers_formula_a_when_both_present():
    v = {**BASE, ("CF", "cash_change_total"): 999}
    assert by_id(v)["V4"].note.startswith("산식 A")


def test_v4_review_when_no_change_account():
    v = dict(BASE); del v[("CF", "cash_change")]
    r = by_id(v)["V4"]
    assert r.status == S.REVIEW and "CF:cash_change|cash_change_total" in r.missing


def test_v6_na_when_only_one():
    v = dict(BASE); del v[("CIS", "profit_cis")]
    assert by_id(v)["V6"].status == S.NA
    assert by_id({**BASE, ("CIS", "profit_cis"): 8})["V6"].status == S.FAIL
