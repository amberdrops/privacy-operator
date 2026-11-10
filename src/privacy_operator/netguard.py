"""통신 차단 장치. 절대 규칙 1을 실행 중에도 지킨다.

우리 코드는 통신 모듈을 쓰지 않는다(tests/test_no_network.py). 그래도 함께 쓰는 라이브러리가 몰래
밖으로 나가지 못하게, 파이썬 감사 훅(sys.addaudithook)으로 소켓 연결과 주소 조회를 가로챈다.
내 컴퓨터 주소(127.0.0.1, ::1)만 통과시키고 나머지는 NetworkBlocked로 막는다.

- 한 번 걸면 풀 수 없다(감사 훅은 지울 수 없다). 프로그램을 켤 때, AI 탐지 작업 프로세스를 켤 때 건다.
- loopback=False면 내 컴퓨터 주소도 막는다(Schift 작업 프로세스는 아무와도 통신할 일이 없다).
- 파이썬 밖(Node의 kordoc, Ollama 프로그램 자체)은 막지 못한다. 그쪽은 KORDOC_OFFLINE=1,
  Ollama 접속 주소 고정과 클라우드 모델 차단으로 막는다.
- 이 파일은 socket을 불러오지 않는다. 소켓 이름표를 보고 판단만 한다.
"""

from __future__ import annotations

import sys

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})

_installed: set[bool] = set()


class NetworkBlocked(RuntimeError):
    pass


def _host_of(address) -> str | None:
    if isinstance(address, tuple) and address:
        host = address[0]
        return host.decode() if isinstance(host, bytes) else str(host)
    return None  # AF_UNIX 경로 같은 것


def _is_local(host: str | None) -> bool:
    if host is None:
        return True
    h = host.strip().lower()
    if h.startswith("::ffff:"):
        h = h[len("::ffff:"):]
    return h in LOOPBACK_HOSTS


def make_hook(loopback: bool = True):
    def hook(event: str, args: tuple) -> None:
        if not event.startswith("socket."):
            return
        if event in ("socket.connect", "socket.sendto", "socket.sendmsg"):
            host = _host_of(args[1]) if len(args) > 1 else None
        elif event == "socket.getaddrinfo":
            host = None if args[0] is None else str(args[0].decode() if isinstance(args[0], bytes) else args[0])
        elif event in ("socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr"):
            host = str(args[0]) if args else ""
        elif event == "socket.bind":
            host = _host_of(args[1]) if len(args) > 1 else None
        else:
            return
        if loopback and _is_local(host):
            return
        if not loopback and event == "socket.bind" and _is_local(host):
            return  # 프로세스 안 소켓 짝(asyncio, multiprocessing)은 밖으로 나가지 않는다
        raise NetworkBlocked(f"통신 차단: {event}")

    return hook


def install(loopback: bool = True) -> None:
    """감사 훅을 건다. 같은 종류를 두 번 걸지 않는다."""
    if loopback in _installed:
        return
    sys.addaudithook(make_hook(loopback))
    _installed.add(loopback)
