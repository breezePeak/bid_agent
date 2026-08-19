"""Bounded embedded research control graph.

This module deliberately exposes no runner or server.  The graph is the internal
control loop implemented by :class:`DeepResearchEngine` and can only be reached
through the existing ResearchService provider contract.
"""

from .engine import DeepResearchEngine

__all__ = ["DeepResearchEngine"]
