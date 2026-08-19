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
    version="0.2.0",
    servers=[
        {
            "url": "https://gap-radar-api-vbg8.onrender.com",
            "description": "Render 운영 서버",
        }
    ],
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
    # 다른 모든 오류(HTTPException)와 형태를 통일: {"detail": {"error":..., "message":...}}
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "error": "internal_server_error",
                "message": "예상치 못한 서버 오류가 발생했습니다.",
            }
        },
    )


# ------------------------------------------------------------------
# 응답 모델
# ------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str


class ErrorDetail(BaseModel):
    error: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


# 4개 엔드포인트에서 공통으로 발생 가능한 오류를 OpenAPI 문서에 명시하기 위한 세트.
COMMON_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "잘못된 요청 (지원하지 않는 지역, 가격/면적 범위 역전, 잘못된 날짜 형식 등)"},
    401: {"model": ErrorResponse, "description": "인증 실패 (X-API-Key 헤더 누락 또는 값 불일치)"},
    502: {"model": ErrorResponse, "description": "국토부 실거래가 API 호출 또는 응답 처리 실패"},
    503: {"model": ErrorResponse, "description": "서버에 필요한 API 키(GAP_RADAR_API_KEY/MOLIT_API_KEY)가 설정되지 않음"},
}


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
    count: int = Field(description="필터 적용 후 전체 결과 건수 (limit 적용 전)")
    returned_count: int = Field(description="실제로 응답에 담겨 반환된 건수 (최대 limit)")
    truncated: bool = Field(description="count가 limit을 초과해 일부만 반환됐는지 여부")
    filters: dict
    results: list[Transaction] = Field(description="최신 거래일(deal_date) 내림차순으로 정렬됨")


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

    first_month: Optional[str] = Field(None, description="조회 범위의 첫 달, 형식 YYYY-MM (범위가 2~3개월일 때 값 존재)")
    first_month_transaction_count: Optional[int] = Field(None, description="first_month의 거래건수")
    first_month_average_price: Optional[float] = Field(None, description="first_month의 평균가(만원)")
    first_month_min_price: Optional[int] = Field(None, description="first_month의 최저가(만원)")
    first_month_max_price: Optional[int] = Field(None, description="first_month의 최고가(만원)")

    second_month: Optional[str] = Field(None, description="조회 범위의 마지막 달, 형식 YYYY-MM (범위가 2~3개월일 때 값 존재)")
    second_month_transaction_count: Optional[int] = Field(None, description="second_month의 거래건수")
    second_month_average_price: Optional[float] = Field(None, description="second_month의 평균가(만원)")
    second_month_min_price: Optional[int] = Field(None, description="second_month의 최저가(만원)")
    second_month_max_price: Optional[int] = Field(None, description="second_month의 최고가(만원)")

    price_change_percent: Optional[float] = Field(
        None, description="first_month 평균가 대비 second_month 평균가 변화율(%)"
    )


class ComplexAnalysisResponse(BaseModel):
    count: int
    filters: dict
    note: str
    results: list[ComplexAnalysisItem]


# ------------------------------------------------------------------
# /health — 인증 불필요
# ------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, summary="서버 상태 확인", operation_id="health_check")
async def health():
    return {"status": "ok"}


# ------------------------------------------------------------------
# /api/v1/transactions
# ------------------------------------------------------------------
@app.get(
    "/api/v1/transactions",
    response_model=TransactionsResponse,
    summary="아파트 매매 실거래 목록 조회",
    operation_id="get_transactions",
    dependencies=[Depends(require_api_key)],
    responses=COMMON_ERROR_RESPONSES,
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
    limit: int = Query(
        200, ge=1, le=500,
        description="최대 반환 건수. 최신 거래일(deal_date) 내림차순으로 상위 N건만 반환됩니다.",
    ),
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

    # 최신 거래일 순으로 정렬 후, limit을 초과하는 나머지는 잘라낸다.
    # (어떤 거래를 우선 반환할지의 규칙: "최신 거래일부터")
    all_rows.sort(key=lambda r: r["deal_date"], reverse=True)
    total_count = len(all_rows)
    sliced = all_rows[:limit]

    return {
        "count": total_count,
        "returned_count": len(sliced),
        "truncated": total_count > limit,
        "filters": {
            "sido": sido, "gu": gu, "year": year, "month": month,
            "min_price": min_price, "max_price": max_price,
            "min_area": min_area, "max_area": max_area,
            "complex_name": complex_name, "limit": limit,
        },
        "results": sliced,
    }


# ------------------------------------------------------------------
# /api/v1/complex-analysis — ChatGPT 연동용 핵심 API
# ------------------------------------------------------------------
@app.get(
    "/api/v1/complex-analysis",
    response_model=ComplexAnalysisResponse,
    summary="단지별 실거래 분석 (ChatGPT 연동용 핵심 API)",
    operation_id="get_complex_analysis",
    dependencies=[Depends(require_api_key)],
    responses=COMMON_ERROR_RESPONSES,
)
async def complex_analysis(
    sido: Optional[str] = Query("서울특별시", description="서울특별시만 지원"),
    gu: Optional[str] = Query(None, description="생략 시 서울 전체 25개 구를 조회합니다 — 느릴 수 있습니다."),
    from_year_month: str = Query(
        ..., description="조회 시작 연월, 형식 YYYY-MM",
        json_schema_extra={"examples": ["2026-06"]},
    ),
    to_year_month: str = Query(
        ..., description="조회 종료 연월, 형식 YYYY-MM (from_year_month로부터 최대 3개월)",
        json_schema_extra={"examples": ["2026-07"]},
    ),
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
    first_label = month_labels[0] if len(month_labels) >= 2 else None
    second_label = month_labels[-1] if len(month_labels) >= 2 else None

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

        if first_label and second_label and first_label != second_label:
            first_deals = [d for d in deals if d["deal_date"][:7] == first_label]
            second_deals = [d for d in deals if d["deal_date"][:7] == second_label]

            item["first_month"] = first_label
            item["second_month"] = second_label

            if first_deals:
                fp = [d["price"] for d in first_deals]
                item["first_month_transaction_count"] = len(fp)
                item["first_month_average_price"] = round(sum(fp) / len(fp), 1)
                item["first_month_min_price"] = min(fp)
                item["first_month_max_price"] = max(fp)
            if second_deals:
                sp = [d["price"] for d in second_deals]
                item["second_month_transaction_count"] = len(sp)
                item["second_month_average_price"] = round(sum(sp) / len(sp), 1)
                item["second_month_min_price"] = min(sp)
                item["second_month_max_price"] = max(sp)
            if first_deals and second_deals and item.get("first_month_average_price"):
                item["price_change_percent"] = round(
                    (item["second_month_average_price"] - item["first_month_average_price"])
                    / item["first_month_average_price"]
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
            "first_month/second_month 필드는 조회 범위의 첫 달/마지막 달을 실제 YYYY-MM 값으로 담습니다 "
            "(예: from_year_month=2026-06, to_year_month=2026-07 이면 first_month=\"2026-06\", "
            "second_month=\"2026-07\"). 조회 범위가 2~3개월일 때 채워지며, "
            "3개월 조회에서는 가운데 달도 전체 통계에 포함되지만 월간 비교는 첫 달과 마지막 달을 사용합니다."
        ),
        "results": results,
    }
