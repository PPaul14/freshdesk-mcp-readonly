"""Read-only HTTP client for the Freshdesk API v2.

Only GET requests exist in this module: `_request` has no method parameter.
Every failure is raised as a typed FreshdeskError (see errors.py); no raw
`requests` exception ever leaves this module.

Logging goes through the `logging` module only (never print): stdout is
reserved for the MCP protocol when the server runs over stdio.
"""

import logging
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from freshdesk_mcp.errors import (
    AuthError,
    ConfigError,
    FreshdeskError,
    InvalidRequestError,
    MalformedResponseError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    UpstreamError,
)

logger = logging.getLogger(__name__)

# src/freshdesk_mcp/client.py -> parents[2] is the folder holding pyproject.toml.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# (connect timeout, read timeout) in seconds.
TIMEOUT = (5, 20)

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 8.0
JITTER_MAX_SECONDS = 0.5

LOW_REMAINING_THRESHOLD = 5
LOW_REMAINING_PAUSE_SECONDS = 1.0

MAX_PER_PAGE = 100
MAX_PAGES = 5  # hard ceiling for get_pages; callers cannot raise it

SEARCH_PATH = "/search/tickets"
SEARCH_PAGE_SIZE = 30  # fixed by Freshdesk for search
SEARCH_MAX_PAGE = 10  # Freshdesk rejects search pages above 10
SEARCH_MAX_QUERY_LENGTH = 512


@dataclass(frozen=True)
class RateLimitInfo:
    """Rate-limit headers from the most recent response (None if absent)."""

    total: int | None
    remaining: int | None


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    has_more: bool


@dataclass(frozen=True)
class PagedResult:
    items: list[dict[str, Any]]
    truncated: bool  # True if more data existed beyond the page ceiling


@dataclass(frozen=True)
class SearchPage:
    total: int
    results: list[dict[str, Any]]
    has_more: bool


@dataclass(frozen=True)
class _ApiResponse:
    data: Any
    has_next: bool


def _normalise_domain(domain: str) -> str:
    domain = domain.strip()
    for prefix in ("https://", "http://"):
        if domain.lower().startswith(prefix):
            domain = domain[len(prefix) :]
    domain = domain.rstrip("/")
    if not domain or "/" in domain:
        raise ConfigError("FRESHDESK_DOMAIN must be a host like yourcompany.freshdesk.com.")
    return domain


def _int_header(headers: Any, name: str) -> int | None:
    try:
        # Freshdesk sends counts as decimals, e.g. "50.0"; int("50.0") would fail.
        return int(float(headers[name]))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _retry_after_seconds(headers: Any) -> float | None:
    """Parse Retry-After as seconds. HTTP also allows a date form; we treat that as absent."""
    try:
        return max(0.0, float(headers["Retry-After"]))
    except (KeyError, TypeError, ValueError):
        return None


def _error_details(response: requests.Response, path: str) -> dict[str, Any]:
    """Safe, small details for an error: status, path and Freshdesk's own error summary."""
    details: dict[str, Any] = {"status": response.status_code, "path": path}
    try:
        body = response.json()
    except ValueError:
        return details  # not JSON (e.g. an HTML error page): leave it out
    if isinstance(body, dict):
        if isinstance(body.get("description"), str):
            details["description"] = body["description"]
        if isinstance(body.get("errors"), list):
            details["errors"] = [
                {k: e.get(k) for k in ("field", "message", "code") if k in e}
                for e in body["errors"][:5]
                if isinstance(e, dict)
            ]
    return details


class FreshdeskClient:
    """Read-only Freshdesk API v2 client with retries, rate-limit awareness and pagination."""

    def __init__(
        self,
        domain: str,
        api_key: str,
        *,
        max_retries: int = 3,
        max_total_wait_seconds: float = 30.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ConfigError("FRESHDESK_API_KEY is missing or empty.")
        self.domain = _normalise_domain(domain)
        self.base_url = f"https://{self.domain}/api/v2"
        self.max_retries = max_retries
        self.max_total_wait_seconds = max_total_wait_seconds
        self._sleep = sleep_fn
        self._random = random_fn
        self.last_rate_limit: RateLimitInfo | None = None

        self._session = requests.Session()
        # Basic auth: API key as username, dummy password "X" (per Freshdesk docs).
        self._session.auth = (api_key.strip(), "X")
        self._session.headers["Accept"] = "application/json"

    @classmethod
    def from_env(cls, env_file: Path | None = None, **kwargs: Any) -> "FreshdeskClient":
        """Build a client from FRESHDESK_DOMAIN / FRESHDESK_API_KEY.

        Loads the project's .env if present. Variables already set in the real
        environment win over .env (python-dotenv's default), so a host such as
        Claude Desktop can pass them directly.
        """
        load_dotenv(env_file or PROJECT_ROOT / ".env")
        domain = os.getenv("FRESHDESK_DOMAIN", "").strip()
        api_key = os.getenv("FRESHDESK_API_KEY", "").strip()
        missing = [
            name
            for name, value in (("FRESHDESK_DOMAIN", domain), ("FRESHDESK_API_KEY", api_key))
            if not value
        ]
        if missing:
            raise ConfigError(f"Missing or empty environment variable(s): {', '.join(missing)}.")
        return cls(domain, api_key, **kwargs)

    def __repr__(self) -> str:
        return f"FreshdeskClient(domain={self.domain!r})"

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------ HTTP core

    def _request(self, path: str, params: dict[str, Any] | None = None) -> _ApiResponse:
        """GET `path` with retries. The only way this client talks to Freshdesk."""
        waited = 0.0
        attempt = 0
        while True:
            try:
                return self._send_once(path, params)
            except FreshdeskError as exc:
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                delay = self._retry_delay(exc, attempt)
                if waited + delay > self.max_total_wait_seconds:
                    # Waiting would blow the time budget: fail now without sleeping.
                    # A RateLimitedError already carries retry_after_seconds for the caller.
                    raise
                attempt += 1
                logger.warning(
                    "Freshdesk GET %s failed (%s); retry %d/%d in %.2fs",
                    path,
                    exc.code,
                    attempt,
                    self.max_retries,
                    delay,
                )
                self._sleep(delay)
                waited += delay

    def _retry_delay(self, exc: FreshdeskError, attempt: int) -> float:
        if isinstance(exc, RateLimitedError) and exc.retry_after_seconds is not None:
            return exc.retry_after_seconds  # server told us exactly how long: honour it
        backoff = min(BACKOFF_BASE_SECONDS * 2**attempt, BACKOFF_CAP_SECONDS)
        return backoff + self._random() * JITTER_MAX_SECONDS

    def _send_once(self, path: str, params: dict[str, Any] | None) -> _ApiResponse:
        self._pause_if_near_limit()
        try:
            response = self._session.get(f"{self.base_url}{path}", params=params, timeout=TIMEOUT)
        # `from None` drops the original exception: its text has the full URL and its
        # .request object holds the Authorization header, neither of which may leak.
        except requests.Timeout:
            raise NetworkError(f"Timed out calling Freshdesk GET {path}.") from None
        except requests.ConnectionError:
            raise NetworkError(f"Could not connect to Freshdesk for GET {path}.") from None
        except requests.RequestException as exc:
            raise NetworkError(
                f"Request to Freshdesk failed for GET {path} ({type(exc).__name__}).",
                retryable=False,
            ) from None

        self._record_rate_limit(response)
        logger.debug("Freshdesk GET %s -> %d", path, response.status_code)

        if response.ok:
            try:
                data = response.json()
            except ValueError:
                raise MalformedResponseError(
                    f"Freshdesk returned non-JSON for GET {path}.",
                    details={"status": response.status_code, "path": path},
                ) from None
            return _ApiResponse(data=data, has_next="next" in response.links)

        raise self._error_for(response, path)

    def _error_for(self, response: requests.Response, path: str) -> FreshdeskError:
        status = response.status_code
        details = _error_details(response, path)
        if status == 400:
            return InvalidRequestError(details=details)
        if status == 401:
            return AuthError(details=details)
        if status == 403:
            return PermissionDeniedError(details=details)
        if status == 404:
            return NotFoundError(details=details)
        if status == 429:
            return RateLimitedError(
                retry_after_seconds=_retry_after_seconds(response.headers), details=details
            )
        if status >= 500:
            return UpstreamError(
                f"Freshdesk returned a server error (HTTP {status}).",
                retryable=status in RETRYABLE_STATUSES,
                details=details,
            )
        return InvalidRequestError(
            f"Freshdesk rejected the request (HTTP {status}).", details=details
        )

    # ----------------------------------------------------------- rate limiting

    def _record_rate_limit(self, response: requests.Response) -> None:
        total = _int_header(response.headers, "X-RateLimit-Total")
        remaining = _int_header(response.headers, "X-RateLimit-Remaining")
        if total is not None or remaining is not None:
            self.last_rate_limit = RateLimitInfo(total=total, remaining=remaining)

    def _pause_if_near_limit(self) -> None:
        # Heuristic: Freshdesk tells us how many calls remain but not when the
        # per-minute window resets, so we can't compute an exact wait. A short
        # pause when we're nearly out spreads calls out instead of hitting 429.
        info = self.last_rate_limit
        if info and info.remaining is not None and info.remaining < LOW_REMAINING_THRESHOLD:
            logger.info("Freshdesk rate limit nearly used (%d left); pausing", info.remaining)
            self._sleep(LOW_REMAINING_PAUSE_SECONDS)

    # -------------------------------------------------------------- pagination

    def get_page(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page: int = 1,
        per_page: int = 30,
    ) -> Page:
        """Fetch one page of a list endpoint. has_more comes from the Link rel="next" header."""
        if page < 1:
            raise InvalidRequestError("page must be 1 or greater.")
        per_page = max(1, min(per_page, MAX_PER_PAGE))
        response = self._request(path, {**(params or {}), "page": page, "per_page": per_page})
        if not isinstance(response.data, list):
            raise MalformedResponseError(
                f"Expected a list from GET {path}.", details={"path": path}
            )
        return Page(items=response.data, has_more=response.has_next)

    def get_pages(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        per_page: int = MAX_PER_PAGE,
        max_pages: int = MAX_PAGES,
    ) -> PagedResult:
        """Fetch up to max_pages pages (never more than MAX_PAGES).

        Stops on: no next link, an empty page, or the page ceiling. `truncated`
        is True only when the ceiling stopped us and more data existed.
        """
        limit = max(1, min(max_pages, MAX_PAGES))
        items: list[dict[str, Any]] = []
        for page in range(1, limit + 1):
            result = self.get_page(path, params, page=page, per_page=per_page)
            items.extend(result.items)
            if not result.items or not result.has_more:
                return PagedResult(items=items, truncated=False)
        return PagedResult(items=items, truncated=True)

    def search_page(self, query: str, page: int = 1) -> SearchPage:
        """One page (30 results) of /search/tickets.

        `query` is the bare Freshdesk query expression, e.g. "status:2 AND priority:3";
        this method adds the surrounding double quotes Freshdesk requires.
        """
        if not query or not query.strip():
            raise InvalidRequestError("Search query must not be empty.")
        if not 1 <= page <= SEARCH_MAX_PAGE:
            raise InvalidRequestError(f"Search page must be between 1 and {SEARCH_MAX_PAGE}.")
        quoted = f'"{query.strip()}"'
        if len(quoted) > SEARCH_MAX_QUERY_LENGTH:
            raise InvalidRequestError(
                f"Search query is longer than {SEARCH_MAX_QUERY_LENGTH} characters."
            )
        response = self._request(SEARCH_PATH, {"query": quoted, "page": page})
        data = response.data
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("total"), int)
            or not isinstance(data.get("results"), list)
        ):
            raise MalformedResponseError(
                "Unexpected shape from ticket search.", details={"path": SEARCH_PATH}
            )
        total = data["total"]
        has_more = page < SEARCH_MAX_PAGE and page * SEARCH_PAGE_SIZE < total
        return SearchPage(total=total, results=data["results"], has_more=has_more)
