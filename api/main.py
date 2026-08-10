"""
GAP-RADAR 독립 FastAPI 서버.

기존 Streamlit 앱(app.py)과 완전히 분리된 프로세스로 동작한다. app.py는
import하지 않으며 어떤 방식으로도 수정하지 않는다. 국토부 실거래가 조회
로직은 이 폴더의 molit.py를 사용한다.

실행:
    cd api
    uvicorn main:app --reload --port 8000

문서:
    http://127.0.0.1:8000/docs
    http://127.0.0.1:8000/openapi.json
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

import molit

_ROOT_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ROOT_ENV if _ROOT_ENV.exists() else None)

GAP_RADAR_API_KEY = os.getenv("GAP_RADAR_API_KEY", "").strip()

app = FastAPI(
    title="GAP-RADAR 실거래가 조회 API",
    description=(
        "서울 아파트 매매 실거래가를 조회하는 읽기 전용 API. "
        "기존 Streamlit 앱(GAP-RADAR)과 독립적으로 동작하며, 같은 국토부 API 키를 재사용한다."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# 인증: X-API-Key 헤더. /health, /docs, /openapi.json 은 제외.
# ------------------------------------------------------------------
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: Optional[str] = Depends(_api_key_header)) -> str:
    if not GAP_RADAR_API_KEY:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "server_misconfigured",
                "message": "GAP_RADAR_API_KEY가 서버에 설정되어 있지 않습니다.",
            },
        )
    if not api_key or api_key != GAP_RADAR_API_KEY:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "유효한 X-API-Key 헤더가 필요합니다."},
        )
    return api_key


# ------------------------------------------------------------------
# 공통 검증/오류 변환 헬퍼
# ------------------------------------------------------------------
def _check_range(min_v, max_v, field: str) -> None:
    if min_v is not None and max_v is not None and min_v > max_v:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"invalid_{field}_range",
                "message": f"{field} 최소값이 최대값보다 큽니다.",
            },
        )


def _parse_year_month(ym: str, field: str) -> tuple[int, int]:
    try:
        y_str, m_str = ym.split("-")
        y, m = int(y_str), int(m_str)
        if not (1 <= m <= 12) or y < 2006:
            raise ValueError
        return y, m
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_date",
                "message": f"{field} 형식이 올바르지 않습니다. 예: 2026-06",
            },
        )


def _run_molit(fn, *args, **kwargs):
    """molit.py의 예외를 일관된 JSON 오류(HTTPException)로 변환한다."""
    try:
        return fn(*args, **kwargs)
    except molit.InvalidRegionError as e:
        raise HTTPException(status_code=400, detail={"error": "invalid_region", "message": str(e)})
    except molit.MolitConfigError as e:
        raise HTTPException(status_code=503, detail={"error": "molit_api_key_missing", "message": str(e)})
    except molit.MolitRequestError as e:
        raise HTTPException(status_code=502, detail={"error": "molit_api_request_failed", "message": str(e)})
    except molit.MolitResponseError as e:
        raise HTTPException(status_code=502, detail={"error": "molit_api_response_invalid", "message": str(e)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "message": "예상치 못한 서버 오류가 발생했습니다."},
    )


# ------------------------------------------------------------------
# 응답 모델
# ------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str


class Transaction(BaseModel):
    deal_date: str
    gu: str
    dong: str
    complex_name: str
    area: float
    floor: Optional[int]
    price: int = Field(description="거래금액, 단위: 만원")
    jibun: str


class TransactionsResponse(BaseModel):
    count: int
    filters: dict
    results: list[Transaction]


class ComplexAnalysisItem(BaseModel):
    complex_name: str
    gu: str
    dong: str
    area: float = Field(description="대표 전용면적(㎡, 반올림)")
    transaction_count: int
    average_price: float = Field(description="단위: 만원")
    min_price: int = Field(description="단위: 만원")
    max_price: int = Field(description="단위: 만원")
    latest_price: int = Field(description="단위: 만원")
    latest_deal_date: str
    june_transaction_count: Optional[int] = Field(None, description="조회범위 중 이전 달 거래건수")
    june_average_price: Optional[float] = Field(None, description="조회범위 중 이전 달 평균가(만원)")
    july_transaction_count: Optional[int] = Field(None, description="조회범위 중 이후 달 거래건수")
    july_average_price: Optional[float] = Field(None, description="조회범위 중 이후 달 평균가(만원)")
    price_change_percent: Optional[float] = Field(None, description="이전 달 대비 이후 달 평균가 변화율(%)")


class ComplexAnalysisResponse(BaseModel):
    count: int
    filters: dict
    note: str
    results: list[ComplexAnalysisItem]


# ------------------------------------------------------------------
# /health — 인증 불필요
# ------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, summary="서버 상태 확인")
async def health():
    return {"status": "ok"}


# ------------------------------------------------------------------
# /api/v1/transactions
# ------------------------------------------------------------------
@app.get(
    "/api/v1/transactions",
    response_model=TransactionsResponse,
    summary="아파트 매매 실거래 목록 조회",
    dependencies=[Depends(require_api_key)],
)
async def get_transactions(
    sido: Optional[str] = Query("서울특별시", description="현재 서울특별시만 지원"),
    gu: Optional[str] = Query(
        None, description="자치구명 (예: 강남구). 생략 시 서울 전체 25개 구를 조회합니다 — 느릴 수 있습니다."
    ),
    year: Optional[int] = Query(None, description="조회 연도. month와 함께 사용. 생략 시 지난달 기준."),
    month: Optional[int] = Query(None, ge=1, le=12, description="조회 월(1~12)"),
    min_price: Optional[int] = Query(None, ge=0, description="최소 거래금액(만원)"),
    max_price: Optional[int] = Query(None, ge=0, description="최대 거래금액(만원)"),
    min_area: Optional[float] = Query(None, ge=0, description="최소 전용면적(㎡)"),
    max_area: Optional[float] = Query(None, ge=0, description="최대 전용면적(㎡)"),
    complex_name: Optional[str] = Query(None, description="단지명 부분 검색"),
):
    _check_range(min_price, max_price, "price")
    _check_range(min_area, max_area, "area")

    if year is None or month is None:
        first_of_this_month = date.today().replace(day=1)
        prev_month_date = first_of_this_month - timedelta(days=1)
        year, month = prev_month_date.year, prev_month_date.month

    gu_names = [gu] if gu else molit.all_gu_names()

    all_rows: list[dict] = []
    for gu_name in gu_names:
        rows = _run_molit(molit.fetch_transactions, gu_name, year, month, sido)
        all_rows.extend(rows)

    if min_price is not None:
        all_rows = [r for r in all_rows if r["price"] >= min_price]
    if max_price is not None:
        all_rows = [r for r in all_rows if r["price"] <= max_price]
    if min_area is not None:
        all_rows = [r for r in all_rows if r["area"] >= min_area]
    if max_area is not None:
        all_rows = [r for r in all_rows if r["area"] <= max_area]
    if complex_name:
        kw = complex_name.strip()
        all_rows = [r for r in all_rows if kw in r["complex_name"]]

    return {
        "count": len(all_rows),
        "filters": {
            "sido": sido, "gu": gu, "year": year, "month": month,
            "min_price": min_price, "max_price": max_price,
            "min_area": min_area, "max_area": max_area,
            "complex_name": complex_name,
        },
        "results": all_rows,
    }


# ------------------------------------------------------------------
# /api/v1/complex-analysis — ChatGPT 연동용 핵심 API
# ------------------------------------------------------------------
@app.get(
    "/api/v1/complex-analysis",
    response_model=ComplexAnalysisResponse,
    summary="단지별 실거래 분석 (ChatGPT 연동용 핵심 API)",
    dependencies=[Depends(require_api_key)],
)
async def complex_analysis(
    sido: Optional[str] = Query("서울특별시"),
    gu: Optional[str] = Query(None, description="생략 시 서울 전체 25개 구를 조회합니다 — 느릴 수 있습니다."),
    from_year_month: str = Query(..., description="조회 시작 연월, 예: 2026-06"),
    to_year_month: str = Query(..., description="조회 종료 연월, 예: 2026-07 (최대 3개월)"),
    min_price: Optional[int] = Query(None, ge=0, description="최소 거래금액(만원)"),
    max_price: Optional[int] = Query(None, ge=0, description="최대 거래금액(만원)"),
    min_area: Optional[float] = Query(None, ge=0, description="최소 전용면적(㎡)"),
    max_area: Optional[float] = Query(None, ge=0, description="최대 전용면적(㎡)"),
    min_transactions: int = Query(1, ge=1, description="이 건수 미만인 단지는 결과에서 제외"),
):
    _check_range(min_price, max_price, "price")
    _check_range(min_area, max_area, "area")

    fy, fm = _parse_year_month(from_year_month, "from_year_month")
    ty, tm = _parse_year_month(to_year_month, "to_year_month")

    months: list[tuple[int, int]] = []
    y, m = fy, fm
    while (y, m) <= (ty, tm):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
        if len(months) > 3:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_date_range", "message": "조회 기간은 최대 3개월까지만 가능합니다."},
            )
    if not months:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_date_range", "message": "from_year_month가 to_year_month보다 늦습니다."},
        )

    gu_names = [gu] if gu else molit.all_gu_names()

    all_rows: list[dict] = []
    for gu_name in gu_names:
        for yy, mm in months:
            rows = _run_molit(molit.fetch_transactions, gu_name, yy, mm, sido)
            all_rows.extend(rows)

    if min_price is not None:
        all_rows = [r for r in all_rows if r["price"] >= min_price]
    if max_price is not None:
        all_rows = [r for r in all_rows if r["price"] <= max_price]
    if min_area is not None:
        all_rows = [r for r in all_rows if r["area"] >= min_area]
    if max_area is not None:
        all_rows = [r for r in all_rows if r["area"] <= max_area]

    # (자치구, 법정동, 단지명, 반올림 전용면적) 기준으로 그룹화한다.
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in all_rows:
        key = (r["gu"], r["dong"], r["complex_name"], round(r["area"]))
        groups[key].append(r)

    month_labels = [f"{yy:04d}-{mm:02d}" for yy, mm in months]
    earlier_label = month_labels[0] if len(month_labels) >= 2 else None
    later_label = month_labels[-1] if len(month_labels) >= 2 else None

    results = []
    for (gu_name, dong, name, area), deals in groups.items():
        if len(deals) < min_transactions:
            continue
        prices = [d["price"] for d in deals]
        latest = max(deals, key=lambda d: d["deal_date"])

        item = {
            "complex_name": name,
            "gu": gu_name,
            "dong": dong,
            "area": area,
            "transaction_count": len(deals),
            "average_price": round(sum(prices) / len(prices), 1),
            "min_price": min(prices),
            "max_price": max(prices),
            "latest_price": latest["price"],
            "latest_deal_date": latest["deal_date"],
        }

        if earlier_label and later_label and earlier_label != later_label:
            earlier_prices = [d["price"] for d in deals if d["deal_date"][:7] == earlier_label]
            later_prices = [d["price"] for d in deals if d["deal_date"][:7] == later_label]
            if earlier_prices:
                item["june_transaction_count"] = len(earlier_prices)
                item["june_average_price"] = round(sum(earlier_prices) / len(earlier_prices), 1)
            if later_prices:
                item["july_transaction_count"] = len(later_prices)
                item["july_average_price"] = round(sum(later_prices) / len(later_prices), 1)
            if earlier_prices and later_prices and item.get("june_average_price"):
                item["price_change_percent"] = round(
                    (item["july_average_price"] - item["june_average_price"])
                    / item["june_average_price"]
                    * 100,
                    2,
                )

        results.append(item)

    results.sort(key=lambda x: x["transaction_count"], reverse=True)

    return {
        "count": len(results),
        "filters": {
            "sido": sido, "gu": gu,
            "from_year_month": from_year_month, "to_year_month": to_year_month,
            "min_price": min_price, "max_price": max_price,
            "min_area": min_area, "max_area": max_area,
            "min_transactions": min_transactions,
        },
        "note": (
            "june_*/july_* 필드는 실제 6월·7월이 아니라 조회 범위의 "
            "'이전 달'/'이후 달' 실적입니다. 조회 범위가 정확히 2개월일 때만 채워집니다."
        ),
        "results": results,
    }
