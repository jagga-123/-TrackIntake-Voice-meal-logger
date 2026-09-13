"""Django settings for the TrackIntake voice meal ingestion backend.

Configuration is environment-driven: every secret and deployment-specific value
is read from environment variables, which may be supplied through a ``.env``
file placed next to ``manage.py`` (see ``.env.example``).
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable; ``1/true/yes/on`` are truthy."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str) -> list[str]:
    """Read a comma-separated environment variable as a list of stripped strings."""
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_origins(name: str, default: str) -> list[str]:
    """Read a comma-separated list of origins, dropping any trailing slash.

    An Origin never carries a path, and ``django-cors-headers`` refuses to start
    when one is configured with a trailing slash. Copying a deployment URL out of
    a browser address bar includes that slash, so normalise it here rather than
    failing the deploy.
    """
    return [origin.rstrip("/") for origin in env_list(name, default)]


def resolve_secret_key(debug: bool) -> str:
    """Return the Django secret key, refusing to start without one outside DEBUG.

    In DEBUG a random per-process key is generated so developers never have to
    commit a secret. The trade-off is that sessions and JWTs reset on restart;
    set ``DJANGO_SECRET_KEY`` in ``.env`` to keep them stable.
    """
    secret = os.getenv("DJANGO_SECRET_KEY")
    if secret:
        return secret
    if debug:
        return get_random_secret_key()
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")


DEBUG = env_bool("DJANGO_DEBUG", default=True)
SECRET_KEY = resolve_secret_key(DEBUG)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# Render injects the service's public hostname at runtime. Trusting it here means
# a deploy works without anyone hand-copying the domain into an env var.
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# Django rejects cross-origin POSTs (such as the admin login form) over HTTPS
# unless the origin is listed here.
CSRF_TRUSTED_ORIGINS = env_origins("CSRF_TRUSTED_ORIGINS", "")
if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

# TLS terminates at Render's edge, so the original scheme arrives in a header.
# Without this Django believes every request is plain HTTP.
if os.getenv("RENDER"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "meal_voice",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- REST framework ----------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "meal_voice.exceptions.api_exception_handler",
    # Emit Decimal fields as JSON numbers so quantities round-trip cleanly.
    "COERCE_DECIMAL_TO_STRING": False,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

CORS_ALLOWED_ORIGINS = env_origins(
    "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)

# --- Voice meal pipeline -----------------------------------------------------

VOICE_MEAL = {
    "WHISPER_MODEL": os.getenv("WHISPER_MODEL", "base"),
    "WHISPER_DEVICE": os.getenv("WHISPER_DEVICE", "cpu"),
    "WHISPER_COMPUTE_TYPE": os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
    "WHISPER_BEAM_SIZE": int(os.getenv("WHISPER_BEAM_SIZE", "5")),
    "WHISPER_CPU_THREADS": int(os.getenv("WHISPER_CPU_THREADS", "0")),
    "WHISPER_VAD_FILTER": env_bool("WHISPER_VAD_FILTER", default=True),
    # Load the model in a background thread once the HTTP server is already
    # accepting connections. Doing it before the server binds delays every
    # request on a cold instance, including sign-in.
    "WARM_MODELS_ON_STARTUP": env_bool("WARM_MODELS_ON_STARTUP", default=False),
    # "auto" picks the first provider below that has a key; or name one explicitly.
    "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "auto"),
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
    "GEMINI_MODEL": os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
    "OPENAI_MODEL": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    "LLM_TIMEOUT_SECONDS": float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
    "MAX_AUDIO_BYTES": int(os.getenv("MAX_AUDIO_BYTES", str(10 * 1024 * 1024))),
}

# --- Logging -----------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "concise": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "concise"},
    },
    "loggers": {
        "meal_voice": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
