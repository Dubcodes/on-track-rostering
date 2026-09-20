from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SourceHTTPError(RuntimeError):
    pass


@dataclass(frozen=True)
class HTTPResponse:
    url: str
    status: int
    content_type: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class SourceHTTPClient:
    """Small public-source HTTP boundary; provider URLs remain code-owned constants."""

    def __init__(self, *, timeout: float, max_bytes: int):
        # urllib applies this bound to connection establishment and blocking reads.
        self.timeout = timeout
        self.max_bytes = max_bytes

    def get(self, url: str, *, accept: str) -> HTTPResponse:
        return self._open(
            Request(
                url,
                headers={
                    "Accept": accept,
                    "User-Agent": "OnTrackRostering/0.3 (+calendar planning evidence)",
                },
            )
        )

    def post_json(self, url: str, value: dict[str, object]) -> HTTPResponse:
        return self._open(
            Request(
                url,
                data=json.dumps(value, separators=(",", ":")).encode(),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "OnTrackRostering/0.3 (+calendar planning evidence)",
                },
                method="POST",
            )
        )

    def _open(self, request: Request) -> HTTPResponse:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise SourceHTTPError("Source response exceeded the configured size limit.")
                return HTTPResponse(
                    url=response.geturl(),
                    status=response.status,
                    content_type=response.headers.get_content_type(),
                    body=body,
                )
        except HTTPError as exc:
            raise SourceHTTPError(f"Source returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SourceHTTPError(f"Source request failed: {type(exc).__name__}.") from exc
