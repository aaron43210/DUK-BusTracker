"""
Service-local ORM facade.
The actual ORM models remain defined in the shared ORM module.
This facade provides a stable service-local import boundary.
"""
from libs.duk_common.models import (
    Base,
    User,
)

__all__ = ["Base", "User"]
