"""Errors raised by the pose stage. The message is the code."""

from __future__ import annotations


class PosesError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
