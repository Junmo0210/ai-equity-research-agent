"""응답 행 → RawLine 펼치기, 금액 파싱, 계정 키, 매핑 상태 (DESIGN 4장, D-04·D-05·D-09)."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import (
    NONSTANDARD_ACCOUNT_ID,
    PERIOD_OFFSET,
    AccountKey,
    Issue,
    MappingStatus,
    RawLine,
    Severity,
)

_INT_RE = re.compile(r"^-?\d+$")
EMPTY_TOKENS = {"", "-"}


class AmountParseError(ValueError):
    pass


def parse_amount(raw: Any) -> Optional[int]:
    """원본 금액 문자열 → int. 공란·'-' 는 None(값 없음, 0 아님). float 변환 금지.

    허용: 선행 부호 '-', 천 단위 쉼표. 그 외(소수점, 괄호, 문자)는 AmountParseError.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s in EMPTY_TOKENS:
        return None
    t = s.replace(",", "")
    if not _INT_RE.match(t):
        raise AmountParseError(f"정수로 해석할 수 없는 금액: {s!r}")
    return int(t)


def normalize_name(name: str) -> str:
    """비표준계정 키용 정규화: 공백 제거 수준만 (DESIGN 4.2)."""
    return re.sub(r"\s+", "", name or "")


def is_standard_id(account_id: str) -> bool:
    return (account_id or "").strip() not in (NONSTANDARD_ACCOUNT_ID, "")


def account_key(sj_div: str, account_id: str, account_nm: str) -> AccountKey:
    if is_standard_id(account_id):
        return AccountKey(sj_div=sj_div, standard=True, ident=account_id.strip())
    return AccountKey(sj_div=sj_div, standard=False, ident=normalize_name(account_nm))


def line_key(line: RawLine) -> AccountKey:
    return account_key(line.sj_div, line.account_id, line.account_nm)


def _to_int_or_none(v: Any) -> Optional[int]:
    s = str(v).strip() if v is not None else ""
    return int(s) if _INT_RE.match(s) else None


def expand_rows(
    rows: Iterable[dict[str, Any]],
    *,
    fs_div: str,
    source_file: str = "",
    source_sha256: str = "",
) -> tuple[list[RawLine], list[Issue]]:
    """fnlttSinglAcntAll 응답 list → 기간별 RawLine.

    응답 행에 해당 기간 금액 필드가 존재할 때만 그 기간의 RawLine 을 만든다(값이 공란이어도 생성, amount=None).
    """
    lines: list[RawLine] = []
    issues: list[Issue] = []
    for idx, row in enumerate(rows):
        bsns_year = int(str(row.get("bsns_year", "")).strip())
        for period, offset in PERIOD_OFFSET.items():
            field = f"{period}_amount"
            if field not in row:
                continue
            raw = row.get(field)
            raw_s = "" if raw is None else str(raw)
            try:
                amount = parse_amount(raw_s)
            except AmountParseError as e:
                amount = None
                issues.append(Issue(
                    code="AMOUNT_PARSE_ERROR",
                    message=str(e),
                    context={"row": idx, "sj_div": row.get("sj_div"), "account_id": row.get("account_id"),
                             "account_nm": row.get("account_nm"), "period": period},
                ))
            lines.append(RawLine(
                corp_code=str(row.get("corp_code", "")),
                bsns_year=bsns_year,
                reprt_code=str(row.get("reprt_code", "")),
                rcept_no=str(row.get("rcept_no", "")),
                fs_div=fs_div,
                sj_div=str(row.get("sj_div", "")),
                sj_nm=str(row.get("sj_nm", "")),
                account_id=str(row.get("account_id", "")),
                account_nm=str(row.get("account_nm", "")),
                account_detail=str(row.get("account_detail", "") or ""),
                ord=_to_int_or_none(row.get("ord")),
                period=period,
                period_nm=str(row.get(f"{period}_nm", "") or ""),
                fiscal_year=bsns_year - offset,
                amount=amount,
                amount_raw=raw_s,
                currency=str(row.get("currency", "") or ""),
                source_file=source_file,
                source_sha256=source_sha256,
            ))
    return lines, issues


# ---- 매핑 -------------------------------------------------------------------

class AccountMap:
    """account_map.csv: (sj_div, account_id) → 검증용 핵심 항목. 정확 일치만 (D-04)."""

    def __init__(self, entries: list[dict[str, str]]):
        self.entries = entries
        self._by_key: dict[tuple[str, str], str] = {}
        for e in entries:
            self._by_key[(e["sj_div"].strip(), e["account_id"].strip())] = e["item"].strip()

    @classmethod
    def from_csv(cls, path: Path) -> "AccountMap":
        with open(path, encoding="utf-8", newline="") as f:
            return cls(list(csv.DictReader(f)))

    def item_for(self, sj_div: str, account_id: str) -> Optional[str]:
        return self._by_key.get((sj_div, (account_id or "").strip()))

    def items_by_sj(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for (sj, _), item in self._by_key.items():
            out[sj].add(item)
        return out


def mapping_status(line_or_key: RawLine | AccountKey, amap: AccountMap) -> MappingStatus:
    key = line_key(line_or_key) if isinstance(line_or_key, RawLine) else line_or_key
    if not key.standard:
        return MappingStatus.NONSTANDARD_REVIEW
    if amap.item_for(key.sj_div, key.ident):
        return MappingStatus.MAPPED
    return MappingStatus.UNMAPPED


def review_issues_for_report(lines: list[RawLine], amap: AccountMap, skip_sj: tuple[str, ...] = ("SCE",)) -> list[Issue]:
    """한 보고서(한 bsns_year)의 RawLine 에서 확인 대상을 뽑는다.

    - 비표준계정 (D-05)
    - 한 재무제표·한 기간 안의 계정 키 중복 (DESIGN 4.2)
    """
    issues: list[Issue] = []
    seen_nonstd: set[tuple[str, str]] = set()
    groups: dict[tuple[str, str, AccountKey], list[RawLine]] = defaultdict(list)
    for ln in lines:
        if ln.sj_div in skip_sj:
            continue
        k = line_key(ln)
        if not k.standard and (ln.sj_div, k.ident) not in seen_nonstd:
            seen_nonstd.add((ln.sj_div, k.ident))
            issues.append(Issue(
                code="NONSTANDARD_ACCOUNT",
                message=f"비표준계정: [{ln.sj_div}] {ln.account_nm}",
                context={"bsns_year": ln.bsns_year, "rcept_no": ln.rcept_no, "sj_div": ln.sj_div,
                         "account_nm": ln.account_nm, "ord": ln.ord},
            ))
        groups[(ln.sj_div, ln.period, k)].append(ln)
    for (sj, period, k), grp in groups.items():
        if len(grp) > 1 and period == "thstrm":
            issues.append(Issue(
                code="DUPLICATE_ACCOUNT_KEY",
                message=f"[{sj}] 계정 키 중복 {len(grp)}건: {k.ident}",
                context={"bsns_year": grp[0].bsns_year, "sj_div": sj, "ident": k.ident,
                         "account_nms": [g.account_nm for g in grp], "ords": [g.ord for g in grp]},
            ))
    return issues


def item_values(
    lines: Iterable[RawLine], amap: AccountMap, period: str = "thstrm"
) -> tuple[dict[tuple[str, str], Optional[int]], list[Issue]]:
    """보고서 원본에서 (sj_div, item) → 금액. 같은 항목에 행이 여러 개면 모호 → 값 없음 + 확인 대상."""
    found: dict[tuple[str, str], list[RawLine]] = defaultdict(list)
    for ln in lines:
        if ln.period != period:
            continue
        item = amap.item_for(ln.sj_div, ln.account_id)
        if item:
            found[(ln.sj_div, item)].append(ln)
    out: dict[tuple[str, str], Optional[int]] = {}
    issues: list[Issue] = []
    for k, grp in found.items():
        if len(grp) == 1:
            out[k] = grp[0].amount
        else:
            out[k] = None
            issues.append(Issue(
                code="AMBIGUOUS_MAPPED_ITEM",
                message=f"핵심 항목 {k[1]} ({k[0]}) 에 해당하는 행이 {len(grp)}건 — 값 판단 보류",
                context={"sj_div": k[0], "item": k[1], "account_nms": [g.account_nm for g in grp]},
            ))
    return out, issues
