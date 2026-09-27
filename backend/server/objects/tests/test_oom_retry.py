"""A CUDA OOM must drop the failed batch before the one-image retry starts."""

from __future__ import annotations

import gc
import weakref

import numpy as np
import pytest

from server.objects import gate, proposals


class _Hold:
    """Supports weak references. A bare object() does not."""


def _oom_once(images: list, sink: list[list[weakref.ref]], torch) -> None:
    batch = [_Hold() for _ in images]
    sink.append([weakref.ref(item) for item in batch])
    if len(images) > 1:
        raise torch.cuda.OutOfMemoryError("simulated")


def test_encode_images_drops_the_failed_batch_before_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    torch = pytest.importorskip("torch")
    held: list[list[weakref.ref]] = []
    live_at_release: list[int] = []

    def once(bundle: dict, images: list):
        _oom_once(images, held, torch)
        return np.ones((1, 2), np.float32)

    def release() -> None:
        gc.collect()
        live_at_release.append(sum(ref() is not None for ref in held[-1]))

    monkeypatch.setattr(gate, "_encode_images_once", once)
    monkeypatch.setattr(gate, "release_cuda", release)
    out = gate._encode_images({"device": "cuda"}, [1, 2, 3, 4])
    assert live_at_release == [0]
    assert out.shape == (4, 2)
    assert len(held) == 5


def test_owl_forward_drops_the_failed_batch_before_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    torch = pytest.importorskip("torch")
    held: list[list[weakref.ref]] = []
    live_at_release: list[int] = []

    def once(model, proc, images, texts, device, half, store_thr):
        _oom_once(images, held, torch)
        return [{"ok": True}]

    def release() -> None:
        gc.collect()
        live_at_release.append(sum(ref() is not None for ref in held[-1]))

    monkeypatch.setattr(proposals, "_owl_forward_once", once)
    monkeypatch.setattr(proposals, "release_cuda", release)
    rows = proposals._owl_forward(None, None, [1, 2, 3, 4], ["q"], "cuda", True, 0.08)
    assert live_at_release == [0]
    assert rows == [{"ok": True}] * 4
    assert len(held) == 5


def test_single_image_oom_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    torch = pytest.importorskip("torch")

    def once(*args, **kwargs):
        raise torch.cuda.OutOfMemoryError("simulated")

    def release() -> None:
        raise AssertionError("release_cuda ran")

    monkeypatch.setattr(gate, "_encode_images_once", once)
    monkeypatch.setattr(gate, "release_cuda", release)
    with pytest.raises(torch.cuda.OutOfMemoryError):
        gate._encode_images({"device": "cuda"}, [1])

    monkeypatch.setattr(proposals, "_owl_forward_once", once)
    monkeypatch.setattr(proposals, "release_cuda", release)
    with pytest.raises(torch.cuda.OutOfMemoryError):
        proposals._owl_forward(None, None, [1], ["q"], "cuda", True, 0.08)
