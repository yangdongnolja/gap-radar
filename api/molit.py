"""
국토교통부 아파트 매매 실거래가 조회 모듈.

Streamlit 앱(app.py)의 fetch_one()/parse_item() 로직을 참고해 독립적으로
다시 작성했다. streamlit(st.*)에 전혀 의존하지 않으므로, FastAPI를 비롯해
어떤 파이썬 프로세스에서도 그대로 import해서 쓸 수 있다.
"""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 이 파일(api/molit.py) 기준으로 프로젝트 루트(아파트아파트/)의 .env를 명시적으로
# 찾는다. uvicorn을 api/ 폴더에서 실행하든 프로젝트 루트에서 실행하든 항상
# 같은 .env(기존 Streamlit 앱과 동일한 키)를 읽기 위함이다.
_ROOT_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ROOT_ENV if _ROOT_ENV.exists() else None)

MOLIT_API_KEY = os.getenv("MOLIT_API_KEY", "").strip()

TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"

# 기존 app.py와 동일한 서울 25개 자치구 코드. 이 프로젝트는 현재 서울만 지원한다.
SEOUL_GU_CODES = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200",
    "광진구": "11215", "동대문구": "11230", "중랑구": "11260", "성북구": "11290",
    "강북구": "11305", "도봉구": "11320", "노원구": "11350", "은평구": "11380",
    "서대문구": "11410", "마포구": "11440", "양천구": "11470", "강서구": "11500",
    "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
    "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710",
    "강동구": "11740",
}
SUPPORTED_SIDO_ALIASES = {"서울특별시", "서울", "서울시"}


class MolitError(Exception):
    """이 모듈에서 발생하는 모든 오류의 기반 클래스."""


class MolitConfigError(MolitError):
    """국토부 API 키가 설정되어 있지 않을 때."""


class MolitRequestError(MolitError):
    """국토부 서버 호출 자체가 실패했을 때 (네트워크/HTTP 오류)."""


class MolitResponseError(MolitError):
    """국토부가 응답은 했지만 내용이 비정상(빈 응답/파싱 실패/오류코드)일 때."""


class InvalidRegionError(MolitError):
    """지원하지 않는 시도/자치구를 요청했을 때."""


def get_gu_code(sido: Optional[str], gu: str) -> str:
    if sido and sido not in SUPPORTED_SIDO_ALIASES:
        raise InvalidRegionError(f"현재 '서울특별시'만 지원합니다. (요청한 sido: {sido})")
    code = SEOUL_GU_CODES.get(gu)
    if not code:
        raise InvalidRegionError(
            f"'{gu}'는 지원하지 않는 자치구입니다. 지원 목록: {', '.join(SEOUL_GU_CODES)}"
        )
    return code


def all_gu_names() -> list[str]:
    return list(SEOUL_GU_CODES.keys())


def _parse_item(item: ET.Element, gu_name: str, year: int, month: int) -> Optional[dict]:
    def text(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    if text("cdealType"):  # 해제(취소)된 거래는 제외
        return None

    day = text("dealDay")
    price_str = text("dealAmount").replace(",", "")
    try:
        price = int(price_str)
    except ValueError:
        price = 0

    try:
        area = float(text("excluUseAr"))
    except ValueError:
        area = 0.0

    floor_str = text("floor")
    try:
        floor: Optional[int] = int(floor_str)
    except ValueError:
        floor = None

    return {
        "deal_date": f"{year:04d}-{month:02d}-{(day.zfill(2) if day else '01')}",
        "gu": gu_name,
        "dong": text("umdNm"),
        "complex_name": text("aptNm"),
        "area": area,
        "floor": floor,
        "price": price,  # 단위: 만원
        "jibun": text("jibun"),
    }


# ------------------------------------------------------------------
# 아주 단순한 인메모리 TTL 캐시 (Redis/DB 없이 딱 필요한 만큼만).
# 같은 (자치구코드, 연월) 조회가 반복되면 30분 동안 국토부를 다시 부르지 않는다.
# 서버 프로세스가 재시작되면 초기화되며, 별도 저장소는 두지 않는다.
# ------------------------------------------------------------------
_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_CACHE_TTL_SECONDS = 1800  # 30분 — 기존 Streamlit 앱의 fetch_one 캐시 TTL과 동일하게 맞춤


def fetch_transactions(gu_name: str, year: int, month: int, sido: Optional[str] = None) -> list[dict]:
    """지정한 자치구·연·월의 아파트 매매 실거래 목록을 반환한다.

    국토부 응답을 딕셔너리 리스트로 변환하며, 페이지네이션과 30분 캐싱을
    내부에서 처리한다.
    """
    if not MOLIT_API_KEY:
        raise MolitConfigError(
            "MOLIT_API_KEY 환경변수가 설정되어 있지 않습니다. .env 파일을 확인해주세요."
        )

    gu_code = get_gu_code(sido, gu_name)
    ymd = f"{year:04d}{month:02d}"

    cache_key = (gu_code, ymd)
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]

    rows: list[dict] = []
    page = 1
    while True:
        params = {
            "serviceKey": MOLIT_API_KEY,
            "LAWD_CD": gu_code,
            "DEAL_YMD": ymd,
            "numOfRows": "1000",
            "pageNo": str(page),
        }
        try:
            resp = requests.get(TRADE_URL, params=params, verify=False, timeout=15)
        except requests.RequestException as e:
            raise MolitRequestError(f"국토부 서버 호출에 실패했습니다: {e}") from e

        if resp.status_code != 200 or not resp.content.strip().startswith(b"<"):
            raise MolitResponseError(
                f"국토부 서버가 비정상 응답을 반환했습니다 (HTTP {resp.status_code}): "
                f"{resp.text[:200]!r}"
            )

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            raise MolitResponseError(f"국토부 응답 XML 파싱에 실패했습니다: {e}") from e

        result_code = root.findtext(".//resultCode")
        if result_code is not None and result_code not in ("00", "000"):
            result_msg = root.findtext(".//resultMsg")
            raise MolitResponseError(f"국토부 API 오류 {result_code}: {result_msg}")

        items = root.findall(".//item")
        if not items:
            break

        for item in items:
            row = _parse_item(item, gu_name, year, month)
            if row is not None:
                rows.append(row)

        total_count = int(root.findtext(".//totalCount") or 0)
        if page * 1000 >= total_count:
            break
        page += 1

    _CACHE[cache_key] = (time.time() + _CACHE_TTL_SECONDS, rows)
    return rows
