"""회사 고유번호 목록 캐시·검색 (DESIGN 6장 [1], D-07).

정확히 일치하는 상장사(종목코드 보유) 1건일 때만 자동 확정한다.
"""
from __future__ import annotations

import io
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Optional

from .models import Company

CACHE_FILENAME = "corpCode.xml"  # 파일명에 인증키를 쓰지 않는다


class CompanyResolutionError(Exception):
    def __init__(self, query: str, reason: str, candidates: list[Company]):
        self.query = query
        self.reason = reason
        self.candidates = candidates
        super().__init__(f"'{query}': {reason} (후보 {len(candidates)}건)")


def extract_xml_from_zip(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise ValueError("corpCode ZIP 안에 XML 이 없습니다.")
        return zf.read(names[0])


def parse_corp_xml(xml_bytes: bytes) -> list[Company]:
    root = ET.fromstring(xml_bytes)
    out: list[Company] = []
    for el in root.iter("list"):
        def t(tag: str) -> str:
            return (el.findtext(tag) or "").strip()
        out.append(
            Company(
                corp_code=t("corp_code"),
                corp_name=t("corp_name"),
                corp_eng_name=t("corp_eng_name"),
                stock_code=t("stock_code"),
                modify_date=t("modify_date"),
            )
        )
    return out


def load_registry(cache_dir: Path, client=None, max_age_days: float = 7, now: Optional[float] = None) -> tuple[list[Company], Path, bool]:
    """캐시가 없거나 오래되면 client 로 내려받아 갱신. (회사목록, 캐시경로, 새로받음여부) 반환."""
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / CACHE_FILENAME
    now = time.time() if now is None else now
    fresh = cache_path.exists() and (now - cache_path.stat().st_mtime) < max_age_days * 86400
    downloaded = False
    if not fresh:
        if client is None:
            if not cache_path.exists():
                raise FileNotFoundError(f"고유번호 캐시가 없습니다: {cache_path}")
        else:
            xml_bytes = extract_xml_from_zip(client.download_corp_codes())
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(xml_bytes)
            downloaded = True
    return parse_corp_xml(cache_path.read_bytes()), cache_path, downloaded


def resolve_company(name: str, companies: list[Company], max_candidates: int = 20) -> Company:
    """회사명 정확 일치 + 상장사(종목코드 존재) 1건이면 반환. 아니면 후보와 함께 CompanyResolutionError."""
    q = name.strip()
    exact = [c for c in companies if c.corp_name == q]
    exact_listed = [c for c in exact if c.stock_code]
    if len(exact_listed) == 1:
        return exact_listed[0]
    if len(exact_listed) > 1:
        raise CompanyResolutionError(q, "정확히 일치하는 상장사가 여러 건입니다", exact_listed[:max_candidates])
    # 0건: 정확 일치 비상장 + 부분 일치 상장사를 후보로 제시
    partial_listed = [c for c in companies if q in c.corp_name and c.stock_code and c not in exact]
    candidates = (exact + partial_listed)[:max_candidates]
    reason = "정확히 일치하는 회사가 있으나 상장사가 아닙니다" if exact else "정확히 일치하는 상장사가 없습니다"
    raise CompanyResolutionError(q, reason, candidates)
