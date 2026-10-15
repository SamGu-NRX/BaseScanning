"""Errors raised by the dense, coverage and scene stages."""

from __future__ import annotations


class SceneError(RuntimeError):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)
