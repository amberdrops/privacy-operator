"""Schift 작업 프로세스. engine/schift/.venv의 파이썬으로 `-I`(격리 모드)로 띄운다.

privacy_operator를 불러오지 않는다(다른 파이썬 환경이다). 표준 라이브러리와 schift_ko_pii만 쓴다.

- 켜자마자 통신을 막는다: 감사 훅으로 소켓 연결과 주소 조회를 모두 거부하고(내 컴퓨터 주소도),
  Hugging Face 오프라인 환경변수를 넣는다. 모델은 HF_HOME(설치 때 받아 둔 폴더)에서만 읽는다.
- 주고받기: 표준 입력 한 줄 {"texts": [...]} → 표준 출력 한 줄 {"spans": [[[시작, 끝, 이름표, 점수], ...], ...]}.
  글자는 돌려주지 않는다. 자리와 이름표만.
- 라이브러리가 표준 출력에 쓰는 글이 주고받기에 섞이지 않게, 원래 표준 출력은 따로 쥐고
  sys.stdout은 표준 오류로 돌린다. 부르는 쪽은 표준 오류를 버린다.
"""

import json
import os
import sys

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
    "TOKENIZERS_PARALLELISM": "false",
}
LABELS = ("private_person", "private_address")
_NET_EVENTS = ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
               "socket.gethostbyaddr", "socket.sendto", "socket.sendmsg")


def _block_network(event, args):
    if event in _NET_EVENTS:
        raise RuntimeError("통신 차단: " + event)


def _send(out, obj):
    out.write((json.dumps(obj, ensure_ascii=True) + "\n").encode("ascii"))
    out.flush()


def main():
    sys.addaudithook(_block_network)
    os.environ.update(OFFLINE_ENV)
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    try:
        import schift_ko_pii
        from schift_ko_pii import detect

        detect("홍길동")  # 모델을 여기서 읽는다. 실패하면 첫 요청 전에 알린다
    except Exception as e:  # noqa: BLE001
        _send(out, {"error": "LOAD_FAILED", "kind": type(e).__name__})
        return 2
    _send(out, {"ready": True, "version": getattr(schift_ko_pii, "__version__", "")})

    for raw in sys.stdin.buffer:
        try:
            req = json.loads(raw.decode("utf-8"))
            spans = []
            for text in req["texts"]:
                ents = detect(text, postprocess=True, extended=False) if text.strip() else []
                spans.append([[int(e["start"]), int(e["end"]), e["label"], float(e.get("score", 1.0))]
                              for e in ents if e.get("label") in LABELS])
            _send(out, {"spans": spans})
        except Exception as e:  # noqa: BLE001 - 글은 돌려주지 않는다. 종류만
            _send(out, {"error": "DETECT_FAILED", "kind": type(e).__name__})
    return 0


if __name__ == "__main__":
    sys.exit(main())
