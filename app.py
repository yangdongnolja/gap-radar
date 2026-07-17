import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote
from difflib import SequenceMatcher

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 0. 환경설정 (.env 에서 API 키 로딩)
# ==========================================
load_dotenv()
MOLIT_API_KEY = os.getenv("MOLIT_API_KEY", "").strip()
KAKAO_API_KEY = os.getenv("KAKAO_API_KEY", "").strip()         # JavaScript 키 (지도 표시용)
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "").strip()  # REST API 키 (주소→좌표 변환용)

st.set_page_config(page_title="동욱's 갭메우기 레이더", layout="wide", initial_sidebar_state="auto")

# 상단 여백/제목 크기 축소 + 모바일에서 잘리지 않도록 기본 패딩 최소화
st.markdown(
    """
    <style>
    /* Streamlit 상단 고정 바(약 60px)에 안 가리도록 최소 여백만 확보 */
    .block-container { padding-top: 3.6rem; padding-bottom: 1rem; }
    @media (max-width: 640px) {
        .block-container { padding-left: 0.8rem; padding-right: 0.8rem; }
    }
    </style>
    <div style="display:flex;align-items:baseline;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.1rem;">
        <span style="font-size:1.4rem;font-weight:800;">🎯 동욱's 갭메우기 레이더</span>
        <span style="font-size:0.8rem;color:#888;">v3.4</span>
    </div>
    <div style="font-size:0.82rem;color:#888;margin-bottom:0.6rem;">
        공공데이터 API 실거래가 기반 | 멀티 지역·기간 검색 + 카카오맵 + 엑셀 다운로드
    </div>
    """,
    unsafe_allow_html=True,
)

if not MOLIT_API_KEY or not KAKAO_API_KEY or not KAKAO_REST_API_KEY:
    st.error("🚨 `.env` 파일에 MOLIT_API_KEY / KAKAO_API_KEY / KAKAO_REST_API_KEY 값이 "
             "설정되어 있지 않습니다. 프로젝트 폴더의 .env 파일을 확인해주세요.")
    st.stop()

# ==========================================
# 서울 25개 자치구 코드
# ==========================================
LAWD_CD_DICT = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200",
    "광진구": "11215", "동대문구": "11230", "중랑구": "11260", "성북구": "11290",
    "강북구": "11305", "도봉구": "11320", "노원구": "11350", "은평구": "11380",
    "서대문구": "11410", "마포구": "11440", "양천구": "11470", "강서구": "11500",
    "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
    "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710",
    "강동구": "11740",
}

# ==========================================
# session_state 초기화
# ==========================================
DEFAULTS = {
    "raw_df": None,          # API로 받아온 전체 원본 데이터
    "pending_search": None,  # 확인 대기 중인 검색 조건
    "selected_pins": [],     # 지도에서 선택한 단지 목록
    "current_page": 1,       # 페이지네이션 안전 관리를 위한 상태
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ==========================================
# 유틸 및 API 호출 함수 (내장 캐싱 적용)
# ==========================================
def month_range(start_ymd: str, end_ymd: str):
    """YYYYMM ~ YYYYMM 사이 월 목록 리턴 (최대 3개월)"""
    s = datetime.strptime(start_ymd, "%Y%m")
    e = datetime.strptime(end_ymd, "%Y%m")
    if s > e:
        raise ValueError("시작월이 종료월보다 늦습니다.")
    months = []
    cur = s
    while cur <= e:
        months.append(cur.strftime("%Y%m"))
        cur = (cur.replace(day=1) + pd.DateOffset(months=1)).to_pydatetime()
    if len(months) > 3:
        raise ValueError("조회 기간은 최대 3개월까지만 가능합니다.")
    return months


def parse_item(item, gu_name: str, ymd: str):
    def gettext(tag):
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    if gettext("cdealType"):  # 취소 거래 제외
        return None

    apt_name = gettext("aptNm")
    dong = gettext("umdNm")
    jibun = gettext("jibun")
    floor_str = gettext("floor")
    day = gettext("dealDay")

    price_str = gettext("dealAmount").replace(",", "")
    try:
        price = int(price_str)
    except ValueError:
        price = 0

    try:
        area = float(gettext("excluUseAr"))
    except ValueError:
        area = 0.0

    try:
        floor_num = int(floor_str)
    except ValueError:
        floor_num = 0

    addr_parts = ["서울특별시", gu_name]
    if dong:
        addr_parts.append(dong)
    if jibun:
        addr_parts.append(jibun)
    address = " ".join(addr_parts)

    return {
        "계약일": f"{ymd[:4]}-{ymd[4:]}-{day.zfill(2) if day else '01'}",
        "자치구": gu_name,
        "법정동": dong,
        "아파트명": apt_name,
        "전용면적(㎡)": area,
        "층": floor_str,
        "층_num": floor_num,
        "거래금액(만원)": price,
        "주소": address,
    }


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_one(gu_code: str, gu_name: str, ymd: str):
    """실거래가 API 호출 결과 캐싱 (30분 유지)"""
    url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
    rows = []
    page = 1
    while True:
        params = {
            "serviceKey": MOLIT_API_KEY,
            "LAWD_CD": gu_code,
            "DEAL_YMD": ymd,
            "numOfRows": "1000",
            "pageNo": str(page),
        }
        resp = requests.get(url, params=params, verify=False, timeout=15)
        
        # HTTP 상태 코드 및 XML 포맷 검증 (서버 비표준 응답 에러 방어)
        if resp.status_code != 200 or not resp.content.strip().startswith(b"<"):
            raise RuntimeError(f"[{gu_name} {ymd}] 서버 응답 오류 (HTTP {resp.status_code}): {resp.text[:100]}")
            
        root = ET.fromstring(resp.content)

        result_code = root.findtext(".//resultCode")
        if result_code is not None and result_code not in ("00", "000"):
            result_msg = root.findtext(".//resultMsg")
            raise RuntimeError(f"[{gu_name} {ymd}] API 오류 {result_code}: {result_msg}")

        items = root.findall(".//item")
        if not items:
            break

        for item in items:
            row = parse_item(item, gu_name, ymd)
            if row is not None:
                rows.append(row)

        total_count = int(root.findtext(".//totalCount") or 0)
        if page * 1000 >= total_count:
            break
        page += 1

    return rows


def _norm_apt_name(s: str) -> str:
    """단지명 매칭용 정규화: 괄호 내용 제거 후 한글/영문/숫자만 남김"""
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^0-9a-zA-Z가-힣]", "", s)
    return s.lower()


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_apt_list(gu_code: str):
    """공동주택 단지목록 조회 캐싱 (24시간 유지)"""
    url = "https://apis.data.go.kr/1613000/AptListService3/getSigunguAptList3"
    out = []
    page = 1
    while True:
        resp = requests.get(
            url,
            params={"serviceKey": MOLIT_API_KEY, "sigunguCode": gu_code,
                    "numOfRows": "1000", "pageNo": str(page)},
            verify=False, timeout=15,
        )
        if resp.status_code == 403:
            raise RuntimeError("공동주택 API 권한이 아직 동기화되지 않았습니다.")
        if resp.status_code != 200:
            raise RuntimeError(f"공동주택 단지목록 API 오류 (HTTP {resp.status_code})")
        body = resp.json().get("response", {}).get("body", {})
        items = body.get("items") or []
        if isinstance(items, dict):
            items = items.get("item") or []
            if isinstance(items, dict):
                items = [items]
        if not items:
            break
        for item in items:
            code = (item.get("kaptCode") or "").strip()
            name = (item.get("kaptName") or "").strip()
            dong = (item.get("as3") or "").strip()
            if code and name:
                out.append((code, name, _norm_apt_name(name), dong))
        total_count = int(body.get("totalCount") or 0)
        if page * 1000 >= total_count:
            break
        page += 1
    return out


CINFO_EMPTY = {"세대수": None, "준공연도": None, "건폐율(%)": None, "용적률(%)": None}


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_complex_info(kapt_code: str):
    """단지별 상세정보 및 건축물대장 조회 캐싱 (24시간 유지)"""
    info = dict(CINFO_EMPTY)
    try:
        resp = requests.get(
            "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusBassInfoV4",
            params={"serviceKey": MOLIT_API_KEY, "kaptCode": kapt_code},
            verify=False, timeout=15,
        )
        item = resp.json().get("response", {}).get("body", {}).get("item", {})
    except Exception:
        return info

    cnt = item.get("kaptdaCnt")
    if cnt not in (None, "", " "):
        try:
            info["세대수"] = int(float(cnt))
        except ValueError:
            pass

    used = str(item.get("kaptUsedate") or "").strip()
    if len(used) >= 4 and used[:4].isdigit():
        info["준공연도"] = int(used[:4])

    bjd = str(item.get("bjdCode") or "").strip()
    addr = str(item.get("kaptAddr") or "")
    m = re.search(r"\s(\d+)(?:-(\d+))?(?:\s|$)", addr)
    if len(bjd) == 10 and m:
        bun = m.group(1).zfill(4)
        ji = (m.group(2) or "0").zfill(4)
        try:
            r2 = requests.get(
                "https://apis.data.go.kr/1613000/BldRgstHubService/getBrRecapTitleInfo",
                params={"serviceKey": MOLIT_API_KEY, "sigunguCd": bjd[:5], "bjdongCd": bjd[5:],
                        "bun": bun, "ji": ji, "numOfRows": "10", "_type": "json"},
                verify=False, timeout=15,
            )
            if r2.status_code == 200:
                body = r2.json().get("response", {}).get("body", {})
                items = body.get("items") or {}
                it = items.get("item") if isinstance(items, dict) else items
                if isinstance(it, list):
                    it = it[0] if it else None
                if it:
                    bc, vl = it.get("bcRat"), it.get("vlRat")
                    if bc not in (None, "", 0, "0"):
                        info["건폐율(%)"] = float(bc)
                    if vl not in (None, "", 0, "0"):
                        info["용적률(%)"] = float(vl)
        except Exception:
            pass
    return info


def _best_match(n: str, candidates):
    """정규화된 이름 n과 candidates([(code, name, knorm), ...]) 중 최고 유사도 매칭"""
    best_code, best_score = None, 0.0
    for code, _, knorm in candidates:
        if not knorm:
            continue
        score = SequenceMatcher(None, n, knorm).ratio()
        if n in knorm or knorm in n:
            score += 0.25
        if score > best_score:
            best_score, best_code = score, code
    return best_code, best_score


def _match_kapt(apt_name: str, dong: str, entries):
    """실거래 아파트명+법정동과 공동주택 단지목록 매칭.

    같은 이름(예: '중앙하이츠', '래미안')이 다른 동에도 흔히 존재하므로,
    법정동이 같은 후보를 우선 사용하고, 동이 일치하는 후보가 없을 때만
    자치구 전체로 넓히되 오매칭 방지를 위해 더 높은 기준을 요구한다.
    """
    n = _norm_apt_name(apt_name)
    if not n:
        return None

    same_dong = [(c, nm, kn) for c, nm, kn, d in entries if d == dong]
    if same_dong:
        for code, _, knorm in same_dong:
            if n == knorm:
                return code
        code, score = _best_match(n, same_dong)
        if score >= 0.55:
            return code
        return None  # 동은 맞는데 이름이 안 맞으면 엉뚱한 동 단지로 확대하지 않음

    # 법정동 정보가 없거나(동명 표기 차이 등) 동일 동 후보가 없는 경우에만
    # 자치구 전체로 확대 탐색 — 오매칭 위험이 크므로 기준을 훨씬 엄격하게.
    all_entries = [(c, nm, kn) for c, nm, kn, d in entries]
    for code, _, knorm in all_entries:
        if n == knorm:
            return code
    code, score = _best_match(n, all_entries)
    if score >= 0.85:
        return code
    return None


def attach_complex_info(df: pd.DataFrame) -> pd.DataFrame:
    """세대수/준공연도/건폐율/용적률 컬럼 연동 및 병렬 스레딩 안정화.

    동일 아파트명이 여러 법정동에 흔히 존재하므로 (자치구, 법정동, 아파트명)
    3중키로 매칭·캐싱한다 — 이름만으로 키를 잡으면 다른 동의 동명 단지 정보가
    섞여 세대수 등이 틀리게 표시되는 문제가 있었다.
    """
    df = df.copy()
    triples = df[["자치구", "법정동", "아파트명"]].drop_duplicates()
    results_map = {}

    for gu in triples["자치구"].unique():
        gu_code = LAWD_CD_DICT[gu]
        entries = fetch_apt_list(gu_code)

        gu_triples = triples.loc[triples["자치구"] == gu, ["법정동", "아파트명"]].drop_duplicates()
        to_fetch = []

        for dong, name in gu_triples.itertuples(index=False):
            kapt_code = _match_kapt(name, dong, entries)
            if kapt_code is None:
                results_map[(gu, dong, name)] = dict(CINFO_EMPTY)
            else:
                to_fetch.append(((gu, dong, name), kapt_code))

        if to_fetch:
            with ThreadPoolExecutor(max_workers=8) as ex:
                futures = {ex.submit(fetch_complex_info, code): key for key, code in to_fetch}
                for future in futures:
                    key = futures[future]
                    try:
                        results_map[key] = future.result()
                    except Exception:
                        results_map[key] = dict(CINFO_EMPTY)

    for col in CINFO_EMPTY:
        df[col] = [results_map.get((gu, dong, name), CINFO_EMPTY).get(col)
                   for gu, dong, name in zip(df["자치구"], df["법정동"], df["아파트명"])]
    return df


@st.cache_data(ttl=2592000, show_spinner=False)
def geocode(address: str):
    """주소 좌표 변환 내장 캐싱 (한 달 유지)"""
    try:
        resp = requests.get(
            "https://dapi.kakao.com/v2/local/search/address.json",
            headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"},
            params={"query": address},
            timeout=5,
        )
        docs = resp.json().get("documents", [])
        if docs:
            return float(docs[0]["y"]), float(docs[0]["x"])
    except Exception:
        pass
    return None, None


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="data")
    return buf.getvalue()


def _fmt_price(manwon: float) -> str:
    if manwon >= 10000:
        return f"{manwon / 10000:.1f}억"
    return f"{int(manwon):,}만원"


def build_popup_html(gu: str, dong: str, apt: str, addr: str, deals: pd.DataFrame) -> str:
    """지도 핀 클릭 시 뜨는 상세카드 HTML (전역 캐시 의존 제거로 결합도 완화)"""
    meta = []
    if not deals.empty:
        first = deals.iloc[0]
        if "세대수" in deals.columns and pd.notna(first["세대수"]):
            meta.append(f"{int(first['세대수']):,}세대")
        if "준공연도" in deals.columns and pd.notna(first["준공연도"]):
            meta.append(f"{int(first['준공연도'])}년 준공")
        if "용적률(%)" in deals.columns and pd.notna(first["용적률(%)"]):
            meta.append(f"용적률 {first['용적률(%)']:.0f}%")
        if "건폐율(%)" in deals.columns and pd.notna(first["건폐율(%)"]):
            meta.append(f"건폐율 {first['건폐율(%)']:.0f}%")

    grp = deals.groupby(deals["전용면적(㎡)"].round().astype(int))["거래금액(만원)"]
    rows = [f"{area}㎡ 평균 <b>{_fmt_price(s.mean())}</b> ({len(s)}건)"
            for area, s in sorted(grp, key=lambda t: t[0])][:5]

    naver_url = "https://search.naver.com/search.naver?query=" + quote(f"{dong} {apt}")
    divider = "<hr style='margin:4px 0;border:none;border-top:1px solid #ddd;'/>"
    return (
        "<div style='padding:8px 12px;font-size:12px;line-height:1.7;min-width:200px;max-width:250px;'>"
        f"<b style='font-size:14px'>{apt}</b> <span style='color:#888'>({dong})</span><br/>"
        + (" · ".join(meta) + divider if meta else divider)
        + "<br/>".join(rows)
        + divider
        + f"<span style='color:#666'>{addr}</span><br/>"
        + f"<a href='{naver_url}' target='_blank' style='color:#03c75a;font-weight:bold;text-decoration:none;'>네이버에서 단지정보 보기 ↗</a>"
        + "</div>"
    )


def build_marker_label(apt: str, deals: pd.DataFrame) -> dict:
    """지도에 항상 보이는 세로형 말풍선용 데이터: 평당가 / 대표 매매가 / 대표 면적.

    부동산 지도 서비스(네이버 등)에 익숙한 형태로 세로 2단 뱃지를 만든다 —
    가로로 긴 한 줄 텍스트 대신 평당가(작게) + 매매가(굵게) + 면적(작게)로 쌓는다.
    """
    if deals.empty:
        return {"pyeong": "", "price": "-", "area": ""}
    grp = deals.groupby(deals["전용면적(㎡)"].round().astype(int))["거래금액(만원)"]
    # 거래건수가 가장 많은 평형을 대표값으로 사용
    rep_area, rep_series = max(grp, key=lambda t: len(t[1]))
    rep_price = rep_series.mean()
    pyeong_price = rep_price / rep_area * 3.3058  # 3.3㎡(1평)당 가격
    return {
        "pyeong": f"평 {pyeong_price / 10000:.1f}억" if pyeong_price >= 10000 else f"평 {pyeong_price:,.0f}만",
        "price": _fmt_price(rep_price),
        "area": f"{rep_area}㎡",
    }


def render_kakao_map(marker_data: list):
    markers_str = json.dumps(marker_data, ensure_ascii=False)

    html = f"""
    <div id="map" style="width:100%;height:600px;"></div>
    <script>
    // Streamlit의 iframe(srcdoc) 안에서는 카카오 SDK가 현재 페이지를 https로 인식하지 못해
    // 지도 엔진 스크립트를 http://로 요청하다 브라우저에 차단되는 문제(Mixed Content)가 있다.
    // 동적으로 추가되는 <script src="http://..."> 를 https로 강제 승격시켜 우회한다.
    (function() {{
        var d = Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype, 'src');
        Object.defineProperty(HTMLScriptElement.prototype, 'src', {{
            set: function(v) {{
                if (typeof v === 'string' && v.indexOf('http://') === 0) {{
                    v = 'https://' + v.slice(7);
                }}
                d.set.call(this, v);
            }},
            get: d.get
        }});
    }})();
    </script>
    <script src="https://dapi.kakao.com/v2/maps/sdk.js?appkey={KAKAO_API_KEY}&autoload=false"></script>
    <script>
    kakao.maps.load(function() {{
        var container = document.getElementById('map');
        var options = {{ center: new kakao.maps.LatLng(37.5665, 126.9780), level: 7 }};
        var map = new kakao.maps.Map(container, options);
        var markers = {markers_str};
        var bounds = new kakao.maps.LatLngBounds();
        var openDetail = null;

        markers.forEach(function(m, idx) {{
            var pos = new kakao.maps.LatLng(m.lat, m.lng);
            bounds.extend(pos);

            // 핀 대신 단지명·대표면적·가격이 바로 보이는 라벨 뱃지
            // 세로형 2단 말풍선: 위 - 평당가(작은 회색 알약), 아래 - 매매가(굵게)+면적, 꼬리표 포함
            var accent = m.selected ? '#f5a623' : '#2f6fed';
            var badge = document.createElement('div');
            badge.style.cssText =
                'display:flex;flex-direction:column;align-items:center;cursor:pointer;' +
                'filter:drop-shadow(0 1px 2px rgba(0,0,0,.35));';

            var pyeongEl = document.createElement('div');
            pyeongEl.textContent = m.label.pyeong;
            pyeongEl.style.cssText =
                'font-size:9px;color:#666;background:#fff;border:1px solid #ddd;' +
                'border-radius:8px;padding:0px 6px;margin-bottom:1px;white-space:nowrap;' +
                (m.label.pyeong ? '' : 'display:none;');

            var mainEl = document.createElement('div');
            mainEl.style.cssText =
                'display:flex;flex-direction:column;align-items:center;line-height:1.15;' +
                'padding:3px 8px;border-radius:7px;white-space:nowrap;' +
                'background:' + accent + ';color:#fff;';
            var priceEl = document.createElement('div');
            priceEl.textContent = m.label.price;
            priceEl.style.cssText = 'font-size:12.5px;font-weight:800;';
            var areaEl = document.createElement('div');
            areaEl.textContent = m.label.area;
            areaEl.style.cssText = 'font-size:9px;opacity:.9;';
            mainEl.appendChild(priceEl);
            mainEl.appendChild(areaEl);

            var tailEl = document.createElement('div');
            tailEl.style.cssText =
                'width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;' +
                'border-top:6px solid ' + accent + ';margin-top:-1px;';

            badge.appendChild(pyeongEl);
            badge.appendChild(mainEl);
            badge.appendChild(tailEl);
            badge.onmouseenter = function() {{ badge.style.zIndex = 20; }};

            var overlay = new kakao.maps.CustomOverlay({{
                position: pos, content: badge, map: map,
                yAnchor: 1.0, zIndex: m.selected ? 10 : 1
            }});

            // 상세 팝업도 InfoWindow 대신 CustomOverlay로 만든다 — InfoWindow는
            // zIndex를 지정할 수 없어 라벨 뱃지(zIndex 지정됨)보다 뒤로 깔리는
            // 문제가 있었다. CustomOverlay는 zIndex를 크게 줘서 항상 맨 위에 뜨게 한다.
            var detailWrap = document.createElement('div');
            detailWrap.style.cssText =
                'position:relative;background:#fff;border-radius:8px;' +
                'box-shadow:0 2px 10px rgba(0,0,0,.35);';
            var closeBtn = document.createElement('div');
            closeBtn.textContent = '×';
            closeBtn.style.cssText =
                'position:absolute;top:2px;right:7px;cursor:pointer;' +
                'font-size:16px;line-height:1;color:#999;font-weight:bold;';
            var contentDiv = document.createElement('div');
            contentDiv.innerHTML = m.html;
            detailWrap.appendChild(closeBtn);
            detailWrap.appendChild(contentDiv);

            var detailOverlay = new kakao.maps.CustomOverlay({{
                position: pos, content: detailWrap, map: null,
                yAnchor: 1.15, zIndex: 999
            }});
            closeBtn.addEventListener('click', function(e) {{
                e.stopPropagation();
                detailOverlay.setMap(null);
                if (openDetail === detailOverlay) openDetail = null;
            }});

            badge.addEventListener('click', function() {{
                if (openDetail === detailOverlay) {{
                    detailOverlay.setMap(null);
                    openDetail = null;
                }} else {{
                    if (openDetail) openDetail.setMap(null);
                    detailOverlay.setMap(map);
                    openDetail = detailOverlay;
                }}
            }});
        }});
        if (markers.length > 0) {{
            var fitMap = function() {{ map.relayout(); map.setBounds(bounds); }};
            var fitted = false;
            var tryFit = function() {{
                var rect = container.getBoundingClientRect();
                var visible = rect.width > 50 && rect.height > 50 && container.offsetParent !== null;
                if (visible && !fitted) {{
                    fitted = true;
                    fitMap();
                    setTimeout(fitMap, 300);
                    clearInterval(timer);
                }}
            }};
            var timer = setInterval(tryFit, 300);
            tryFit();
        }}
    }});
    </script>
    """
    components.html(html, height=620)


# ==========================================
# 사이드바 - 1. 검색 지역 & 기간
# ==========================================
st.sidebar.header("⚙️ 1. 검색 지역 & 기간")
selected_gu_names = st.sidebar.multiselect(
    "📌 자치구 선택 (복수 선택 가능)", sorted(LAWD_CD_DICT.keys())
)

_prev_month = (pd.Timestamp.today().replace(day=1) - pd.DateOffset(months=1)).strftime("%Y%m")
col1, col2 = st.sidebar.columns(2)
start_month = col1.text_input("시작월 (YYYYMM)", value=_prev_month)
end_month = col2.text_input("종료월 (YYYYMM)", value=_prev_month)
st.sidebar.caption("※ 조회 기간은 최대 3개월까지 가능합니다.")

st.sidebar.markdown("---")

# ==========================================
# 사이드바 - 2. 필터
# ==========================================
st.sidebar.header("🔍 2. 필터")

FILTER_DEFAULTS = {
    # 기본 프리셋: 가격 최대 8.5억 / 전용면적 최소 50㎡ / 세대수 최소 300세대
    "f_use_price": True, "f_price_min": "", "f_price_max": "85",
    "f_use_area": True, "f_area": (50.0, 200.0),
    "f_use_floor": False, "f_floor": (-3, 70),
    "f_use_hh": True, "f_hh": (300, 2000), "f_hh_unknown": True,
    "f_show_cinfo": False,
    "f_kw": "",
}
for _k, _v in FILTER_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def reset_filters():
    for k, v in FILTER_DEFAULTS.items():
        st.session_state[k] = v


use_price_filter = st.sidebar.checkbox("💰 가격 필터 사용", key="f_use_price")
pcol1, pcol2 = st.sidebar.columns(2)
price_min_txt = pcol1.text_input(
    "최소 (천만원)", key="f_price_min", placeholder="공란=0원",
    disabled=not use_price_filter,
)
price_max_txt = pcol2.text_input(
    "최대 (천만원)", key="f_price_max", placeholder="공란=무제한",
    disabled=not use_price_filter,
)
st.sidebar.caption("※ 천만원 단위 입력. 예: 최소 50, 최대 85 → 5억~8.5억")


def _parse_chonman(txt: str, default: float) -> float:
    txt = str(txt).strip()
    if not txt:
        return default
    try:
        return float(txt) * 1000
    except ValueError:
        return default

use_area_filter = st.sidebar.checkbox("📐 면적 필터 사용", key="f_use_area")
area_range = st.sidebar.slider(
    "전용면적 범위 (㎡)", min_value=10.0, max_value=200.0,
    step=1.0, key="f_area", disabled=not use_area_filter,
)

use_floor_filter = st.sidebar.checkbox("🏢 층 필터 사용", key="f_use_floor")
floor_range = st.sidebar.slider(
    "층 범위", min_value=-3, max_value=70, key="f_floor", disabled=not use_floor_filter,
)

use_hh_filter = st.sidebar.checkbox("🏘️ 세대수 필터 사용", key="f_use_hh")
hh_range = st.sidebar.slider(
    "세대수 범위", min_value=0, max_value=2000, step=100,
    key="f_hh", disabled=not use_hh_filter,
)
st.sidebar.caption("※ 오른쪽 끝(2000)에 두면 '2000세대 이상' 대단지도 모두 포함됩니다.")
include_unknown_hh = st.sidebar.checkbox(
    "세대수 미확인 단지도 포함", key="f_hh_unknown", disabled=not use_hh_filter,
)

show_cinfo = st.sidebar.checkbox(
    "🏗️ 단지 정보 컬럼 표시 (세대수·준공연도·건폐율·용적률)", key="f_show_cinfo",
)

search_keyword = st.sidebar.text_input(
    "📝 단지/동 텍스트 검색 (공백이면 전체)", placeholder="예: 두산, 공릉동", key="f_kw"
)

st.sidebar.button("♻️ 필터 초기화", on_click=reset_filters, use_container_width=True)

st.sidebar.markdown("---")

# ==========================================
# 검색 실행 (2단계 확인)
# ==========================================
st.sidebar.header("🚀 3. 조회 실행")

if st.sidebar.button("검색 조건 확인", type="primary", use_container_width=True):
    if not selected_gu_names:
        st.sidebar.error("자치구를 1개 이상 선택해주세요.")
    else:
        try:
            months = month_range(start_month.strip(), end_month.strip())
            st.session_state.pending_search = {
                "gus": selected_gu_names,
                "months": months,
            }
        except ValueError as e:
            st.sidebar.error(f"기간 입력 오류: {e}")
        except Exception:
            st.sidebar.error("년월 형식이 올바르지 않습니다. 예: 202606")

if st.session_state.pending_search:
    ps = st.session_state.pending_search
    n_calls = len(ps["gus"]) * len(ps["months"])
    est_sec = n_calls * 1.5
    st.sidebar.warning(
        f"⚠️ {len(ps['gus'])}구 × {len(ps['months'])}개월 = **{n_calls}회 호출**, "
        f"약 **{est_sec:.0f}초** 소요 예상"
    )
    c1, c2 = st.sidebar.columns(2)
    confirm = c1.button("✅ 조회 시작", use_container_width=True)
    cancel = c2.button("❌ 취소", use_container_width=True)

    if cancel:
        st.session_state.pending_search = None
        st.rerun()

    if confirm:
        gu_codes = [(name, LAWD_CD_DICT[name]) for name in ps["gus"]]
        months = ps["months"]

        progress = st.sidebar.progress(0.0, text="조회 준비 중...")
        all_rows = []
        errors = []
        total = len(gu_codes) * len(months)
        done = 0

        for gu_name, gu_code in gu_codes:
            for ymd in months:
                try:
                    rows = fetch_one(gu_code, gu_name, ymd)  # 캐시 연동 호출
                    all_rows.extend(rows)
                except Exception as e:
                    errors.append(str(e))
                done += 1
                progress.progress(done / total, text=f"{gu_name} {ymd} 조회 중... ({done}/{total})")

        progress.empty()
        st.session_state.raw_df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame()
        st.session_state.pending_search = None
        st.session_state.selected_pins = []
        st.session_state.current_page = 1  # 검색 후 1페이지로 초기화

        if errors:
            st.sidebar.error("일부 호출 실패:\n" + "\n".join(errors))
        st.rerun()


# ==========================================
# 필터링
# ==========================================
filtered_df = None
if st.session_state.raw_df is not None:
    df = st.session_state.raw_df.copy()
    if not df.empty:
        if use_price_filter:
            price_min = _parse_chonman(price_min_txt, 0)
            price_max = _parse_chonman(price_max_txt, float("inf"))
            df = df[(df["거래금액(만원)"] >= price_min) & (df["거래금액(만원)"] <= price_max)]
        if use_area_filter:
            df = df[(df["전용면적(㎡)"] >= area_range[0]) & (df["전용면적(㎡)"] <= area_range[1])]
        if use_floor_filter:
            df = df[(df["층_num"] >= floor_range[0]) & (df["층_num"] <= floor_range[1])]
        if search_keyword.strip():
            kw = search_keyword.strip()
            df = df[
                df["아파트명"].str.contains(kw, case=False, na=False)
                | df["법정동"].str.contains(kw, case=False, na=False)
            ]
        if (use_hh_filter or show_cinfo) and not df.empty:
            try:
                with st.spinner("단지별 정보(세대수·준공연도·건폐율·용적률)를 수집 중입니다..."):
                    df = attach_complex_info(df)
                if use_hh_filter:
                    hh_upper = float("inf") if hh_range[1] >= 2000 else hh_range[1]
                    in_range = (df["세대수"] >= hh_range[0]) & (df["세대수"] <= hh_upper)
                    if include_unknown_hh:
                        df = df[df["세대수"].isna() | in_range]
                    else:
                        df = df[df["세대수"].notna() & in_range]
            except RuntimeError as e:
                st.warning(f"⚠️ 단지 정보를 가져오지 못했습니다: {e}")
    filtered_df = df

DISPLAY_COLS = ["계약일", "자치구", "법정동", "아파트명", "전용면적(㎡)", "층", "거래금액(만원)"]
if filtered_df is not None:
    DISPLAY_COLS += [c for c in ["세대수", "준공연도", "건폐율(%)", "용적률(%)"] if c in filtered_df.columns]

def naver_search_url(name: str) -> str:
    """단지명 클릭 시 이동할 네이버 검색 링크. LinkColumn의 display_text 정규식이
    'query=' 뒤 문자열을 그대로 잘라 보여주므로, URL 인코딩 없이 원문 그대로 붙인다."""
    return f"https://search.naver.com/search.naver?query={name}"


NAVER_LINK_CONFIG = {
    "아파트명": st.column_config.LinkColumn("아파트명", display_text=r"query=(.+)$"),
}

NUM_COL_CONFIG = {
    "거래금액(만원)": st.column_config.NumberColumn(format="localized"),
    "전용면적(㎡)": st.column_config.NumberColumn(format="localized"),
    "세대수": st.column_config.NumberColumn(format="localized"),
    "준공연도": st.column_config.NumberColumn(format="%d"),
    "건폐율(%)": st.column_config.NumberColumn(format="localized"),
    "용적률(%)": st.column_config.NumberColumn(format="localized"),
    "거래건수": st.column_config.NumberColumn(format="localized"),
    "평균가": st.column_config.NumberColumn(format="localized"),
    "최저가": st.column_config.NumberColumn(format="localized"),
    "최고가": st.column_config.NumberColumn(format="localized"),
}

# ==========================================
# 탭 구성
# ==========================================
tab1, tab2 = st.tabs(["📋 리스트", "🗺️ 지도"])

with tab1:
    if filtered_df is None:
        st.info("왼쪽 사이드바에서 조건을 설정하고 [검색 조건 확인] 버튼을 눌러주세요.")
    elif filtered_df.empty:
        st.warning("조건에 맞는 실거래 데이터가 없습니다.")
    else:
        st.success(f"🎉 총 **{len(filtered_df):,}건**의 실거래를 찾았습니다.")

        sorted_df = filtered_df.sort_values(by="계약일", ascending=False).reset_index(drop=True)

        page_size = 50
        total_pages = max(1, (len(sorted_df) - 1) // page_size + 1)
        
        # [수정됨] 페이지네이션 초과 버그 방어 로직
        if st.session_state.current_page > total_pages:
            st.session_state.current_page = 1
            
        page_num = st.number_input("페이지", min_value=1, max_value=total_pages, value=st.session_state.current_page, step=1, key="page_input")
        st.session_state.current_page = page_num
        
        start_idx = (page_num - 1) * page_size
        page_df = sorted_df.iloc[start_idx:start_idx + page_size]

        page_df_view = page_df[DISPLAY_COLS].copy()
        page_df_view["아파트명"] = page_df_view["아파트명"].apply(naver_search_url)
        st.dataframe(page_df_view, use_container_width=True, hide_index=True,
                     column_config={**NUM_COL_CONFIG, **NAVER_LINK_CONFIG})
        st.caption(f"페이지 {page_num} / {total_pages} (전체 {len(sorted_df):,}건, 50건씩 표시) · 아파트명 클릭 시 네이버 검색으로 이동")

        st.markdown("### 🏢 단지별 요약")
        summary_df = (
            filtered_df.groupby(["법정동", "아파트명"])
            .agg(
                거래건수=("거래금액(만원)", "count"),
                평균가=("거래금액(만원)", "mean"),
                최저가=("거래금액(만원)", "min"),
                최고가=("거래금액(만원)", "max"),
            )
            .reset_index()
        )
        summary_df["평균가"] = summary_df["평균가"].round(0).astype(int)
        summary_df = summary_df.sort_values(by="거래건수", ascending=False)
        summary_df_view = summary_df.copy()
        summary_df_view["아파트명"] = summary_df_view["아파트명"].apply(naver_search_url)
        st.dataframe(summary_df_view, use_container_width=True, hide_index=True,
                     column_config={**NUM_COL_CONFIG, **NAVER_LINK_CONFIG})

        st.markdown("### ⬇️ 엑셀 다운로드")
        d1, d2 = st.columns(2)
        with d1:
            st.download_button(
                "① 현재 필터 결과 전체",
                data=to_excel_bytes(filtered_df[DISPLAY_COLS]),
                file_name="실거래_필터결과.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with d2:
            st.download_button(
                "③ 단지별 요약본",
                data=to_excel_bytes(summary_df),
                file_name="단지별_요약.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

with tab2:
    if filtered_df is None or filtered_df.empty:
        st.info("표시할 데이터가 없습니다. 먼저 리스트 탭에서 검색을 진행해주세요.")
    else:
        st.caption(
            "📍 지도의 라벨에 단지명·대표 면적·가격이 바로 보이고, 클릭하면 상세정보 팝업이 뜹니다. "
            "아래 **핀 선택 목록**에서 단지를 골라 선택(강조)하고 엑셀로 내려받을 수 있습니다."
        )

        map_summary = (
            filtered_df.groupby(["자치구", "법정동", "아파트명", "주소"])
            .agg(거래건수=("거래금액(만원)", "count"), 평균가=("거래금액(만원)", "mean"))
            .reset_index()
        )

        MAX_PINS = 300
        if len(map_summary) > MAX_PINS:
            st.warning(f"단지 수가 {MAX_PINS}개를 초과하여 거래건수 상위 {MAX_PINS}개 단지만 지도에 표시합니다.")
            map_summary = map_summary.sort_values("거래건수", ascending=False).head(MAX_PINS)

        with st.spinner("좌표 변환 중... (카카오 지도 API)"):
            coords = map_summary["주소"].apply(geocode)
            map_summary["lat"] = coords.apply(lambda x: x[0])
            map_summary["lng"] = coords.apply(lambda x: x[1])

        geo_ok = map_summary.dropna(subset=["lat", "lng"])
        geo_fail_count = len(map_summary) - len(geo_ok)
        if geo_fail_count > 0:
            st.caption(f"⚠️ {geo_fail_count}개 단지는 주소 좌표 변환에 실패하여 지도에 표시되지 않았습니다.")

        pin_options = [f"{r['아파트명']} ({r['법정동']})" for _, r in geo_ok.iterrows()]
        selected_labels = st.multiselect("📍 핀 선택", pin_options)
        st.session_state.selected_pins = selected_labels

        # [수정됨] 동일 아파트명 중복 필터링 방어 및 맵 마커 표시
        if not geo_ok.empty:
            marker_data = []
            for _, r in geo_ok.iterrows():
                deals = filtered_df[
                    (filtered_df["자치구"] == r["자치구"])
                    & (filtered_df["법정동"] == r["법정동"])
                    & (filtered_df["아파트명"] == r["아파트명"])
                ]
                
                apt_label = f"{r['아파트명']} ({r['법정동']})"
                marker_data.append({
                    "lat": r["lat"], "lng": r["lng"],
                    "selected": apt_label in selected_labels,
                    "label": build_marker_label(r["아파트명"], deals),
                    "html": build_popup_html(r["자치구"], r["법정동"], r["아파트명"], r["주소"], deals),
                })
            render_kakao_map(marker_data)
        else:
            st.info("지도에 표시할 좌표가 없습니다.")

        # [수정됨] 엑셀 다운로드: 아파트명과 법정동을 묶어서 필터링
        if selected_labels:
            selected_df = filtered_df[
                filtered_df.apply(lambda r: f"{r['아파트명']} ({r['법정동']})" in selected_labels, axis=1)
            ]
        else:
            selected_df = pd.DataFrame(columns=DISPLAY_COLS)
            
        st.download_button(
            "⬇️ ② 지도에서 선택한 단지만",
            data=to_excel_bytes(selected_df[DISPLAY_COLS]) if not selected_df.empty else to_excel_bytes(pd.DataFrame(columns=DISPLAY_COLS)),
            file_name="선택단지_실거래.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            disabled=selected_df.empty,
        )