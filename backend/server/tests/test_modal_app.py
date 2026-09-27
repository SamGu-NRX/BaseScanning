from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

CUDA_DIGEST = "sha256:5645fec64549cc35930eee9d85aafd2b0006c0c3f22632be5a1d85e2604e9749"


def _load(monkeypatch: pytest.MonkeyPatch, env: str):
    monkeypatch.setenv("SMP_ENVIRONMENT", env)
    import server.modal_app as mod

    return importlib.reload(mod)


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_secret_names(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    mod = _load(monkeypatch, env)
    assert mod.APP_NAME == "house-scanning"
    assert mod.secret_names("pipeline") == [f"hss-{env}-gcp"]
    assert mod.secret_names("capture-api") == [f"hss-{env}-gcp", f"hss-{env}-api"]
    assert mod.KNOBS["pipeline_gpu"] == "L4"
    assert mod.KNOBS["pipeline_min_containers"] == 0
    assert mod.KNOBS["pipeline_max_containers"] == 3
    # SD-73: these three names are canonical. WS-03 reads them.
    assert "gpu" not in mod.KNOBS
    assert "min_containers" not in mod.KNOBS
    assert "max_containers" not in mod.KNOBS
    assert mod.VOL_NAME == "house-capture-pipeline"
    assert mod.GPU_PRICES_USD_PER_S["L4"] == 0.000222


def test_dev_deploy_env_enables_hooks_and_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc123")
    monkeypatch.delenv("HSS_ORCH_MODAL_REMOTE", raising=False)
    monkeypatch.delenv("HSS_ORCH_STAGE_STUBS", raising=False)
    mod = _load(monkeypatch, "dev")
    for service in ("capture-api", "pipeline", "early-worker", "sweeper"):
        env = mod.deploy_env(service)
        assert env["HSS_INGEST_HOOKS"] == "auto"
        assert env["HSS_ORCH_STAGE_STUBS"] == "1"
        assert env["SMP_ENVIRONMENT"] == "dev"
        assert env["SMP_SERVICE_NAME"] == service
    capture = mod.deploy_env("capture-api")
    assert capture["STORAGE_BACKEND"] == "gcs"
    assert capture["STATE_BACKEND"] == "modal_dict"
    assert capture["STATE_DICT_NAME"] == "hss-dev-state"
    assert capture["HSS_AUTH_MODE"] == "none"
    pipeline = mod.deploy_env("pipeline")
    assert pipeline["HSS_DENSE_WEIGHTS"] == "/vol/hf"
    assert pipeline["HOUSE_WEIGHTS"] == "/vol/hf"
    assert pipeline["HF_HOME"] == "/vol/hf"
    assert pipeline["HUGGINGFACE_HUB_CACHE"] == "/vol/hf/hub"
    assert mod.deploy_env("sweeper")["HF_HOME"] == "/vol/hf"
    assert "HSS_AUTH_MODE" not in mod.deploy_env("pipeline")
    assert "STORAGE_BACKEND" not in mod.deploy_env("pipeline")
    assert "STATE_BACKEND" not in mod.deploy_env("pipeline")
    from server.api.modal_entry import capture_runtime_env, capture_secret_names

    assert capture_runtime_env("dev", "abc123") == capture
    assert capture_secret_names("dev") == mod.secret_names("capture-api")


def test_real_run_switch_is_off_unless_the_deploy_shell_sets_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc123")
    monkeypatch.delenv("HSS_ORCH_MODAL_REMOTE", raising=False)
    mod = _load(monkeypatch, "dev")
    capture = mod.deploy_env("capture-api")
    assert "HSS_ORCH_MODAL_REMOTE" not in capture
    from server.api.modal_entry import capture_runtime_env, web_function_timeout_s

    assert capture_runtime_env("dev", "abc123") == capture
    assert web_function_timeout_s() == 600
    monkeypatch.setenv("HSS_ORCH_MODAL_REMOTE", "1")
    monkeypatch.delenv("HSS_ORCH_STAGE_STUBS", raising=False)
    enabled = mod.deploy_env("capture-api")
    assert enabled["HSS_ORCH_MODAL_REMOTE"] == "1"
    assert enabled["HSS_ORCH_STAGE_STUBS"] == "1"
    assert capture_runtime_env("dev", "abc123") == enabled
    assert web_function_timeout_s() == 1560
    entry = Path("server/api/modal_entry.py").read_text()
    assert "web_function_timeout_s()" in entry
    assert "max_containers=_max_containers" in entry
    assert "CAPTURE_API_MAX_CONTAINERS = 4" in entry
    assert "CAPTURE_API_TARGET_INPUTS = 1" in entry
    assert "buffer_containers=CAPTURE_API_BUFFER_CONTAINERS" in entry
    assert "@modal.concurrent" in entry
    monkeypatch.setenv("HSS_ORCH_STAGE_STUBS", "0")
    for service in ("capture-api", "pipeline", "sweeper"):
        baked = mod.deploy_env(service)
        assert baked["HSS_ORCH_STAGE_STUBS"] == "0"
        assert baked["STATE_BACKEND"] == "modal_dict"
        assert baked["STORAGE_BACKEND"] == "gcs"
        assert baked["STATE_DICT_NAME"] == "hss-dev-state"
        if service == "capture-api":
            assert baked["HSS_ORCH_MODAL_REMOTE"] == "1"
        else:
            assert "HSS_ORCH_MODAL_REMOTE" not in baked
    early = mod.deploy_env("early-worker")
    assert early["HSS_ORCH_STAGE_STUBS"] == "0"
    assert "HSS_ORCH_MODAL_REMOTE" not in early
    assert "STATE_BACKEND" not in early
    assert "STORAGE_BACKEND" not in early
    assert capture_runtime_env("dev", "abc123") == mod.deploy_env("capture-api")
    monkeypatch.setenv("HSS_ORCH_STAGE_STUBS", " 0 ")
    assert mod.deploy_env("pipeline")["HSS_ORCH_STAGE_STUBS"] == "0"
    monkeypatch.delenv("HSS_ORCH_MODAL_REMOTE")
    held = mod.deploy_env("capture-api")
    assert held["HSS_ORCH_STAGE_STUBS"] == "1"
    assert "HSS_ORCH_MODAL_REMOTE" not in held
    assert capture_runtime_env("dev", "abc123") == held


def test_every_service_bakes_the_deployed_app_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline, sweeper, and the coordinator must not fall back to house-scanning."""

    from server.api.modal_entry import capture_runtime_env
    from server.common.deploy_switch import modal_app_name

    monkeypatch.setenv("GIT_SHA", "abc123")
    monkeypatch.setenv("MODAL_APP_NAME", "hss-probe")
    monkeypatch.delenv("HSS_ORCH_MODAL_REMOTE", raising=False)
    mod = _load(monkeypatch, "dev")
    baked = {service: mod.deploy_env(service) for service in ("capture-api", "pipeline", "early-worker", "sweeper")}
    assert capture_runtime_env("dev", "abc123") == baked["capture-api"]
    monkeypatch.delenv("MODAL_APP_NAME")
    importlib.reload(mod)
    for service, env in baked.items():
        assert env["MODAL_APP_NAME"] == "hss-probe", service
        assert modal_app_name(env) == "hss-probe"
    assert modal_app_name() == "house-scanning"
    assert mod.deploy_env("pipeline")["MODAL_APP_NAME"] == "house-scanning"
    assert mod.deploy_env("sweeper")["MODAL_APP_NAME"] == "house-scanning"
    assert mod.APP_NAME == "house-scanning"


def test_prod_deploy_env_does_not_force_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load(monkeypatch, "prod")
    env = mod.deploy_env("capture-api")
    assert "HSS_INGEST_HOOKS" not in env
    assert "HSS_ORCH_STAGE_STUBS" not in env
    assert "HSS_AUTH_MODE" not in env


def test_api_and_pipeline_absent_until_upstream_merges() -> None:
    import server.modal_app as mod

    try:
        importlib.import_module("server.api.modal_entry")
    except ImportError:
        assert mod.api_app is None
    else:
        assert mod.api_app is not None
    try:
        importlib.import_module("server.orchestrator.modal_functions")
    except ImportError:
        assert "server.orchestrator.modal_functions" not in sys.modules
    else:
        assert "server.orchestrator.modal_functions" in sys.modules


def test_capture_web_function_uses_the_cpu_image() -> None:
    entry = Path("server/api/modal_entry.py").read_text()
    images = Path("server/modal_images.py").read_text()
    assert "CPU_IMAGE" in entry
    assert "from_dockerfile" not in entry
    assert "GPU_IMAGE" not in entry
    assert "HSS_GPU_IMAGE_ID" in images
    assert "from_id" in images
    capture = entry.split("def capture_api", 1)[0].rsplit("@api_app.function", 1)[1]
    assert "api_image" in capture
    assert "from_dockerfile" not in capture


def test_publish_without_gpu_image_id_uses_the_cpu_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HSS_GPU_IMAGE_ID", raising=False)
    mod = _load(monkeypatch, "dev")
    assert mod.image is mod.CPU_IMAGE
    assert mod.GPU_IMAGE is mod.CPU_IMAGE
    text = Path("server/modal_app.py").read_text()
    assert "image = gpu_image()" not in text
    assert "HSS_GPU_IMAGE_ID" in text


def test_image_pin_uses_the_gpu_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HSS_GPU_IMAGE_ID", raising=False)
    mod = _load(monkeypatch, "dev")

    text = Path("server/modal_app.py").read_text()
    assert mod.GPU_IMAGE is mod.image
    pin = text.split("def image_pin", 1)[0].rsplit("@app.function", 1)[1]
    assert "GPU_IMAGE" in pin
    assert "gpu=" not in pin
    assert 'secrets=secrets_for("pipeline")' in pin
    smoke = text.split("def smoke_cpu", 1)[0].rsplit("@app.function", 1)[1]
    assert 'secrets=secrets_for("pipeline")' in smoke


def test_stage_images_include_non_python_package_data() -> None:
    from server.modal_images import stage_package_ignore

    root = Path("server")
    kept = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not stage_package_ignore(path.relative_to(root))
    }
    assert "result/review/template.html" in kept
    assert "result/review/style.css" in kept
    assert "criteria/profiles.yaml" in kept
    assert "contracts/schemas/result.v0.schema.json" in kept
    assert "packet/schemas/normalized-packet.v0.schema.json" in kept
    assert "poses/schemas/registration-report.v0.schema.json" in kept
    assert "scale/schemas/scale-ext.v0.schema.json" in kept
    assert "result/review/render.py" in kept
    assert "result/tests/data/prompts.en-US.json" not in kept
    assert "criteria/tests/data/reads.sample.json" not in kept
    assert "reads/README.md" not in kept
    assert Path("data/meter-models/meter_models.v1.json").is_file()
    images = Path("server/modal_images.py").read_text()
    assert images.count("with_stage_package_data(") >= 3


def test_colmap_probe_does_not_spawn_a_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("server.modal_app.shutil.which", lambda _name: None)

    def boom(*_args, **_kwargs):
        raise AssertionError("colmap was executed")

    monkeypatch.setattr("server.modal_app.subprocess.run", boom)
    from server.modal_app import colmap_probe

    assert colmap_probe() == (None, "")


def test_dockerfile_pins_colmap_matching_pycolmap() -> None:
    text = Path("Dockerfile").read_text()
    assert "--branch 4.2.0" in text
    assert "--branch 2.2.0" in text
    assert "--branch 3.11.1" not in text
    assert "libopenimageio-dev" in text
    assert "libopenexr-dev" in text
    assert "cmake-3.31.8-linux-x86_64.tar.gz" in text
    assert "archive/9fa3ddb3f8d53041f8b2738df404f62223bbaa7b.tar.gz" in text
    assert "codeload.github.com" not in text
    assert "libceres-dev" not in text
    assert CUDA_DIGEST in text
    assert "pycolmap==4.2.0" in Path("pyproject.toml").read_text()
    assert "pycolmap-cuda12==4.2.0" in text
    assert "pycolmap.has_cuda" in text


def test_colmap_cuda_smoke_requests_an_l4() -> None:
    text = Path("server/modal_app.py").read_text()
    body = text.split("def colmap_cuda", 1)[1]
    deco = text.split("def colmap_cuda", 1)[0].rsplit("@app.function", 1)[1]
    assert 'gpu="L4"' in deco
    assert "GPU_IMAGE" in deco
    assert "pycolmap.has_cuda" in body
    assert "torch.cuda.is_available" in body
    assert "HSS_POSES_COLMAP_BIN" in body
