"""Workflow-level errors shared by API adapters and services."""


class QuotaExceededError(RuntimeError):
    """Raised before a paid provider task is submitted past its local limit."""
