"""Domain exceptions and the DRF exception handler that maps them to HTTP.

Every failure surfaces to clients as a uniform envelope::

    {"error": {"code": "<machine_readable>", "message": "<human_readable>", "details": {...}}}

``details`` is present only for request-validation failures.
"""

from __future__ import annotations

import logging
from typing import Any

from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class VoiceMealError(Exception):
    """Base class for pipeline failures that have a well-defined HTTP mapping."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "voice_meal_error"
    default_message: str = "The voice meal pipeline failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class TranscriptionError(VoiceMealError):
    """Speech-to-text could not run (model unavailable, decode failure)."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "transcription_failed"
    default_message = "Speech-to-text is currently unavailable."


class LLMUnavailableError(VoiceMealError):
    """The LLM provider is unreachable, misconfigured or returned an API error."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "llm_unavailable"
    default_message = "The meal parsing service is currently unavailable."


class MealParseError(VoiceMealError):
    """The audio was understood, but no valid meal could be extracted from it."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "meal_parse_failed"
    default_message = "Could not extract a meal from the recording."


def _code_for(exc: APIException) -> str:
    """Return a stable machine-readable code for a DRF exception."""
    if isinstance(exc, ValidationError):
        return "validation_error"
    return str(getattr(exc, "default_code", "error"))


def _message_for(exc: APIException) -> str:
    """Return a single human-readable sentence for a DRF exception."""
    if isinstance(exc, ValidationError):
        return "Request validation failed."
    detail = exc.detail
    return str(detail) if isinstance(detail, str) else str(exc.default_detail)


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Convert domain and DRF exceptions into the shared error envelope.

    Returning ``None`` lets Django's default 500 handling take over for
    unexpected exceptions, so genuine bugs are never masked as client errors.
    """
    if isinstance(exc, VoiceMealError):
        # Upstream failures (502) keep their chained traceback in the log so the
        # provider's own status/message is visible; user-input failures do not.
        upstream_failure = exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR
        logger.warning("%s: %s", exc.code, exc, exc_info=upstream_failure)
        return Response(
            {"error": {"code": exc.code, "message": str(exc)}}, status=exc.status_code
        )

    response = drf_exception_handler(exc, context)
    if response is None or not isinstance(exc, APIException):
        return response

    payload: dict[str, Any] = {"code": _code_for(exc), "message": _message_for(exc)}
    if isinstance(exc, ValidationError):
        payload["details"] = response.data
    response.data = {"error": payload}
    return response
