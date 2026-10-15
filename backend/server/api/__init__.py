"""Capture ingest API. Importing this package does not import Modal."""

from server.api.app import create_app

__all__ = ["create_app"]
