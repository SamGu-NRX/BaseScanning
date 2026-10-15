"""CPU stays available when CUDA is not. No weights are loaded."""

from __future__ import annotations

from server.objects.device import gate_batch, owl_batch, resolve_device, use_half


def test_cpu_request_never_selects_cuda() -> None:
    assert resolve_device("cpu") == "cpu"
    assert use_half("cpu") is False
    assert gate_batch("cpu") == 1
    assert owl_batch("cpu") == 1


def test_cuda_request_falls_back_when_torch_has_no_gpu(monkeypatch) -> None:
    monkeypatch.setenv("HSS_OBJECTS_DEVICE", "cuda")
    assert use_half("cuda") is True
    assert gate_batch("cuda") == 8
    assert owl_batch("cuda") == 4
    torch = __import__("torch") if _has_torch() else None
    if torch is None or not torch.cuda.is_available():
        assert resolve_device("cuda") == "cpu"
        assert resolve_device("auto") == "cpu"
        return
    assert resolve_device("auto") == "cuda"


def test_import_error_is_cpu(monkeypatch) -> None:
    import builtins

    real = builtins.__import__

    def guarded(name: str, *args: object, **kwargs: object):
        if name == "torch":
            raise ImportError("no torch")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert resolve_device("auto") == "cpu"
    assert resolve_device("cuda") == "cpu"


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True
