# GAP-RADAR 프로젝트 컨텍스트

> 작성 기준: 2026-08-19, Git `master` 브랜치 커밋 `2414522`
>
> 이 문서는 현재 구현을 인수·유지보수하기 위한 설명서다. API 키의 실제 값은 기록하지 않는다.

## 1. 프로젝트 목적

GAP-RADAR(화면 제목: **동욱's 갭메우기 레이더**)는 서울 아파트 매매 실거래를 조회하고 가격·면적·층·세대수 조건으로 걸러서 리스트, 단지별 요약, 카카오 지도, 엑셀 파일로 보여주는 도구다.

프로젝트에는 서로 독립적으로 실행되는 두 애플리케이션이 있다.

1. `app.py`: 사람이 브라우저에서 사용하는 Streamlit 화면
2. `api/main.py`: ChatGPT Actions 같은 외부 클라이언트가 사용하는 읽기 전용 FastAPI API

두 애플리케이션은 데이터베이스를 공유하지 않는다. 국토교통부 공공데이터 API와 같은 `MOLIT_API_KEY`를 사용하지만, 호출·가공·캐시 코드는 각각 따로 구현되어 있다.

## 2. 전체 아키텍처

```text
[Streamlit 사용자]
        |
        v
     app.py
        |---- 국토교통부 실거래가 API
        |---- 공동주택 단지목록/기본정보 API
        |---- 건축물대장 API
        |---- 카카오 주소검색 REST API
        `---- 카카오 지도 JavaScript SDK

[ChatGPT Actions / API 사용자]
        |
        | X-API-Key 인증
        v
  api/main.py (FastAPI)
        |
        v
  api/molit.py
        |
        `---- 국토교통부 실거래가 API
```

- 영구 데이터베이스나 파일 저장소는 없다.
- Streamlit은 `st.session_state`와 `st.cache_data`를 사용한다.
- FastAPI는 프로세스 메모리의 단순 TTL 캐시를 사용한다.
- 서버나 Streamlit 프로세스가 재시작되면 세션 및 FastAPI 캐시는 초기화된다.

## 3. 폴더 구조

```text
아파트아파트/
├─ .git/                    # Git 저장소 메타데이터
├─ .claude/                 # Claude Code 로컬 실행/권한 설정(Git 제외)
│  ├─ launch.json
│  └─ settings.local.json
├─ api/
│  ├─ main.py               # FastAPI 앱, 인증, 검증, 응답 모델, 엔드포인트
│  ├─ molit.py              # FastAPI용 국토부 실거래 조회 모듈
│  ├─ requirements.txt      # FastAPI 배포 의존성
│  └─ __pycache__/          # 로컬 생성 캐시(Git 제외, 삭제해도 재생성됨)
├─ .env                     # 로컬 비밀키(Git 제외)
├─ .gitignore               # 비밀키·캐시·로컬 설정·엑셀 제외 규칙
├─ app.py                   # Streamlit 앱 전체 구현
├─ render.yaml              # Render FastAPI Blueprint
├─ requirements.txt         # Streamlit 앱 의존성
├─ 앱실행.bat               # Windows용 Streamlit 실행 도우미
└─ PROJECT_CONTEXT.md       # 현재 문서
```

현재 저장소는 소수의 파일로 구성되어 있고, Streamlit 화면·데이터 수집·가공·지도 HTML이 977줄짜리 `app.py` 하나에 모여 있다.

## 4. 파일별 역할

### `app.py`

Streamlit 앱의 단일 진입점이다.

- `.env`에서 국토부 및 카카오 키를 읽고 필수 키 3개를 검사한다.
- 서울 25개 자치구명과 법정동 코드 앞 5자리(`LAWD_CD`)를 관리한다.
- 최대 3개월의 조회 월 목록을 만든다.
- 국토부 XML 실거래 응답을 파싱하고 취소 거래를 제외한다.
- 공동주택 단지목록과 실거래 단지명을 법정동+유사도 기준으로 연결한다.
- 단지 기본정보 및 건축물대장에서 세대수, 준공연도, 건폐율, 용적률을 가져온다.
- 카카오 REST API로 주소를 위도·경도로 변환한다.
- 카카오 지도 JavaScript SDK용 HTML/JavaScript를 생성해 Streamlit iframe에 표시한다.
- 가격, 면적, 층, 세대수, 검색어 필터와 검색 전 확인 단계를 제공한다.
- 거래 리스트, 단지별 요약, 페이지네이션, 네이버 검색 링크를 제공한다.
- 전체 필터 결과, 단지별 요약, 지도 선택 단지를 XLSX로 다운로드한다.

### `api/main.py`

독립 FastAPI 애플리케이션이다. `app.py`를 import하지 않는다.

- 앱 메타데이터와 OpenAPI 문서를 생성한다.
- 공개 `/health`와 인증이 필요한 실거래/단지 분석 API를 제공한다.
- `X-API-Key` 헤더를 `GAP_RADAR_API_KEY`와 비교한다.
- 입력 범위와 `YYYY-MM` 형식을 검사한다.
- `api/molit.py`의 사용자 정의 예외를 일관된 HTTP 오류로 바꾼다.
- Pydantic 응답 모델로 API 형식을 문서화한다.
- 모든 출처를 허용하는 CORS 설정이며 GET만 허용한다.

### `api/molit.py`

FastAPI에서 사용하는 Streamlit 비의존 실거래 조회 모듈이다.

- 프로젝트 루트 `.env`를 명시적으로 찾는다.
- 서울 25개 구 코드 및 서울 별칭을 검증한다.
- 국토부 실거래 XML을 Python 딕셔너리로 변환한다.
- 취소 거래를 제외하고 페이지당 1,000건씩 전체 페이지를 조회한다.
- `(자치구 코드, YYYYMM)` 단위로 30분 메모리 캐시를 둔다.
- 설정, 지역, 요청, 응답 오류를 별도 예외 클래스로 구분한다.

### `render.yaml`

FastAPI만 Render에 배포하는 Blueprint다. `rootDir: api`이므로 루트의 Streamlit 앱은 이 배포에 포함되지 않는다.

### 의존성 파일

- 루트 `requirements.txt`: Streamlit 앱용
- `api/requirements.txt`: FastAPI/Render용

### 기타 파일

- `.env`: 로컬 키 저장소. Git에 포함되지 않는다.
- `.gitignore`: `.env`, `.claude/`, `__pycache__/`, 가상환경, XLSX 등을 제외한다.
- `앱실행.bat`: 자신의 폴더로 이동한 뒤 `python -m streamlit run app.py`를 실행한다.
- `.claude/launch.json`: Claude Code에서 Streamlit을 8501 포트로 실행하기 위한 로컬 설정이다.
- `.claude/settings.local.json`: Claude Code 로컬 권한 설정이며 Git에서 제외된다.

## 5. Streamlit 앱 구조

화면은 다음 순서로 작동한다.

1. 페이지 설정, 제목과 설명 표시
2. 필수 환경변수 검사
3. 세션 상태 및 필터 기본값 초기화
4. 사이드바에서 자치구(복수), 시작월, 종료월 입력
5. 가격·면적·층·세대수·단지/동 검색 필터 입력
6. **검색 조건 확인**을 눌러 예상 API 호출 횟수와 시간을 확인
7. **조회 시작**을 눌러 자치구×월 조합을 순차 조회
8. 원본 결과를 `st.session_state.raw_df`에 보관
9. 화면 재실행 때 현재 필터를 적용
10. 세대수 필터나 단지 정보 표시가 켜져 있으면 추가 공동주택 API 호출
11. 리스트 탭과 지도 탭으로 표시

주요 캐시 TTL은 다음과 같다.

| 대상 | 캐시 | 유지 시간 |
|---|---|---:|
| 실거래가 | `st.cache_data` | 30분 |
| 공동주택 단지목록 | `st.cache_data` | 24시간 |
| 단지 상세정보 | `st.cache_data` | 24시간 |
| 카카오 주소 좌표 | `st.cache_data` | 30일 |

## 6. FastAPI 백엔드 및 주요 Endpoint

### `GET /health`

- 인증 없음
- Render 상태 검사에 사용
- 응답: `{"status": "ok"}`

### `GET /api/v1/transactions`

- `X-API-Key` 필요
- 한 달의 개별 실거래 목록 반환
- `gu` 생략 시 서울 25개 구 전체 조회
- `year` 또는 `month` 중 하나라도 없으면 둘 다 지난달로 설정
- 가격, 면적, 단지명 부분 검색 지원
- 최신 계약일 순으로 정렬
- `limit` 기본 200, 최대 500
- 전체 건수(`count`), 실제 반환 건수, 잘림 여부를 함께 반환

주요 쿼리: `sido`, `gu`, `year`, `month`, `min_price`, `max_price`, `min_area`, `max_area`, `complex_name`, `limit`

### `GET /api/v1/complex-analysis`

- `X-API-Key` 필요
- 1~3개월의 거래를 단지·법정동·반올림 면적별로 묶어 분석
- 거래건수, 평균/최저/최고/최근 가격과 최근 계약일 반환
- 조회 범위 첫 달과 마지막 달의 통계 및 가격 변화율을 조건부 반환

주요 쿼리: `sido`, `gu`, `from_year_month`, `to_year_month`, `min_price`, `max_price`, `min_area`, `max_area`, `min_transactions`

### 자동 문서

- `/docs`: Swagger UI
- `/openapi.json`: ChatGPT Actions 등에서 사용할 OpenAPI 스키마

## 7. 외부 API와 용도

| 제공자/서비스 | 사용 위치 | 용도 | 필요한 키 |
|---|---|---|---|
| 국토교통부 아파트 매매 실거래 상세자료 | `app.py`, `api/molit.py` | 계약일, 단지, 면적, 층, 거래금액 조회 | `MOLIT_API_KEY` |
| 국토교통부 공동주택 단지목록 | `app.py` | 실거래 단지명과 `kaptCode` 연결 | `MOLIT_API_KEY` 및 해당 API 활용신청/승인 |
| 국토교통부 공동주택 기본정보 | `app.py` | 세대수, 사용승인일(준공연도), 주소·법정동코드 | `MOLIT_API_KEY` 및 해당 API 활용신청/승인 |
| 국토교통부 건축물대장 총괄표제부 | `app.py` | 건폐율, 용적률 | `MOLIT_API_KEY` 및 해당 API 활용신청/승인 |
| Kakao Local 주소검색 REST API | `app.py` | 주소를 위도·경도로 변환 | `KAKAO_REST_API_KEY` |
| Kakao Maps JavaScript SDK | `app.py` | 지도·마커·팝업 표시 | `KAKAO_API_KEY`(JavaScript 키), 허용 도메인 설정 |
| 네이버 통합검색 | `app.py` | 단지 상세정보 검색 링크 | 키 없음 |

주의: 하나의 공공데이터포털 키를 쓰더라도 각 국토부 API의 활용신청 및 승인 상태는 별개일 수 있다.

## 8. 환경변수와 Secret

| 변수 | 사용 위치 | 필수 여부 | 목적 |
|---|---|---|---|
| `MOLIT_API_KEY` | Streamlit, FastAPI | 필수 | 국토부 공공데이터 호출 |
| `KAKAO_API_KEY` | Streamlit | 필수 | 카카오 지도 JavaScript SDK |
| `KAKAO_REST_API_KEY` | Streamlit | 필수 | 카카오 주소→좌표 변환 |
| `GAP_RADAR_API_KEY` | FastAPI | 필수 | 외부 사용자의 `X-API-Key` 인증 |
| `PORT` | Render | Render 제공 | Uvicorn 수신 포트 |
| `PYTHON_VERSION` | Render | Blueprint 지정 | Render Python 런타임 버전 |

로컬 `.env` 예시(실제 값을 Git에 올리지 말 것):

```dotenv
MOLIT_API_KEY=발급받은_공공데이터_서비스키
KAKAO_API_KEY=카카오_JavaScript_키
KAKAO_REST_API_KEY=카카오_REST_API_키
GAP_RADAR_API_KEY=충분히_긴_로컬_API_인증키
```

- 현재 `.env`에는 위 네 변수 이름이 모두 존재한다. 실제 값의 유효성은 이 분석에서 검사하지 않았다.
- `.env`는 `.gitignore`에 포함되고 현재 Git 추적 대상도 아니다.
- Render에는 `MOLIT_API_KEY`와 `GAP_RADAR_API_KEY`를 대시보드 Secret으로 직접 입력한다.
- Streamlit 배포 환경에서는 세 키를 해당 플랫폼의 Secret/환경변수 기능으로 설정해야 한다.
- 키를 로그, 오류 메시지, 문서, 커밋에 복사하지 않는다. 노출되면 즉시 폐기·재발급한다.

## 9. 데이터 처리 흐름

### Streamlit

```text
사용자 검색 조건
  -> 자치구 코드와 YYYYMM 조합 생성(최대 3개월)
  -> 국토부 실거래 XML 페이지 반복 조회
  -> 취소 거래 제외 및 숫자/날짜/주소 정규화
  -> Pandas DataFrame으로 결합
  -> 가격/면적/층/검색어 필터
  -> 필요 시 단지목록 이름 매칭
  -> 단지 기본정보 + 건축물대장 결합
  -> 세대수 필터
  -> 리스트/그룹 요약/XLSX 생성
  -> 주소를 카카오 좌표로 변환
  -> 최대 300개 단지를 카카오 지도에 표시
```

단지 매칭은 `(자치구, 법정동, 아파트명)`을 기준으로 처리한다. 아파트명의 괄호와 특수문자를 제거한 뒤 `SequenceMatcher` 유사도를 사용하며, 같은 법정동 후보를 우선한다. 이는 동명이인 단지의 정보가 섞이는 것을 줄이지만 완전한 고유키 연결은 아니다.

### FastAPI

```text
HTTP GET + X-API-Key
  -> 인증 및 쿼리 검증
  -> 서울 자치구/월 조합 결정
  -> api/molit.py에서 국토부 XML 조회·파싱·30분 캐시
  -> 가격/면적/단지명 필터
  -> transactions: 최신순 정렬 및 limit 적용
     또는
     complex-analysis: 단지+동+반올림 면적 그룹 통계
  -> Pydantic JSON 응답
```

## 10. 로컬 실행방법

### 최초 설치

Windows PowerShell에서 프로젝트 폴더를 연 뒤 다음을 실행한다.

```powershell
cd "C:\Users\yang-\Desktop\아파트아파트"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r api\requirements.txt
```

PowerShell이 스크립트 실행을 차단하면 새 터미널에서 다음을 한 번 실행한 뒤 다시 활성화한다.

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

프로젝트 루트에 `.env`를 준비하되 실제 키는 Git에 커밋하지 않는다.

### Streamlit 실행

```powershell
cd "C:\Users\yang-\Desktop\아파트아파트"
.\.venv\Scripts\Activate.ps1
python -m streamlit run app.py
```

또는 `앱실행.bat`를 더블클릭한다. 단, 이 배치 파일은 PATH에 연결된 기본 Python을 사용하므로 가상환경이 자동 활성화되지는 않는다.

### FastAPI 실행

```powershell
cd "C:\Users\yang-\Desktop\아파트아파트\api"
..\.venv\Scripts\Activate.ps1
python -m uvicorn main:app --reload --port 8000
```

확인 주소:

- 상태: `http://127.0.0.1:8000/health`
- 문서: `http://127.0.0.1:8000/docs`

인증 API 예시:

```powershell
$headers = @{ "X-API-Key" = $env:GAP_RADAR_API_KEY }
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/transactions?gu=강남구&year=2026&month=7&limit=10" -Headers $headers
```

## 11. 배포방법과 Render 구조

현재 `render.yaml`은 다음 한 개의 Render Web Service를 만든다.

- 서비스명: `gap-radar-api`
- 요금제: free
- 루트: `api/`
- 빌드: `pip install -r requirements.txt`
- 시작: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- 헬스 체크: `/health`
- 자동 배포: Git 커밋
- 대시보드 입력 Secret: `MOLIT_API_KEY`, `GAP_RADAR_API_KEY`
- Blueprint 지정 Python: `3.14.6`

일반적인 배포 절차:

1. GitHub `master`에 검증된 변경을 push한다.
2. Render에서 저장소를 연결하고 Blueprint(`render.yaml`)를 적용한다.
3. Render 대시보드에서 두 Secret 값을 입력한다.
4. 배포 로그에서 의존성 설치와 Uvicorn 시작을 확인한다.
5. `/health`, `/docs`, 인증 API를 순서대로 확인한다.

`render.yaml`은 Streamlit을 배포하지 않는다. Streamlit 앱의 실제 원격 배포 설정 파일은 현재 저장소에 없으며, 주석상 Streamlit Cloud를 별도로 사용하는 구조를 전제로 한다. 따라서 Streamlit 배포 URL·플랫폼 설정·Secret 등록 상태는 저장소만으로 확인할 수 없다.

## 12. requirements와 주요 Python 라이브러리

### Streamlit 앱

| 라이브러리 | 지정 버전 | 역할 |
|---|---|---|
| `streamlit` | `>=1.36` | 웹 UI, 세션, 캐시, 다운로드 |
| `requests` | `>=2.31` | 외부 HTTP API 호출 |
| `pandas` | `>=2.2` | 표 형식 데이터 가공·그룹 통계 |
| `python-dotenv` | `>=1.0` | `.env` 로드 |
| `openpyxl` | `>=3.1` | XLSX 생성 |

### FastAPI 서버

| 라이브러리 | 지정 버전 | 역할 |
|---|---|---|
| `fastapi` | `>=0.110` | API, 검증, OpenAPI |
| `uvicorn[standard]` | `>=0.27` | ASGI 서버 |
| `requests` | `>=2.31` | 국토부 API 호출 |
| `python-dotenv` | `>=1.0` | `.env` 로드 |

버전이 모두 최소값(`>=`)만 있어 설치 시점에 따라 결과가 달라질 수 있다. 재현 가능한 배포가 필요해지면 검증된 버전을 잠그는 방안을 별도 변경으로 검토한다.

## 13. 구현 완료 기능

### Streamlit

- 서울 25개 자치구 복수 선택
- 1~3개월 실거래 조회 및 페이지네이션
- 취소 거래 제외
- 검색 전 호출 횟수·예상 시간 확인
- 가격, 면적, 층, 세대수, 단지/동 검색 필터
- 필터 기본 프리셋 및 초기화
- 공동주택 세대수·준공연도·건폐율·용적률 결합
- 법정동 우선 단지명 유사도 매칭
- 리스트 50건 단위 페이지 표시
- 단지별 거래건수·평균·최저·최고가 요약
- 네이버 단지 검색 링크
- 카카오 지도 라벨, 선택 강조, 상세 팝업
- 최대 300개 지도 단지 제한
- 필터 결과, 단지 요약, 지도 선택 단지 XLSX 다운로드
- Streamlit 및 좌표/공공데이터 캐시

### FastAPI

- 상태 확인
- `X-API-Key` 인증
- 한 달 실거래 목록, 필터, 최신순 제한 응답
- 최대 3개월 단지별 통계 및 월간 가격 변화율
- 서울 전체 또는 한 자치구 조회
- 30분 인메모리 캐시
- 오류 유형별 JSON 응답과 OpenAPI 응답 모델
- Render Blueprint 및 헬스 체크

## 14. 미완성 또는 임시로 보이는 부분

- 서울만 지원한다. 다른 시·도 코드나 지역 검색 기능은 없다.
- Streamlit과 FastAPI가 실거래 조회·파싱·서울 코드 로직을 중복 보유한다.
- 데이터베이스와 영구 캐시가 없어 재시작 후 다시 외부 API를 호출한다.
- 자동화 테스트와 CI 설정이 없다.
- Streamlit 원격 배포 설정은 저장소에 없다.
- 단지 매칭이 공공데이터의 확정 고유키가 아니라 문자열 유사도 기반이라 일부 단지는 미연결 또는 오연결될 수 있다.
- 카카오 지도 HTML/JavaScript가 `app.py` 내부의 긴 문자열로 들어 있어 독립적인 프론트엔드 테스트가 어렵다.
- UI의 다운로드 버튼 번호가 `①`, `③`, `②` 순으로 표시되어 임시 수정 흔적처럼 보인다.
- 주석에 로컬 Python 3.14.6이 언급되지만 개발 환경 고정 파일(`.python-version` 등)은 없다.

## 15. 오류 가능성과 기술 부채

### 우선순위 높음

1. **TLS 인증서 검증 비활성화**  
   국토부 요청에서 `verify=False`를 사용하고 경고까지 전역 비활성화한다. 중간자 공격 및 잘못된 인증서 감지를 막으므로 운영 보안상 개선이 필요하다. 인증서 문제가 발생했던 배경을 먼저 확인한 뒤 기본 검증을 복구해야 한다.

2. **FastAPI `async` 엔드포인트 안의 동기 네트워크 호출**  
   `requests.get`을 순차 호출하므로 한 요청이 이벤트 루프를 오래 막을 수 있다. 특히 `gu`를 생략한 서울 전체 조회는 최대 25구×3개월 호출이 직렬 실행된다. 동시 사용자 증가 시 타임아웃과 응답 지연 가능성이 크다.

3. **공공 API 전체 조회의 호출량과 시간 제한**  
   Streamlit도 자치구×월을 순차 조회하고, 단지정보는 단지마다 추가 호출한다. 무료 플랫폼과 외부 API의 요청 제한에 걸릴 수 있다. 현재 재시도는 없고 일부 API 오류는 조용히 빈 정보로 처리된다.

4. **FastAPI 3개월 비교 설명과 구현 불일치**  
   응답 모델과 `note`는 월별 비교 필드가 “정확히 2개월일 때만” 존재한다고 설명한다. 하지만 구현은 2개월 이상이면 첫 달과 마지막 달을 사용하므로 3개월 조회에도 필드가 채워진다. API 소비자가 문서와 다른 응답을 받는다.

5. **Python 3.14.6 배포 호환성 확인 필요**  
   Render Blueprint가 Python 3.14.6을 고정한다. Render 지원 여부와 `fastapi`, `uvicorn`, 바이너리 의존성 호환성을 실제 배포 로그로 검증해야 한다. 검증 없이 버전만 낮추거나 올리지 않는다.

### 우선순위 중간

6. **날짜 입력의 부분 누락 처리**  
   `/transactions`에서 `year`나 `month` 중 하나만 제공해도 오류 대신 두 값을 모두 지난달로 덮어쓴다. 사용자의 입력 실수를 숨길 수 있으므로 둘 다 입력하거나 둘 다 생략하도록 검증하는 편이 명확하다.

7. **너무 넓은 예외 처리**  
   `app.py`의 단지정보와 지오코딩은 여러 `except Exception: pass`로 실패 원인을 숨긴다. 사용자 화면에는 친절한 메시지를 유지하되 서버 로그에는 원인을 남길 필요가 있다.

8. **FastAPI 전역 예외 처리의 관찰성 부족**  
   예상치 못한 예외를 500으로 변환하지만 예외 로깅이 없다. 운영 장애 원인을 Render 로그에서 찾기 어렵다.

9. **메모리 캐시의 동시성·크기 관리 부재**  
   `api/molit.py` 캐시는 만료 항목을 주기적으로 제거하지 않고 프로세스 간 공유되지 않는다. 현재 규모에서는 단순하지만 트래픽과 조회 월이 늘면 관리가 필요하다.

10. **API 키 비교와 CORS 정책**  
    일반 문자열 비교를 사용하며, CORS는 모든 출처와 헤더를 허용한다. 읽기 전용 키 기반 API라 즉시 기능 오류는 아니지만 공개 운영 범위에 맞춰 제한 및 안전한 비교를 검토할 수 있다.

11. **단일 대형 Streamlit 파일**  
    UI, API 클라이언트, 데이터 가공, 지도 JavaScript가 한 파일에 결합되어 수정 영향 범위가 크다. 다만 기존 기능 보호 원칙상 테스트 없이 대규모 분리 리팩터링을 먼저 하면 안 된다.

12. **URL 구성과 지도 SDK 우회 코드**  
    네이버 링크 일부는 원문 문자열을 직접 URL에 붙인다. 카카오 SDK의 HTTP 스크립트를 HTTPS로 바꾸기 위해 `HTMLScriptElement.prototype.src`를 재정의하는 우회는 SDK 동작 변경에 민감하다.

### 품질 관리

13. **자동 테스트 부재**  
    월 범위, XML 파싱, 취소 거래, 단지 매칭, 필터, API 인증 및 응답 모델에 회귀 테스트가 없다.

14. **의존성 버전 범위가 넓음**  
    상한이나 잠금 파일이 없어 새 버전 공개 후 갑자기 빌드 또는 실행이 달라질 수 있다.

15. **중복 코드로 인한 동작 차이 위험**  
    Streamlit과 FastAPI 파서는 층의 실패값(`0` 대 `None`) 등 세부 표현이 다르다. 한쪽만 버그 수정하면 결과가 서로 달라질 수 있다.

## 16. Git/GitHub 현재 상태

문서 생성 직전 확인 결과:

- 현재 브랜치: `master`
- 원격 추적: `origin/master`
- 원격 저장소: `https://github.com/yangdongnolja/gap-radar.git`
- HEAD: `2414522 fix: ChatGPT Actions 연동 안정성 개선 (OpenAPI 검토 반영)`
- 로컬 `master`와 `origin/master`가 같은 커밋을 가리킴
- 기존 추적 파일 수정 없음
- `.env`, `.claude/`, `__pycache__/`는 ignore 규칙에 따라 Git 제외
- 현재 이 문서를 새로 만들었으므로 이후 `git status`에는 `PROJECT_CONTEXT.md`가 새 파일로 표시되는 것이 정상

최근 변경 이력은 FastAPI/Render 추가와 ChatGPT Actions 연동 안정화, 그 이전 Streamlit 필터·지도·매칭 개선에 집중되어 있다.

## 17. 확인 및 테스트 결과

- `app.py`, `api/main.py`, `api/molit.py`를 Python AST로 읽는 구문 검사는 통과했다.
- FastAPI 앱 import/라우트 로딩 검사는 현재 Codex 제공 Python 환경에 `python-dotenv`가 설치되어 있지 않아 수행하지 못했다. 이는 프로젝트 코드 실패가 아니라 검사 환경 의존성 미설치다.
- 실제 국토부·카카오 API 호출은 키 노출, 호출량, 네트워크 영향을 피하기 위해 수행하지 않았다.
- Streamlit 브라우저 렌더링과 Render 실제 배포 상태는 이번 저장소 정적 분석 범위에서 확인하지 않았다.
- 자동 테스트 파일은 저장소에 없다.

## 18. 앞으로 개발할 때 주의할 점

1. 작업 전 `git status`를 확인하고 변경 목적과 대상 파일을 먼저 설명한다.
2. 기존 동작을 재현한 뒤 작은 단위로 수정한다. 테스트 없이 `app.py` 전체를 재작성하지 않는다.
3. `.env`와 모든 키 값을 코드, 문서, 커밋, 화면 오류, 로그에 넣지 않는다.
4. Streamlit과 FastAPI의 실거래 파싱 동작을 함께 비교한다. 공통 규칙을 바꿀 때 한쪽만 수정하지 않는다.
5. 공공데이터 XML/JSON은 빈 항목, 단일 객체/목록 차이, 오류 XML, 호출 제한을 항상 고려한다.
6. 취소 거래 제외 로직을 유지하고 수정 후 표본 XML로 회귀 검사한다.
7. 거래금액 단위는 **만원**, UI 가격 입력은 **천만원**, 면적은 **㎡**임을 혼동하지 않는다.
8. 단지명만으로 정보를 합치지 말고 자치구·법정동을 함께 사용한다.
9. API 호출을 병렬화할 경우 공공데이터 호출 제한과 스레드 안전성을 먼저 확인한다.
10. 카카오 JavaScript 키의 허용 도메인에 로컬 및 실제 Streamlit 배포 주소가 등록되어야 한다.
11. Render의 `rootDir: api` 구조와 Secret 입력 방식을 유지한다. 배포 명령을 바꾸기 전 로컬에서 같은 명령으로 확인한다.
12. API 응답 필드와 OpenAPI 설명을 함께 변경해 ChatGPT Actions 계약을 깨뜨리지 않는다.
13. 변경 후 최소한 구문 검사, 핵심 함수 테스트, `/health`, `/docs`, 표본 API, Streamlit 검색·필터·지도·XLSX를 영향 범위에 맞게 확인한다.
14. 대규모 리팩터링이 필요하면 먼저 현재 동작을 고정하는 테스트를 추가하고 여러 작은 커밋으로 나눈다.

## 19. 권장 개선 순서

현재 기능을 보존한다는 전제에서 다음 순서가 안전하다.

1. XML 파싱, 월 범위, 인증, 필터 및 단지 분석에 자동 테스트 추가
2. 3개월 비교 문서/구현 불일치와 연·월 부분 입력 처리 수정
3. 오류 로깅과 사용자 친화적 오류 메시지 보강
4. TLS 검증 비활성화 원인 확인 후 안전하게 복구
5. Render Python 버전 및 의존성 호환성을 실제 배포에서 고정·검증
6. FastAPI의 블로킹 호출과 서울 전체 조회 성능 개선
7. 테스트 보호 아래 중복 국토부 파싱 로직을 공통 모듈로 점진 통합
8. 마지막 단계에서만 `app.py`의 UI·서비스·지도 코드를 작은 모듈로 분리

