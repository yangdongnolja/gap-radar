"""단지정보 함수만 로드하여 화면·비밀키 없이 API 회귀를 검사한다."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import re
import requests


def functions():
    names = {'clean_apt_name', '_norm_apt_name', '_kapt_response_body',
             'fetch_apt_list', 'fetch_complex_info', '_fill_building_info'}
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8'))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    namespace = {'re': re, 'MOLIT_API_KEY': 'fixture-private-key',
                 'requests': SimpleNamespace(get=Mock(), RequestException=requests.RequestException),
                 'CINFO_EMPTY': {'세대수': None, '준공연도': None, '건폐율(%)': None, '용적률(%)': None}}
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'app.py', 'exec'), namespace)
    return namespace


def response(body=None, status=200, code='00'):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps({'response': {'header': {'resultCode': code}, 'body': body or {}}}).encode()
    return r


class ComplexInfoTests(unittest.TestCase):
    def test_list_current_endpoint_and_pagination(self):
        n = functions()
        n['requests'].get.side_effect = [response({'items': [{'kaptCode': 'A1', 'kaptName': '동성', 'as3': '신내동'}], 'totalCount': 1001}),
                                            response({'items': {'item': {'kaptCode': 'A2', 'kaptName': '두산', 'as3': '묵동'}}, 'totalCount': 1001})]
        result = n['fetch_apt_list']('11260')
        self.assertEqual([r[0] for r in result], ['A1', 'A2'])
        self.assertTrue(n['requests'].get.call_args.args[0].endswith('AptListService4/getSigunguAptList4'))
        self.assertEqual(n['requests'].get.call_args.kwargs['params']['pageNo'], '2')

    def test_failed_response_is_not_successful_empty_list(self):
        n = functions()
        invalid = response()
        invalid._content = b'<OpenAPI_ServiceResponse>fixture-private-key</OpenAPI_ServiceResponse>'
        for r in (response(status=400), response(status=403), response(code='30'), invalid):
            n['requests'].get.return_value = r
            with self.assertRaises(RuntimeError) as error:
                n['fetch_apt_list']('11260')
            self.assertNotIn('fixture-private-key', str(error.exception))
        n['requests'].get.return_value = response({'items': [], 'totalCount': 0})
        self.assertEqual(n['fetch_apt_list']('11260'), [])

    def test_basic_info_current_endpoint_and_households(self):
        n = functions()
        n['requests'].get.return_value = response({'item': {'kaptdaCnt': '315.0', 'kaptUsedate': '19981231'}})
        result = n['fetch_complex_info']('A1')
        self.assertEqual(result['세대수'], 315)
        self.assertEqual(result['준공연도'], 1998)
        self.assertTrue(n['requests'].get.call_args.args[0].endswith('AptBasisInfoServiceV5/getAphusBassInfoV5'))
        n['requests'].get.return_value = response(code='30')
        with self.assertRaises(RuntimeError):
            n['fetch_complex_info']('A1')

    def test_request_error_does_not_expose_secret_url(self):
        n = functions()
        n['requests'].get.side_effect = requests.Timeout('https://example.test/?serviceKey=fixture-private-key')
        for function in ('fetch_apt_list', 'fetch_complex_info'):
            with self.assertRaises(RuntimeError) as error:
                n[function]('A1')
            self.assertNotIn('fixture-private-key', str(error.exception))


if __name__ == '__main__':
    unittest.main()
