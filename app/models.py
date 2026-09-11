"""Alembic model import hub; application code imports bounded modules directly."""

from app.audit import models as audit_models  # noqa: F401
from app.branding import models as branding_models  # noqa: F401
from app.catalog import models as catalog_models  # noqa: F401
from app.core.database import Base
from app.identity import models as identity_models  # noqa: F401
from app.notifications import models as notification_models  # noqa: F401
from app.rostering import models as rostering_models  # noqa: F401
from app.system_settings import models as system_settings_models  # noqa: F401

__all__ = ["Base"]
