"""Reusable deterministic scorers and a small educational evaluation pipeline."""

from .models import Trajectory
from .scorers import argument_mismatches, tool_correctness

__version__ = "0.2.0"
__all__ = ["Trajectory", "argument_mismatches", "tool_correctness"]
