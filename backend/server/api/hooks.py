"""Bindings onto server.orchestrator. Null while those functions still raise."""

from __future__ import annotations

from dataclasses import replace

from server.common.config import Settings
from server.common.logging import log_event
from server.contracts.jobs import CommittedFile


class NullHooks:
    def on_capture_created(self, capture_id: str, *, tier: str, expected_keyframes: int | None) -> None:
        return None

    def on_files_committed(self, capture_id: str, files: list[CommittedFile]) -> None:
        return None

    def on_finalized(self, capture_id: str, packet_sha256: str, packet_uri: str, *, parent_run_id: str | None = None):
        return None

    def on_confirmation(self, capture_id: str, confirmation, confirmation_id: str, *, by: str) -> None:
        return None

    def cancel_capture(self, capture_id: str) -> None:
        return None


class OrchestratorHooks:
    def __init__(self, settings: Settings | None = None, state=None, storage=None) -> None:
        self.settings = settings
        self.state = state
        self.storage = storage

    def _bind(self, fn):
        """Run `fn` with the API state and storage installed as orchestrator services."""
        try:
            import server.orchestrator.services as services
        except ImportError:
            return fn()
        bind = getattr(services, "bind", None)
        current = getattr(services, "current", None)
        if bind is None or current is None:
            return fn()
        try:
            template = current()
            bound = replace(template, state=self.state, storage=self.storage)
        except Exception:
            log_event("orchestrator.bind_failed", level="error")
            return fn()
        with bind(bound):
            return fn()

    def on_capture_created(self, capture_id: str, *, tier: str, expected_keyframes: int | None) -> None:
        import server.orchestrator as orchestrator

        self._bind(lambda: orchestrator.prewarm(capture_id, tier=tier, expected_keyframes=expected_keyframes))

    def on_files_committed(self, capture_id: str, files: list[CommittedFile]) -> None:
        import server.orchestrator as orchestrator

        self._bind(lambda: orchestrator.dispatch_early(capture_id, files))

    def on_finalized(self, capture_id: str, packet_sha256: str, packet_uri: str, *, parent_run_id: str | None = None):
        import server.orchestrator as orchestrator

        return self._bind(
            lambda: orchestrator.start_run(capture_id, packet_sha256, packet_uri, parent_run_id=parent_run_id)
        )

    def on_confirmation(self, capture_id: str, confirmation, confirmation_id: str, *, by: str) -> None:
        import server.orchestrator as orchestrator

        self._bind(lambda: orchestrator.on_confirmation(capture_id, confirmation, confirmation_id, by=by))

    def cancel_capture(self, capture_id: str) -> None:
        import server.orchestrator as orchestrator

        self._bind(lambda: orchestrator.cancel_capture(capture_id))


def resolve_hooks(settings: Settings, state=None, storage=None):
    if settings.HSS_INGEST_HOOKS == "null":
        return NullHooks()
    import server.orchestrator as orchestrator

    if getattr(orchestrator, "STUB", False):
        return NullHooks()
    return OrchestratorHooks(settings, state, storage)


def safe_hook(name: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log_event("hook.error", hook=name, error_code=type(exc).__name__, level="error")
        return None
