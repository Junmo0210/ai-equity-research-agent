"""pydantic 스키마 (DESIGN 4장). 각 기능의 입력·출력 스키마로 사용한다."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

NONSTANDARD_ACCOUNT_ID = "-표준계정코드 미사용-"

Period = Literal["thstrm", "frmtrm", "bfefrmtrm"]
PERIOD_OFFSET: dict[str, int] = {"thstrm": 0, "frmtrm": 1, "bfefrmtrm": 2}
PERIOD_LABEL_KO: dict[str, str] = {"thstrm": "당기", "frmtrm": "전기", "bfefrmtrm": "전전기"}


class Severity(str, Enum):
    ERROR = "ERROR"      # 처리 중단 사유
    REVIEW = "REVIEW"    # 사용자 확인 대상
    INFO = "INFO"


class Issue(BaseModel):
    """확인 대상 / 검토 대상 항목."""
    code: str
    severity: Severity = Severity.REVIEW
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class Company(BaseModel):
    corp_code: str
    corp_name: str
    corp_eng_name: str = ""
    stock_code: str = ""
    modify_date: str = ""


class Filing(BaseModel):
    """공시검색(list.json) 결과 1건."""
    corp_code: str
    corp_name: str = ""
    report_nm: str
    rcept_no: str
    rcept_dt: str
    flr_nm: str = ""
    rm: str = ""
    is_correction: bool = False
    fiscal_year: Optional[int] = None


class MappingStatus(str, Enum):
    MAPPED = "MAPPED"
    NONSTANDARD_REVIEW = "NONSTANDARD_REVIEW"
    UNMAPPED = "UNMAPPED"
    CRITICAL_MISSING_REVIEW = "CRITICAL_MISSING_REVIEW"


class RawLine(BaseModel):
    """API 응답 1행 × 1기간 (DESIGN 4.1)."""
    corp_code: str
    bsns_year: int
    reprt_code: str
    rcept_no: str
    fs_div: str
    sj_div: str
    sj_nm: str = ""
    account_id: str
    account_nm: str
    account_detail: str = ""
    ord: Optional[int] = None
    period: Period
    period_nm: str = ""
    fiscal_year: int
    amount: Optional[int] = None
    amount_raw: str = ""
    currency: str = ""
    source_file: str = ""
    source_sha256: str = ""

    @property
    def is_standard(self) -> bool:
        return self.account_id.strip() != NONSTANDARD_ACCOUNT_ID


class AccountKey(BaseModel, frozen=True):
    """연도·보고서 간 계정 정렬 키 (DESIGN 4.2)."""
    sj_div: str
    standard: bool
    ident: str  # 표준: account_id, 비표준: 공백 제거한 account_nm


class StatementFetch(BaseModel):
    """연도별 재무제표 수집 결과."""
    corp_code: str
    bsns_year: int
    reprt_code: str
    fs_div: str
    status: str                      # API status 코드
    rcept_no: Optional[str] = None
    row_count: int = 0
    source_file: str = ""
    source_sha256: str = ""
    lines: list[RawLine] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class SelectedValue(BaseModel):
    """기준값 1셀 (DESIGN 5장)."""
    key: AccountKey
    fiscal_year: int
    amount: Optional[int]
    source_rcept_no: Optional[str] = None
    source_bsns_year: Optional[int] = None
    source_period: Optional[str] = None
    fallback: bool = False            # 1순위에 값이 없어 대체 출처 사용
    latest_missing: bool = False      # 최신(1순위) 보고서에 계정 자체가 없음
    restated: bool = False            # 출처 간 값 상이


class RestatementEntry(BaseModel):
    key: AccountKey
    account_nm: str
    fiscal_year: int
    candidates: list[dict[str, Any]]  # [{bsns_year, period, rcept_no, amount}]
    max_diff: int


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    NA = "N/A"


class YearRun(BaseModel):
    """연도별 수집·대사 결과 요약."""
    bsns_year: int
    fetch: StatementFetch
    filings: list[Filing] = Field(default_factory=list)


class ValidationResult(BaseModel):
    rule_id: str
    fiscal_year: int
    basis: str                        # "selected" | "report_thstrm"
    status: ValidationStatus
    description: str
    lhs: Optional[int] = None
    rhs: Optional[int] = None
    diff: Optional[int] = None
    missing: list[str] = Field(default_factory=list)
    note: str = ""
