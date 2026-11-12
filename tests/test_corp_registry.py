"""고유번호 목록 파싱·정확 일치 확정 (D-07). 합성 회사명."""
import io
import os
import zipfile

import pytest

from fde.corp_registry import CompanyResolutionError, extract_xml_from_zip, load_registry, parse_corp_xml, resolve_company

XML = """<?xml version="1.0" encoding="UTF-8"?><result>
<list><corp_code>00000001</corp_code><corp_name>가나전자</corp_name><corp_eng_name>GANA</corp_eng_name><stock_code>000001</stock_code><modify_date>20990101</modify_date></list>
<list><corp_code>00000002</corp_code><corp_name>가나전자</corp_name><corp_eng_name>GANA2</corp_eng_name><stock_code> </stock_code><modify_date>20990101</modify_date></list>
<list><corp_code>00000003</corp_code><corp_name>가나전자서비스</corp_name><corp_eng_name/><stock_code>000003</stock_code><modify_date>20990101</modify_date></list>
<list><corp_code>00000004</corp_code><corp_name>다라</corp_name><corp_eng_name/><stock_code>000004</stock_code><modify_date>20990101</modify_date></list>
<list><corp_code>00000005</corp_code><corp_name>다라</corp_name><corp_eng_name/><stock_code>000005</stock_code><modify_date>20990101</modify_date></list>
<list><corp_code>00000006</corp_code><corp_name>마바</corp_name><corp_eng_name/><stock_code></stock_code><modify_date>20990101</modify_date></list>
</result>""".encode("utf-8")


def zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", XML)
    return buf.getvalue()


@pytest.fixture
def companies():
    return parse_corp_xml(extract_xml_from_zip(zip_bytes()))


def test_parse(companies):
    assert len(companies) == 6 and companies[1].stock_code == ""  # 공백 종목코드 → 비상장


def test_exact_listed_single(companies):
    c = resolve_company(" 가나전자 ", companies)
    assert c.corp_code == "00000001"  # 동명 비상장·유사명 상장사는 무시


def test_multiple_exact_listed_stops(companies):
    with pytest.raises(CompanyResolutionError) as e:
        resolve_company("다라", companies)
    assert len(e.value.candidates) == 2


def test_no_exact_shows_partial_candidates(companies):
    with pytest.raises(CompanyResolutionError) as e:
        resolve_company("가나", companies)
    assert {c.corp_code for c in e.value.candidates} == {"00000001", "00000003"}


def test_exact_but_unlisted_stops(companies):
    with pytest.raises(CompanyResolutionError) as e:
        resolve_company("마바", companies)
    assert "상장사가 아닙니다" in e.value.reason


class _ZipClient:
    def __init__(self):
        self.n = 0

    def download_corp_codes(self):
        self.n += 1
        return zip_bytes()


def test_cache_reuse_and_refresh(tmp_path):
    cl = _ZipClient()
    _, path, dl = load_registry(tmp_path, cl, max_age_days=7)
    assert dl and cl.n == 1 and path.name == "corpCode.xml"
    _, _, dl = load_registry(tmp_path, cl, max_age_days=7)
    assert not dl and cl.n == 1
    os.utime(path, (0, 0))  # 오래된 캐시
    _, _, dl = load_registry(tmp_path, cl, max_age_days=7)
    assert dl and cl.n == 2
