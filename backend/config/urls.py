"""Root URL configuration.

All application endpoints are versioned under ``/api/v1/``. Authentication uses
JSON Web Tokens issued by ``djangorestframework-simplejwt``.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from meal_voice.views import HealthView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", HealthView.as_view(), name="health"),
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/meal-log/", include("meal_voice.urls")),
]
