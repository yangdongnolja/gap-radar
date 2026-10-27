"""수정된 Streamlit에서 실제 단지정보 결합·세대수 필터·엑셀을 검사한다.

실거래 표본은 화면 검사 전용 합성 행이며, 단지목록·기본정보만 실제 조회한다.
"""
import io
import os
from pathlib import Path
import sys

import pandas as pd
import requests
from dotenv import dotenv_values
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def verify():
    settings = dotenv_values(ROOT / '.env')
    for name in ('MOLIT_API_KEY', 'KAKAO_API_KEY', 'KAKAO_REST_API_KEY'):
        os.environ.setdefault(name, settings[name])
    key = settings['MOLIT_API_KEY']
    response = requests.get('https://apis.data.go.kr/1613000/AptListService4/getSigunguAptList4',
                            params={'serviceKey': key, 'sigunguCode': '11260', 'numOfRows': 1000, 'pageNo': 1}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError('Live list query failed')
    items = response.json()['response']['body']['items']
    chosen = [items[0], next(x for x in items if '중화한신' in x['kaptName'])]
    expected = []
    for item in chosen:
        r = requests.get('https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5',
                         params={'serviceKey': key, 'kaptCode': item['kaptCode']}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError('Live basic query failed')
        expected.append(int(float(r.json()['response']['body']['item']['kaptdaCnt'])))
    print('Live Jungnang complexes:', len(items), 'sample households:', expected)
    at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=120).run()
    assert not at.exception, 'Initial app failed'
    rows = []
    for item in chosen:
        rows.append({'계약일': '2026-09-30', '자치구': '중랑구', '법정동': item['as3'],
                     '아파트명': item['kaptName'], '전용면적(㎡)': 59.4, '층': '5', '층_num': 5,
                     '거래금액(만원)': 68000, '법정동코드': item['bjdCode'][5:], '지번': '', '주소': ''})
    at.session_state['raw_df'] = pd.DataFrame(rows)
    # 미확인 단지는 제외: 실제로 세대수를 조회해야만 300세대 필터를 통과한다.
    at.checkbox(key='f_show_cinfo').check()
    at.checkbox(key='f_hh_unknown').uncheck()
    at.run(timeout=120)
    assert not at.exception, 'Result screen failed'
    assert not any('단지 정보를 가져오지 못했습니다' in w.value for w in at.warning)
    table = at.dataframe[0].value
    expected_count = sum(x >= 300 for x in expected)
    assert len(table) == expected_count
    assert all(table['세대수'] >= 300)
    assert set(table['세대수'].astype(int)) == {x for x in expected if x >= 300}
    # 실제 화면 데이터의 동일 컬럼을 Excel로 내보냈다가 다시 읽어 확인한다.
    buffer = io.BytesIO()
    table.to_excel(buffer, index=False)
    buffer.seek(0)
    exported = pd.read_excel(buffer)
    assert len(exported) == expected_count and '세대수' in exported
    print('AppTest initial/list/household-filter/summary/Excel PASS; filtered rows:', len(table))
    print('Map browser SDK/production deployment: NOT TESTED')


if __name__ == '__main__':
    try:
        verify()
    except Exception as error:
        import traceback
        print('Failure location:', [(Path(frame.filename).name, frame.lineno) for frame in traceback.extract_tb(error.__traceback__)])
        print('VERIFICATION FAILED:', type(error).__name__)
        sys.exit(1)
