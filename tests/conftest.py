"""공통 fixture. 샘플은 세션에서 한 번만 읽는다. kordoc 엔진이 없으면 엔진이 필요한 테스트만 건너뛴다."""

from __future__ import annotations

import pytest
from answer_utils import SAMPLE_FILES, SAMPLES

from privacy_operator.reader.engine import EngineNotFound, find_cli, find_node


@pytest.fixture(scope="session")
def reader():
    try:
        find_node()
        find_cli()
    except EngineNotFound as e:  # pragma: no cover
        pytest.skip(str(e))
    from privacy_operator.reader import DocumentReader

    with DocumentReader() as r:
        yield r


@pytest.fixture(scope="session")
def results(reader):
    return {name: reader.read(SAMPLES / name) for name in SAMPLE_FILES}
