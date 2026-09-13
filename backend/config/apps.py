"""App configurations that put Django's bundled models on MongoDB.

Each contrib app pins ``default_auto_field`` in its own ``AppConfig``, which
takes precedence over the project-wide ``DEFAULT_AUTO_FIELD``. MongoDB has no
integer auto field, so these subclasses swap in ``ObjectIdAutoField`` and are
listed in ``INSTALLED_APPS`` in place of the stock apps.
"""

from __future__ import annotations

from django.contrib.admin.apps import AdminConfig
from django.contrib.auth.apps import AuthConfig
from django.contrib.contenttypes.apps import ContentTypesConfig

OBJECT_ID_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"


class MongoAdminConfig(AdminConfig):
    """``django.contrib.admin`` with ObjectId primary keys."""

    default_auto_field = OBJECT_ID_AUTO_FIELD


class MongoAuthConfig(AuthConfig):
    """``django.contrib.auth`` with ObjectId primary keys."""

    default_auto_field = OBJECT_ID_AUTO_FIELD


class MongoContentTypesConfig(ContentTypesConfig):
    """``django.contrib.contenttypes`` with ObjectId primary keys."""

    default_auto_field = OBJECT_ID_AUTO_FIELD
