from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class SourceHTTPError(RuntimeError):
    pass


TRUSTED_SOURCE_HOSTS = frozenset(
    {
        "loveracing.nz",
        "events.loveracing.nz",
        "infohorse.hrnz.co.nz",
        "www.hrnz.co.nz",
    }
)
MAX_SOURCE_REDIRECTS = 3


DestinationValidator = Callable[[str], bool]


def _safe_source_destination(
    url: str,
    trusted_hosts: frozenset[str],
    trusted_host_suffixes: frozenset[str],
) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    return bool(
        parsed.scheme.casefold() == "https"
        and (
            host in trusted_hosts
            or any(host.endswith(f".{suffix}") for suffix in trusted_host_suffixes)
        )
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.fragment
        and not any(ord(character) < 32 or ord(character) == 127 for character in url)
    )


class _ValidatedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, validator: DestinationValidator, max_redirects: int):
        super().__init__()
        self.validator = validator
        self.max_redirects = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not self.validator(newurl):
            raise SourceHTTPError("Source redirect was outside the trusted destination boundary.")
        count = int(getattr(req, "_ontrack_redirect_count", 0))
        if count >= self.max_redirects:
            raise SourceHTTPError("Source exceeded the redirect limit.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        safe_headers = {
            key: value
            for key, value in redirected.header_items()
            if key.casefold() in {"accept", "user-agent", "content-type"}
        }
        clean = Request(
            redirected.full_url,
            data=redirected.data,
            headers=safe_headers,
            method=redirected.get_method(),
        )
        clean._ontrack_redirect_count = count + 1  # type: ignore[attr-defined]
        return clean


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

    def __init__(
        self,
        *,
        timeout: float,
        max_bytes: int,
        trusted_hosts: frozenset[str] = TRUSTED_SOURCE_HOSTS,
        trusted_host_suffixes: frozenset[str] = frozenset({"loveracing.nz", "hrnz.co.nz"}),
        max_redirects: int = MAX_SOURCE_REDIRECTS,
    ):
        # urllib applies this bound to connection establishment and blocking reads.
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.trusted_hosts = frozenset(host.casefold().rstrip(".") for host in trusted_hosts)
        self.trusted_host_suffixes = frozenset(
            host.casefold().rstrip(".") for host in trusted_host_suffixes
        )
        self.max_redirects = max_redirects

    def get(
        self,
        url: str,
        *,
        accept: str,
        destination_validator: DestinationValidator | None = None,
    ) -> HTTPResponse:
        return self._open(
            Request(
                url,
                headers={
                    "Accept": accept,
                    "User-Agent": "OnTrackRostering/0.3 (+calendar planning evidence)",
                },
            ),
            destination_validator=destination_validator,
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

    def _open(
        self,
        request: Request,
        *,
        destination_validator: DestinationValidator | None = None,
    ) -> HTTPResponse:
        def validator(url: str) -> bool:
            return _safe_source_destination(
                url, self.trusted_hosts, self.trusted_host_suffixes
            ) and (
                destination_validator(url) if destination_validator else True
            )

        if not validator(request.full_url):
            raise SourceHTTPError("Source URL was outside the trusted destination boundary.")
        try:
            opener = build_opener(_ValidatedRedirectHandler(validator, self.max_redirects))
            with opener.open(request, timeout=self.timeout) as response:
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise SourceHTTPError("Source response exceeded the configured size limit.")
                return HTTPResponse(
                    url=response.geturl(),
                    status=response.status,
                    content_type=response.headers.get_content_type(),
                    body=body,
                )
        except SourceHTTPError:
            raise
        except HTTPError as exc:
            raise SourceHTTPError(f"Source returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SourceHTTPError(f"Source request failed: {type(exc).__name__}.") from exc
