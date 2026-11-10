"""내 컴퓨터의 Ollama와만 주고받는다. 절대 규칙 1의 유일한 예외(tests/test_no_network.py ALLOWED_FILES).

- 접속 주소는 HOST 하나로 고정한다. 설정에서 바꿀 수 있는 것은 포트뿐이다.
- 클라우드 모델은 목록에서 빼고, 이름을 직접 줘도 거부한다. Ollama는 클라우드 모델 요청을 내 컴퓨터에서
  받아 바깥 서버로 넘기므로, 127.0.0.1로만 접속해도 클라우드 모델을 부르면 글이 밖으로 나간다.
  가리는 기준: 이름의 꼬리표가 `cloud`이거나 `-cloud`로 끝남(`gemma4:cloud`, `gpt-oss:120b-cloud`),
  모델 정보에 원격 서버 표시(remote_host, remote_model)가 있음.
- http.client는 프록시 환경변수와 다시 보내기(redirect)를 따르지 않는다.
- 오류에는 코드만 담는다. 응답 본문을 넣지 않는다(보낸 글이 되돌아올 수 있다).
"""

from __future__ import annotations

import http.client
import json
from dataclasses import dataclass

HOST = "127.0.0.1"
DEFAULT_PORT = 11434
_REMOTE_KEYS = ("remote_host", "remote_model")

# 오류 코드
NOT_RUNNING = "OLLAMA_NOT_RUNNING"
CLOUD_MODEL = "CLOUD_MODEL"
MODEL_NOT_INSTALLED = "MODEL_NOT_INSTALLED"
BAD_RESPONSE = "BAD_RESPONSE"
TIMEOUT = "OLLAMA_TIMEOUT"  # 켜져 있지만 제때 답하지 못함(느린 PC, 큰 모델)
PARTIAL = "OLLAMA_PARTIAL"  # 일부 묶음에 답하지 못함. 다시 찾아볼 수 있다


class OllamaError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def is_cloud_name(name: str) -> bool:
    n = name.strip().lower()
    tag = n.rpartition(":")[2] if ":" in n else ""
    return tag == "cloud" or tag.endswith("-cloud") or n.endswith("-cloud")


def is_cloud(info: dict) -> bool:
    """/api/tags의 모델 한 줄이나 /api/show 응답."""
    name = str(info.get("name") or info.get("model") or "")
    details = info.get("details") if isinstance(info.get("details"), dict) else {}
    return is_cloud_name(name) or any(info.get(k) or details.get(k) for k in _REMOTE_KEYS)


@dataclass(frozen=True)
class LocalModel:
    name: str
    size: int  # 바이트
    parameter_size: str
    family: str


class LocalOllama:
    def __init__(self, port: int = DEFAULT_PORT, timeout: float = 300.0):
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("포트는 1에서 65535 사이 정수입니다")
        self.port, self.timeout = port, timeout

    def _call(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        conn = http.client.HTTPConnection(HOST, self.port, timeout=timeout or self.timeout)
        try:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            status, data = resp.status, resp.read()
        except TimeoutError:
            raise OllamaError(TIMEOUT) from None
        except (OSError, http.client.HTTPException):
            raise OllamaError(NOT_RUNNING) from None
        finally:
            conn.close()
        if status == 404:
            raise OllamaError(MODEL_NOT_INSTALLED)
        if status != 200:
            raise OllamaError(f"HTTP_{status}")
        try:
            out = json.loads(data.decode("utf-8"))
        except ValueError:
            raise OllamaError(BAD_RESPONSE) from None
        if not isinstance(out, dict):
            raise OllamaError(BAD_RESPONSE)
        return out

    def version(self) -> str:
        return str(self._call("GET", "/api/version", timeout=5).get("version", ""))

    def models(self) -> list[LocalModel]:
        """내 컴퓨터에 받아 둔 모델. 클라우드 모델은 뺀다."""
        rows = self._call("GET", "/api/tags", timeout=10).get("models") or []
        out = []
        for m in rows:
            if not isinstance(m, dict) or is_cloud(m):
                continue
            d = m.get("details") if isinstance(m.get("details"), dict) else {}
            out.append(LocalModel(str(m.get("name") or m.get("model") or ""), int(m.get("size") or 0),
                                  str(d.get("parameter_size") or ""), str(d.get("family") or "")))
        return [m for m in out if m.name]

    def check_model(self, model: str) -> dict:
        """부르기 전에 확인한다: 클라우드 모델이 아니고, 내 컴퓨터에 받아 둔 모델이다. 모델 정보를 돌려준다."""
        if is_cloud_name(model):
            raise OllamaError(CLOUD_MODEL)
        if model not in {m.name for m in self.models()}:
            raise OllamaError(MODEL_NOT_INSTALLED)
        info = self._call("POST", "/api/show", {"model": model}, timeout=10)
        if is_cloud(info):
            raise OllamaError(CLOUD_MODEL)
        return info

    def chat_json(self, model: str, system: str, user: str, schema: dict, num_ctx: int = 8192) -> dict:
        """JSON 형식(schema)으로 답을 받는다. 부를 때마다 check_model로 다시 확인한다."""
        info = self.check_model(model)
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": num_ctx},
            "keep_alive": "5m",
        }
        if "thinking" in (info.get("capabilities") or []):
            # 생각 단계를 켜면 qwen3:4b가 CPU에서 묶음 하나에 5분을 넘겼다. 골라내기에는 필요 없다
            body["think"] = False
        out = self._call("POST", "/api/chat", body)
        content = (out.get("message") or {}).get("content") if isinstance(out.get("message"), dict) else None
        if not isinstance(content, str):
            raise OllamaError(BAD_RESPONSE)
        try:
            parsed = json.loads(content)
        except ValueError:
            raise OllamaError(BAD_RESPONSE) from None
        if not isinstance(parsed, dict):
            raise OllamaError(BAD_RESPONSE)
        return parsed
