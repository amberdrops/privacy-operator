"""조사 보정 규칙마다 따로 시험한다. 이름은 모두 지어낸 것이다.

받침 없음: 김민수, 박하나   받침 있음: 이도윤, 정하람   ㄹ 받침: 한별, 최다올
"""

from __future__ import annotations

import pytest

from privacy_operator.restore import restore

NAMES = {
    "[강사-01]": ("김민수", False),
    "[강사-02]": ("이도윤", True),
    "[강사-03]": ("한별", True),  # ㄹ 받침
    "[강사-04]": ("박하나", False),
    "[전화-01]": ("010-0000-0001", True),  # 일: ㄹ 받침
    "[전화-02]": ("010-0000-0002", False),  # 이
    "[전화-03]": ("010-0000-0003", True),  # 삼
    "[이메일-01]": ("a@example.kr", True),  # 알: ㄹ 받침
    "[이메일-02]": ("a@example.net", True),  # 닷넷
}
MAPPING = {"mission_id": "M-20261107-01", "entries": [
    {"alias_code": code, "category": "이름", "raw_value": value, "has_batchim": b, "source_files": []}
    for code, (value, b) in NAMES.items()]}


def r(text: str) -> str:
    return restore(text, MAPPING).text


# ---------------------------------------------------------------- 은/는

@pytest.mark.parametrize("given,expected", [
    ("[강사-01]는 강의한다", "김민수는 강의한다"),
    ("[강사-01]은 강의한다", "김민수는 강의한다"),
    ("[강사-02]는 강의한다", "이도윤은 강의한다"),  # 완료 기준 예
    ("[강사-02]은 강의한다", "이도윤은 강의한다"),
    ("[강사-03]는", "한별은"),
])
def test_eun_neun(given, expected):
    assert r(given) == expected


# ---------------------------------------------------------------- 이/가

@pytest.mark.parametrize("given,expected", [
    ("[강사-01]이 맡는다", "김민수가 맡는다"),
    ("[강사-01]가 맡는다", "김민수가 맡는다"),
    ("[강사-02]가 맡는다", "이도윤이 맡는다"),
    ("[강사-02]이 맡는다", "이도윤이 맡는다"),
    ("[전화-02]가 바뀌었다", "010-0000-0002가 바뀌었다"),
    ("[전화-03]가 바뀌었다", "010-0000-0003이 바뀌었다"),
])
def test_i_ga(given, expected):
    assert r(given) == expected


# ---------------------------------------------------------------- 을/를

@pytest.mark.parametrize("given,expected", [
    ("[강사-01]을 초청", "김민수를 초청"),
    ("[강사-02]를 초청", "이도윤을 초청"),
    ("[강사-04]을, ", "박하나를, "),
])
def test_eul_reul(given, expected):
    assert r(given) == expected


# ---------------------------------------------------------------- 과/와

@pytest.mark.parametrize("given,expected", [
    ("[강사-01]과 [강사-02]와 함께", "김민수와 이도윤과 함께"),
    ("[강사-02]와 함께", "이도윤과 함께"),
    ("[강사-04]과.", "박하나와."),
])
def test_gwa_wa(given, expected):
    assert r(given) == expected


# ---------------------------------------------------------------- 으로/로 (ㄹ 받침 뒤는 '로')

@pytest.mark.parametrize("given,expected", [
    ("[강사-01]으로 정했다", "김민수로 정했다"),
    ("[강사-02]로 정했다", "이도윤으로 정했다"),
    ("[강사-03]으로 정했다", "한별로 정했다"),  # ㄹ 받침
    ("[전화-01]으로 연락", "010-0000-0001로 연락"),  # 일
    ("[전화-03]로 연락", "010-0000-0003으로 연락"),  # 삼
    ("[이메일-01]으로 보내", "a@example.kr로 보내"),  # 알
    ("[이메일-02]로 보내", "a@example.net으로 보내"),  # 닷넷
])
def test_euro_ro(given, expected):
    assert r(given) == expected


@pytest.mark.parametrize("given,expected", [
    ("[강사-02]로서 참석", "이도윤으로서 참석"),
    ("[강사-01]으로써", "김민수로써"),
    ("[강사-02]로부터 받은", "이도윤으로부터 받은"),
    ("[강사-03]으로는 부족", "한별로는 부족"),
    ("[강사-02]로의 변경", "이도윤으로의 변경"),
])
def test_euro_ro_longer_forms(given, expected):
    assert r(given) == expected


# ---------------------------------------------------------------- 이나/나

@pytest.mark.parametrize("given,expected", [
    ("[강사-01]이나 [강사-02]나 상관없다", "김민수나 이도윤이나 상관없다"),
    ("[강사-03]나", "한별이나"),
])
def test_ina_na(given, expected):
    assert r(given) == expected


# ---------------------------------------------------------------- 이랑/랑

@pytest.mark.parametrize("given,expected", [
    ("[강사-01]이랑 갔다", "김민수랑 갔다"),
    ("[강사-02]랑 갔다", "이도윤이랑 갔다"),
])
def test_irang_rang(given, expected):
    assert r(given) == expected


# ---------------------------------------------------------------- 조사가 아닌 것은 건드리지 않는다

@pytest.mark.parametrize("given,expected", [
    ("[강사-01]이다", "김민수이다"),  # 서술격 조사
    ("[강사-02]이에요", "이도윤이에요"),
    ("[강사-01] 강사가", "김민수 강사가"),  # 조사가 띄어 쓴 다음 낱말에 붙음
    ("[강사-01]님은", "김민수님은"),
    ("[강사-01]의 강의", "김민수의 강의"),  # 받침과 상관없는 조사
    ("[강사-02]에게", "이도윤에게"),
    ("[강사-02]도", "이도윤도"),
    ("[강사-01]", "김민수"),
])
def test_not_a_correctable_josa(given, expected):
    assert r(given) == expected


@pytest.mark.parametrize("punct", [".", ",", "!", "?", ")", "\"", "”", "…", "\n"])
def test_josa_before_punctuation(punct):
    assert r(f"[강사-02]는{punct}") == f"이도윤은{punct}"
