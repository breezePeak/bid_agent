"""V3 document-production domain contracts and services.

This package is deliberately versionless: V3 artifact paths isolate persisted
data while the domain language remains stable for later iterations.
"""

from .contracts import V3_SCHEMA_VERSION

__all__ = ["V3_SCHEMA_VERSION"]
