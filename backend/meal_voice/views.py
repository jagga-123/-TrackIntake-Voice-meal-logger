"""HTTP endpoints for voice meal ingestion.

Failures raised by the pipeline are domain exceptions; the project-wide
exception handler (``meal_voice.exceptions``) turns them into 400/422/502
responses, so the views stay focused on the happy path.
"""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from meal_voice.models import MealLog
from meal_voice.serializers import (
    ConfirmMealSerializer,
    MealLogSerializer,
    VoiceAudioUploadSerializer,
)
from meal_voice.services.pipeline import get_pipeline


class HealthView(APIView):
    """Unauthenticated liveness probe used by the frontend and deploy checks."""

    permission_classes = (AllowAny,)

    def get(self, request: Request) -> Response:
        """Report that the service is up."""
        return Response({"status": "ok"})


class VoiceMealPreviewView(APIView):
    """``POST /api/v1/meal-log/voice/preview/`` — audio in, editable preview out.

    Nothing is persisted here; the client must call the confirm endpoint with
    the (possibly edited) preview to save it.
    """

    parser_classes = (MultiPartParser, FormParser)

    def post(self, request: Request) -> Response:
        """Validate the upload and run the voice pipeline for the current user."""
        serializer = VoiceAudioUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        audio_bytes = serializer.validated_data["audio"].read()
        preview = get_pipeline().run(audio_bytes, request.user)
        return Response(preview)


class VoiceMealConfirmView(APIView):
    """``POST /api/v1/meal-log/voice/confirm/`` — persist an edited preview."""

    def post(self, request: Request) -> Response:
        """Save the meal log and items, returning the stored representation."""
        serializer = ConfirmMealSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        meal_log = serializer.save()
        return Response(MealLogSerializer(meal_log).data, status=status.HTTP_201_CREATED)


class MealLogPagination(PageNumberPagination):
    """Page-number pagination with a client-adjustable, capped page size."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class MealLogListView(ListAPIView):
    """``GET /api/v1/meal-log/`` — the requesting user's meal logs, newest first."""

    serializer_class = MealLogSerializer
    pagination_class = MealLogPagination

    def get_queryset(self) -> QuerySet[MealLog]:
        """Scope the listing to the authenticated user."""
        return MealLog.objects.filter(user=self.request.user).prefetch_related("items")
