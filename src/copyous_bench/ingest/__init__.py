"""Ingest raw benchmark artifacts into a SQLite database."""

from .service import IngestStats, ingest

__all__ = ["IngestStats", "ingest"]
