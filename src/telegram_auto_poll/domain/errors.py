"""Domain error hierarchy.

Expected failures are exceptions with a stable, user-safe string: handlers render them directly to
admins, so nothing here may contain a token, a raw update, or an SQLAlchemy object.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for expected, user-facing failures."""


class ValidationError(DomainError):
    """A user-supplied value violates a domain rule.

    ``field`` lets the wizard re-prompt the same step; ``str(error)`` is the message shown to the
    admin and includes the constraint and the offending length (FR-117).
    """

    def __init__(self, field: str, detail: str) -> None:
        super().__init__(detail)
        self.field = field
        self.detail = detail

    def __str__(self) -> str:
        return self.detail


class PermissionDeniedError(DomainError):
    """The bot lacks the Telegram permission required for the action."""


class NotFoundError(DomainError):
    """The requested entity does not exist."""


class ConflictError(DomainError):
    """The request conflicts with existing state."""


class DeliveryError(DomainError):
    """A send attempt failed, classified for the retry policy (FR-131)."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        retry_after: float | None = None,
        classification: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.classification = classification or message
