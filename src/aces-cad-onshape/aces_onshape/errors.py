"""The adapter's errors.

Every error derives from AdapterError and carries `exit_code` (what the aces-onshape CLI exits with) and `kind` (what
the aces-onshape-mcp server reports in an isError result), so both front ends map a failure the same way. `str(e)` is
always a complete sentence that names the call where there is one. It never contains a request header, the base64
credential, the access key or the secret, and a URL that is not under the base URL (a redirect target) is shown
without its query or fragment, because pre-signed URLs carry their signatures there.
"""
from __future__ import annotations


class AdapterError(Exception):
    """Base of every adapter error."""

    exit_code = 1
    kind = "error"

    def to_dict(self) -> dict:
        """The error as the MCP server's structuredContent shows it."""
        out = {"kind": self.kind, "message": str(self), "exit_code": self.exit_code}
        status = getattr(self, "status", None)
        if status is not None:
            out["status"] = status
        if isinstance(self, TransportError):
            out["maybe_applied"] = self.maybe_applied
        return out


class OnshapeError(AdapterError):
    """Other 4xx and unexpected responses: a body that is not JSON, an id in a response that fails validation."""

    exit_code = 1
    kind = "onshape"

    def __init__(self, message: str, *, method: str | None = None, path: str | None = None,
                 status: int | None = None):
        super().__init__(message)
        self.method = method
        self.path = path
        self.status = status


class AuthError(OnshapeError):
    """No, partial or malformed credentials; a bad mode or owner on the credentials file; HTTP 401 or 403."""

    exit_code = 3
    kind = "auth"


class NotFoundError(OnshapeError):
    """HTTP 404."""

    kind = "not_found"


class RateLimitError(OnshapeError):
    """HTTP 429 after the retries, or a Retry-After above the cap."""

    kind = "rate_limited"


class ServerError(OnshapeError):
    """HTTP 5xx after the retries, or any 5xx on a call that is not idempotent."""

    kind = "server"


class TransportError(OnshapeError):
    """DNS, connect, TLS, timeouts and resets. `maybe_applied` is True when a write may have reached Onshape."""

    kind = "transport"

    def __init__(self, message: str, *, maybe_applied: bool = False, method: str | None = None,
                 path: str | None = None, status: int | None = None):
        super().__init__(message, method=method, path=path, status=status)
        self.maybe_applied = bool(maybe_applied)


class TranslationError(OnshapeError):
    """A translation FAILED, the wait timed out, or it finished DONE without result ids."""

    kind = "translation"


class UsageError(AdapterError, ValueError):
    """Bad ids, refs, labels, names, JSON or parameter values; a request that cannot be built."""

    exit_code = 2
    kind = "usage"


class ConfigError(UsageError):
    """A bad ONSHAPE_BASE_URL or ACES_ONSHAPE_ACES_CMD."""

    kind = "config"


class ProjectError(UsageError):
    """No project, a bound-project mismatch, an unreadable or ambiguous aces.yaml."""

    kind = "project"


class WriteRefused(UsageError):
    """A write to a target this process did not create without confirm_foreign, or into a public document without
    allow_public."""

    kind = "write_refused"


class ClassRefused(AdapterError):
    """The class gate: Onshape is refused for sovereign and controlled projects, and for unknown classes."""

    exit_code = 4
    kind = "class_refused"
