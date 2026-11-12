"""공용 픽스처. 모든 금액·식별자는 합성 데이터이며 실제 공시 수치가 아니다."""
from __future__ import annotations

from pathlib import Path

import pytest

from fde.normalize import AccountMap

ROOT = Path(__file__).resolve().parents[1]
FAKE_CORP = "99999999"
FAKE_KEY = "TESTKEY_0123456789abcdef0123456789abcdef"  # 합성 키 (실제 키 아님)


def row(bsns_year, sj_div, account_id, account_nm, th=None, fr=None, bf=None, *, rcept_no=None, ord_=1,
        include_bf=True, currency="KRW"):
    """fnlttSinglAcntAll 응답 형식의 합성 행."""
    r = {
        "rcept_no": rcept_no or f"{bsns_year + 1}0301000001",
        "reprt_code": "11011", "bsns_year": str(bsns_year), "corp_code": FAKE_CORP,
        "sj_div": sj_div, "sj_nm": sj_div, "account_id": account_id, "account_nm": account_nm,
        "account_detail": "-", "thstrm_nm": "제3기", "thstrm_amount": "" if th is None else str(th),
        "frmtrm_nm": "제2기", "frmtrm_amount": "" if fr is None else str(fr),
        "ord": str(ord_), "currency": currency,
    }
    if include_bf:
        r["bfefrmtrm_nm"] = "제1기"
        r["bfefrmtrm_amount"] = "" if bf is None else str(bf)
    return r


@pytest.fixture
def amap() -> AccountMap:
    return AccountMap.from_csv(ROOT / "config" / "account_map.csv")


class FakeClient:
    """DartClient 대역. endpoint 별 응답 큐를 돌려주고 호출을 기록한다."""

    def __init__(self, responses: dict[str, list[tuple[str, dict]]]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if params.get("fs_div") == "OFS":
            raise AssertionError("OFS 호출 금지 (D-02)")
        return self.responses[endpoint].pop(0)
