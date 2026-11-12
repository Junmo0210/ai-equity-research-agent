"""출처 간 값 차이 → 재작성 로그 (DESIGN 5장 규칙 3)."""
from __future__ import annotations

from typing import Any, Optional

from .models import AccountKey, RestatementEntry


def compare_candidates(key: AccountKey, account_nm: str, fiscal_year: int,
                       candidates: list[dict[str, Any]]) -> Optional[RestatementEntry]:
    """값이 있는 후보들끼리 금액이 다르면 RestatementEntry, 같으면 None."""
    amounts = [c["amount"] for c in candidates if c.get("amount") is not None]
    if len(set(amounts)) <= 1:
        return None
    return RestatementEntry(key=key, account_nm=account_nm, fiscal_year=fiscal_year,
                            candidates=list(candidates), max_diff=max(amounts) - min(amounts))
