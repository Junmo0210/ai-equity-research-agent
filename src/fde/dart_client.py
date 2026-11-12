"""OpenDART API 호출: 상태코드 처리, 재시도, 인증키 마스킹, 원본 저장 (DESIGN 3·8장).

인증키는 환경변수 DART_API_KEY 로만 읽는다. 키는 URL 쿼리(crtfc_key)로 전달되므로
예외 메시지·로그에 URL이 섞일 수 있다. 이 모듈 밖으로 나가는 모든 메시지는 mask_secret 을 거친다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import requests

BASE_URL = "https://opendart.fss.or.kr/api"
ENV_KEY_NAME = "DART_API_KEY"
MASK = "***"

_KEY_QUERY_RE = re.compile(r"(crtfc_key=)[^&\s'\"]*", re.IGNORECASE)

# 상태코드 분류 (DESIGN 3.2)
STATUS_OK = "000"
STATUS_NO_DATA = "013"
AUTH_STATUSES = {"010", "011", "012", "901"}
STATUS_RATE_LIMIT = "020"
STATUS_MAINTENANCE = "800"

AUTH_HINT = {
    "010": "등록되지 않은 인증키입니다.",
    "011": "사용할 수 없는 인증키입니다.",
    "012": "접근할 수 없는 IP입니다.",
    "901": "인증키 사용 기간이 만료되었습니다.",
}

RETRY_MAX = 3


def mask_secret(text: Any, secret: Optional[str] = None) -> str:
    """crtfc_key=... 쿼리값과 키 문자열 자체를 마스킹한다."""
    s = str(text)
    s = _KEY_QUERY_RE.sub(r"\1" + MASK, s)
    if secret:
        s = s.replace(secret, MASK)
    return s


class SecretMaskingFilter(logging.Filter):
    """로그 레코드의 메시지·인자에서 인증키를 마스킹한다."""

    def __init__(self, secret: Optional[str] = None):
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = mask_secret(msg, self.secret)
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            # 예외 텍스트를 미리 마스킹해 둔다 (Formatter 가 exc_text 를 재사용)
            formatted = logging.Formatter().formatException(record.exc_info)
            record.exc_text = mask_secret(formatted, self.secret)
            record.exc_info = None
        return True


class DartError(Exception):
    """키가 제거된 메시지만 담는 예외."""


class MissingApiKeyError(DartError):
    pass


class DartApiError(DartError):
    def __init__(self, status: str, message: str, category: str):
        self.status = status
        self.category = category  # auth | rate_limit | maintenance | other
        super().__init__(f"[{status}] {message}")


class DartNetworkError(DartError):
    pass


def load_api_key(env: Optional[dict[str, str]] = None) -> str:
    env = os.environ if env is None else env
    key = (env.get(ENV_KEY_NAME) or "").strip()
    if not key:
        raise MissingApiKeyError(
            f"환경변수 {ENV_KEY_NAME} 가 설정되어 있지 않습니다. README 의 setx 안내를 따른 뒤 새 터미널을 여세요."
        )
    return key


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class DartClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = BASE_URL,
    ):
        self._key = api_key if api_key is not None else load_api_key()
        self.session = session or requests.Session()
        self.timeout = timeout
        self.sleep = sleep
        self.base_url = base_url.rstrip("/")
        self.log = logging.getLogger("fde.dart_client")
        self.log.addFilter(SecretMaskingFilter(self._key))

    def __repr__(self) -> str:  # 키 노출 방지
        return f"DartClient(base_url={self.base_url!r}, api_key={MASK})"

    def mask(self, text: Any) -> str:
        return mask_secret(text, self._key)

    # ---- 저수준 요청 ------------------------------------------------------
    def _request(self, endpoint: str, params: dict[str, Any]) -> requests.Response:
        url = f"{self.base_url}/{endpoint}"
        query = {**params, "crtfc_key": self._key}
        last_err: Optional[str] = None
        for attempt in range(1, RETRY_MAX + 1):
            try:
                resp = self.session.get(url, params=query, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = f"{type(e).__name__}: {self.mask(e)}"
            else:
                if resp.status_code >= 500:
                    last_err = f"HTTP {resp.status_code}"
                elif resp.status_code >= 400:
                    raise DartNetworkError(f"{endpoint}: HTTP {resp.status_code}")
                else:
                    return resp
            self.log.warning("%s 요청 실패 (시도 %d/%d): %s", endpoint, attempt, RETRY_MAX, last_err)
            if attempt < RETRY_MAX:
                self.sleep(2 ** (attempt - 1))
        raise DartNetworkError(f"{endpoint}: {RETRY_MAX}회 재시도 후 실패 — {last_err}")

    @staticmethod
    def check_status(endpoint: str, data: dict[str, Any]) -> str:
        """status 를 검사한다. 000/013 은 반환, 그 외는 DartApiError (키 미포함)."""
        status = str(data.get("status", "")).strip()
        message = mask_secret(data.get("message", ""))
        if status in (STATUS_OK, STATUS_NO_DATA):
            return status
        if status in AUTH_STATUSES:
            raise DartApiError(status, f"{endpoint}: 인증 오류 — {AUTH_HINT[status]} (키 값은 표시하지 않음)", "auth")
        if status == STATUS_RATE_LIMIT:
            raise DartApiError(status, f"{endpoint}: 요청 제한 초과 — 잠시 후 다시 실행하세요. ({message})", "rate_limit")
        if status == STATUS_MAINTENANCE:
            raise DartApiError(status, f"{endpoint}: OpenDART 시스템 점검 중 ({message})", "maintenance")
        raise DartApiError(status or "?", f"{endpoint}: {message or '알 수 없는 응답'}", "other")

    # ---- 공개 메서드 ------------------------------------------------------
    def get_json(self, endpoint: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """JSON API 호출. (status, 응답 dict) 반환. 000/013 외 상태는 예외."""
        resp = self._request(endpoint, params)
        try:
            data = resp.json()
        except ValueError:
            raise DartNetworkError(f"{endpoint}: JSON 이 아닌 응답") from None
        status = self.check_status(endpoint, data)
        return status, data

    def download_corp_codes(self) -> bytes:
        """corpCode.xml 은 ZIP 으로 내려온다. 오류 시 JSON/XML 상태 응답이 온다."""
        resp = self._request("corpCode.xml", {})
        content = resp.content
        if content[:2] == b"PK":
            return content
        # 오류 응답 (XML 또는 JSON) 해석
        text = content.decode("utf-8", errors="replace")
        status_m = re.search(r"<status>(\d+)</status>", text) or re.search(r'"status"\s*:\s*"(\d+)"', text)
        msg_m = re.search(r"<message>(.*?)</message>", text) or re.search(r'"message"\s*:\s*"([^"]*)"', text)
        self.check_status(
            "corpCode.xml",
            {"status": status_m.group(1) if status_m else "", "message": msg_m.group(1) if msg_m else "ZIP 이 아닌 응답"},
        )
        raise DartNetworkError("corpCode.xml: ZIP 이 아닌 응답")


def save_raw_json(
    raw_dir: Path,
    corp_code: str,
    bsns_year: int,
    rcept_no: str,
    endpoint: str,
    params: dict[str, Any],
    response: dict[str, Any],
) -> tuple[Path, str]:
    """원본 응답을 키 없이 저장하고 (경로, SHA-256) 반환. 경로: raw/{corp_code}/{bsns_year}_{rcept_no}.json"""
    safe_params = {k: v for k, v in params.items() if k.lower() != "crtfc_key"}
    payload = {
        "request": {"endpoint": endpoint, "params": safe_params},
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "response": response,
    }
    out_dir = Path(raw_dir) / corp_code
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{bsns_year}_{rcept_no}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return path, sha256_file(path)
