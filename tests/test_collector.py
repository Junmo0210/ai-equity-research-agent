"""수집·CFS 정책·정정공시 대사. 네트워크 없음 (FakeClient)."""
import json
from datetime import date

import pytest
from conftest import FAKE_CORP, FakeClient, row

from fde.collector import fetch_filings, fetch_statements, parse_report_fiscal_year, reconcile_rcept_no
from fde.models import Filing, StatementFetch


def test_cfs_missing_records_review_and_never_calls_ofs(tmp_path):
    client = FakeClient({"fnlttSinglAcntAll.json": [("013", {"status": "013", "message": "조회된 데이타가 없습니다."})]})
    out = fetch_statements(client, FAKE_CORP, 2025, tmp_path)
    assert out.status == "013" and out.lines == [] and out.rcept_no is None
    assert [i.code for i in out.issues] == ["CFS_MISSING"]
    assert len(client.calls) == 1 and client.calls[0][1]["fs_div"] == "CFS"
    assert not any(tmp_path.rglob("*.json"))  # 저장할 원본 없음


def test_ofs_request_rejected(tmp_path):
    with pytest.raises(ValueError):
        fetch_statements(FakeClient({}), FAKE_CORP, 2025, tmp_path, fs_div="OFS")


def test_success_saves_raw_with_hash(tmp_path):
    rows = [row(2025, "BS", "ifrs-full_Assets", "자산총계", 3, 2, 1, rcept_no="20990301000001")]
    client = FakeClient({"fnlttSinglAcntAll.json": [("000", {"status": "000", "message": "정상", "list": rows})]})
    out = fetch_statements(client, FAKE_CORP, 2025, tmp_path)
    assert out.rcept_no == "20990301000001" and out.row_count == 1 and len(out.lines) == 3
    path = tmp_path / FAKE_CORP / "2025_20990301000001.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "crtfc_key" not in json.dumps(saved["request"])
    assert len(out.source_sha256) == 64 and all(l.source_sha256 == out.source_sha256 for l in out.lines)


def test_parse_report_fiscal_year():
    assert parse_report_fiscal_year("사업보고서 (2099.12)") == 2099
    assert parse_report_fiscal_year("[기재정정]사업보고서 (2099.12)") == 2099
    assert parse_report_fiscal_year("반기보고서") is None


def _list_resp(items, page=1, total_page=1):
    return ("000", {"status": "000", "page_no": page, "total_page": total_page, "list": items})


def test_fetch_filings_filters_pages_and_sorts():
    p1 = [{"corp_code": FAKE_CORP, "report_nm": "[기재정정]사업보고서 (2098.12)", "rcept_no": "20990501000002", "rcept_dt": "20990501"},
          {"corp_code": FAKE_CORP, "report_nm": "사업보고서 (2097.12)", "rcept_no": "20980301000001", "rcept_dt": "20980301"}]
    p2 = [{"corp_code": FAKE_CORP, "report_nm": "사업보고서 (2098.12)", "rcept_no": "20990301000001", "rcept_dt": "20990301"}]
    client = FakeClient({"list.json": [_list_resp(p1, 1, 2), _list_resp(p2, 2, 2)]})
    filings, params = fetch_filings(client, FAKE_CORP, 2098, today=date(2099, 6, 1))
    assert [f.rcept_no for f in filings] == ["20990301000001", "20990501000002"]
    assert filings[-1].is_correction and params["bgn_de"] == "20990101" and params["end_de"] == "20990601"
    assert [c[1]["page_no"] for c in client.calls] == [1, 2]


def test_fetch_filings_no_data():
    client = FakeClient({"list.json": [("013", {"status": "013"})]})
    assert fetch_filings(client, FAKE_CORP, 2098, today=date(2099, 6, 1))[0] == []


def test_reconcile_rcept_no():
    f1 = Filing(corp_code=FAKE_CORP, report_nm="사업보고서 (2098.12)", rcept_no="A1", rcept_dt="20990301")
    f2 = Filing(corp_code=FAKE_CORP, report_nm="[기재정정]사업보고서 (2098.12)", rcept_no="A2", rcept_dt="20990501", is_correction=True)
    fetch = StatementFetch(corp_code=FAKE_CORP, bsns_year=2098, reprt_code="11011", fs_div="CFS", status="000", rcept_no="A2")
    assert reconcile_rcept_no(fetch, [f1, f2]) is None
    fetch.rcept_no = "A1"
    assert reconcile_rcept_no(fetch, [f1, f2]).code == "R4_RCEPT_MISMATCH"
    assert reconcile_rcept_no(fetch, []).code == "R4_NO_FILING"
