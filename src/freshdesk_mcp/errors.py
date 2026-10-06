"""Typed errors raised by the Freshdesk client.

Each error carries a stable machine-readable `code`, a human-readable `message`,
a `retryable` flag and optional `details`. Messages and details must never
contain the API key, auth headers, or URLs with credentials.

"Not found" from a lookup and "no results" from a search are NOT failures for
the agent; the tools layer turns NotFoundError / empty lists into normal results.
"""

from typing import Any, ClassVar


class FreshdeskError(Exception):
    """Base class for every error the connector raises on purpose.

    Subclasses set CODE, RETRYABLE and DEFAULT_MESSAGE; callers usually pass
    only `message` (optional) and `details`.
    """

    CODE: ClassVar[str] = "freshdesk_error"
    RETRYABLE: ClassVar[bool] = False
    DEFAULT_MESSAGE: ClassVar[str] = "The Freshdesk request failed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code or self.CODE
        self.message = message or self.DEFAULT_MESSAGE
        self.retryable = self.RETRYABLE if retryable is None else retryable
        self.details = details
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details or {},
        }


class ConfigError(FreshdeskError):
    CODE = "config_error"
    DEFAULT_MESSAGE = "The Freshdesk connector is not configured correctly."


class AuthError(FreshdeskError):
    """HTTP 401."""

    CODE = "auth_failed"
    DEFAULT_MESSAGE = (
        "Freshdesk rejected the credentials (HTTP 401). Check the API key, and that API key "
        "access is enabled for this agent in Freshdesk Admin > Agents."
    )


class PermissionDeniedError(FreshdeskError):
    """HTTP 403."""

    CODE = "permission_denied"
    DEFAULT_MESSAGE = "The API key is valid but not allowed to access this resource (HTTP 403)."


class NotFoundError(FreshdeskError):
    """HTTP 404."""

    CODE = "not_found"
    DEFAULT_MESSAGE = "The requested Freshdesk resource does not exist (HTTP 404)."


class InvalidRequestError(FreshdeskError):
    """HTTP 400 (and other 4xx we don't map more specifically), or bad input caught locally."""

    CODE = "invalid_request"
    DEFAULT_MESSAGE = "Freshdesk rejected the request as invalid (HTTP 400)."


class RateLimitedError(FreshdeskError):
    """HTTP 429 that we could not (or should not) wait out."""

    CODE = "rate_limited"
    RETRYABLE = True
    DEFAULT_MESSAGE = "Freshdesk rate limit reached (HTTP 429). Try again later."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        merged = dict(details or {})
        if retry_after_seconds is not None:
            merged["retry_after_seconds"] = retry_after_seconds
        super().__init__(message, details=merged or None)


class UpstreamError(FreshdeskError):
    """HTTP 5xx: Freshdesk itself failed."""

    CODE = "upstream_error"
    RETRYABLE = True
    DEFAULT_MESSAGE = "Freshdesk returned a server error (HTTP 5xx)."


class NetworkError(FreshdeskError):
    """The request never got a response: timeout or connection failure."""

    CODE = "network_error"
    RETRYABLE = True
    DEFAULT_MESSAGE = "Could not reach Freshdesk (timeout or connection failure)."


class MalformedResponseError(FreshdeskError):
    """Freshdesk answered 2xx but the body was not the JSON shape we expect."""

    CODE = "malformed_response"
    DEFAULT_MESSAGE = "Freshdesk returned a response that could not be understood."
