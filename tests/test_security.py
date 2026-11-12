"""인증키 마스킹·상태코드·재시도 (DESIGN 3.2, 8장). 합성 키만 사용."""
import logging

import pytest
import requests
from conftest import FAKE_CORP, FAKE_KEY

from fde.dart_client import (
    DartApiError, DartClient, DartNetworkError, MissingApiKeyError, SecretMaskingFilter,
    load_api_key, mask_secret, save_raw_json,
)


class Resp:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code, self._payload, self.content = status_code, payload, content

    def json(self):
        if self._payload is None:
            raise ValueError
        return self._payload


class Sess:
    def __init__(self, items):
        self.items, self.calls = list(items), 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        return it


def client(items):
    sleeps = []
    c = DartClient(api_key=FAKE_KEY, session=Sess(items), sleep=sleeps.append)
    return c, sleeps


def test_mask_secret_query_and_raw():
    s = f"GET https://x/api/list.json?corp_code=1&crtfc_key={FAKE_KEY}&page_no=1 key={FAKE_KEY}"
    m = mask_secret(s, FAKE_KEY)
    assert FAKE_KEY not in m and "crtfc_key=***&page_no=1" in m
    assert FAKE_KEY not in mask_secret(f"crtfc_key={FAKE_KEY}")  # secret 미지정이어도 쿼리값은 마스킹


def test_load_api_key_env_only():
    assert load_api_key({"DART_API_KEY": " k "}) == "k"
    with pytest.raises(MissingApiKeyError) as e:
        load_api_key({})
    assert "DART_API_KEY" in str(e.value)


def test_repr_hides_key():
    c, _ = client([])
    assert FAKE_KEY not in repr(c)


def test_network_exception_message_masked():
    err = requests.ConnectionError(f"Max retries exceeded with url: /api/list.json?crtfc_key={FAKE_KEY}")
    c, sleeps = client([err, err, err])
    with pytest.raises(DartNetworkError) as e:
        c.get_json("list.json", {})
    assert FAKE_KEY not in str(e.value) and c.session.calls == 3 and sleeps == [1, 2]
    # 원본 예외가 체인으로 따라가지 않아야 한다 (traceback 에 URL 노출 방지)
    assert e.value.__cause__ is None and e.value.__context__ is None


def test_5xx_retry_then_success():
    c, _ = client([Resp(503), Resp(200, {"status": "000", "list": []})])
    assert c.get_json("list.json", {})[0] == "000" and c.session.calls == 2


@pytest.mark.parametrize("status,category", [("010", "auth"), ("011", "auth"), ("012", "auth"), ("901", "auth"),
                                             ("020", "rate_limit"), ("800", "maintenance"), ("100", "other"), ("900", "other")])
def test_status_codes_stop_without_retry(status, category):
    c, _ = client([Resp(200, {"status": status, "message": f"m crtfc_key={FAKE_KEY}"})])
    with pytest.raises(DartApiError) as e:
        c.get_json("list.json", {})
    assert e.value.category == category and c.session.calls == 1 and FAKE_KEY not in str(e.value)


def test_013_returned_as_no_data():
    c, _ = client([Resp(200, {"status": "013", "message": "없음"})])
    assert c.get_json("fnlttSinglAcntAll.json", {})[0] == "013"


def test_corpcode_error_response():
    c, _ = client([Resp(200, content=b"<result><status>010</status><message>x</message></result>")])
    with pytest.raises(DartApiError) as e:
        c.download_corp_codes()
    assert e.value.category == "auth"


def test_logging_filter_masks(caplog):
    logger = logging.getLogger("fde.test_mask")
    logger.addFilter(SecretMaskingFilter(FAKE_KEY))
    with caplog.at_level(logging.INFO, logger="fde.test_mask"):
        logger.info("url=%s", f"https://x?crtfc_key={FAKE_KEY}")
        try:
            raise RuntimeError(f"boom {FAKE_KEY}")
        except RuntimeError:
            logger.exception("failed")
    assert FAKE_KEY not in caplog.text


def test_saved_files_contain_no_key(tmp_path):
    """DESIGN 8장 최종 점검의 축소판: 저장 산출물 전체에서 키 문자열 0건."""
    params = {"corp_code": FAKE_CORP, "crtfc_key": FAKE_KEY, "bsns_year": "2099"}
    save_raw_json(tmp_path, FAKE_CORP, 2099, "R1", "fnlttSinglAcntAll.json", params, {"status": "000", "list": []})
    hits = [p for p in tmp_path.rglob("*") if p.is_file() and FAKE_KEY.encode() in p.read_bytes()]
    assert hits == []
