"""An API-created run must survive fresh worker state and storage clients."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from server.api.app import create_app
from server.api.modal_entry import capture_runtime_env
from server.common.backends.storage_gcs import GcsStorage
from server.common.backends.storage_local import LocalFsStorage
from server.common.config import load
from server.contracts.jobs import FileRecord
from server.orchestrator import services as service_module
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.modal_functions import run_stage_impl, use_shared_dev_backends
from server.orchestrator.services import Services, packet_sha256
from server.orchestrator.tests.conftest import build_services


class _Blob:
    def __init__(self, objects, name):
        self.objects = objects
        self.name = name

    def upload_from_string(self, data, *, content_type):
        self.objects[self.name] = bytes(data)

    def download_as_bytes(self):
        return self.objects[self.name]

    def exists(self):
        return self.name in self.objects


class _Bucket:
    def __init__(self, objects):
        self.objects = objects

    def blob(self, name):
        return _Blob(self.objects, name)


class _Client:
    def __init__(self, buckets):
        self.buckets = buckets

    def bucket(self, name):
        return _Bucket(self.buckets.setdefault(name, {}))

    def list_blobs(self, bucket, *, prefix):
        return [bucket.blob(key) for key in bucket.objects if key.startswith(prefix)]


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    """Replace only the provider boundaries; use real state/storage adapters."""
    states, buckets = {}, {}

    # A mounted volume is a temporary directory in the probe, never host /vol.
    def local_storage(root):
        return LocalFsStorage(tmp_path / "volume" if root == Path("/vol") else root)

    monkeypatch.setattr("server.common.config.LocalFsStorage", local_storage)
    if hasattr(service_module, "LocalFsStorage"):
        monkeypatch.setattr(service_module, "LocalFsStorage", local_storage)
    monkeypatch.setattr("modal.Dict.from_name", lambda name, **_: states.setdefault(name, {}))
    monkeypatch.setattr("server.common.backends.gcp_auth.credentials_for", lambda _: None)
    monkeypatch.setattr("server.common.backends.storage_gcs.gcs.Client", lambda **_: _Client(buckets))
    serial = itertools.count()

    def scratch(**_):
        path = tmp_path / f"scratch-{next(serial)}"
        path.mkdir()
        return str(path)

    monkeypatch.setattr(service_module, "tempfile", SimpleNamespace(mkdtemp=scratch))
    monkeypatch.setattr("server.orchestrator.coordinator.sync_volume", lambda _: None)
    monkeypatch.setattr("server.orchestrator.coordinator.current_call_id", lambda: "synthetic-call")
    return states, buckets


@pytest.mark.parametrize("worker_service", ["pipeline", "sweeper"])
@pytest.mark.parametrize("dict_name", ["hss-dev-state", "hss-synthetic-handoff"])
def test_api_run_packet_and_artifacts_reach_fresh_worker(cloud, monkeypatch, tmp_path, worker_service, dict_name):
    import server.modal_app as deployment

    monkeypatch.setattr(deployment, "ENV", "dev")
    with patch.dict(os.environ, {"HSS_ORCH_MODAL_REMOTE": "1", "STATE_DICT_NAME": dict_name}, clear=True):
        api_env = capture_runtime_env("dev", "synthetic-sha")
        worker_env = deployment.deploy_env(worker_service)

    # API and worker processes have only their own baked environment and fresh clients.
    # The local seed constructs synthetic records; it is not either side's backend.
    with patch.dict(os.environ, api_env, clear=True):
        app = create_app()
        deps = app.state.deps
        seed, uri, _ = build_services(tmp_path / "seed", "memory")
        raw = seed.storage.get_bytes(uri)
        deps.storage.put_bytes(uri, raw, "application/json")
        deps.state.put_capture(seed.state.get_capture("cap_test"))
        media = b"synthetic-media"
        key = "captures/cap_test/committed/k0.jpg"
        deps.storage.put_bytes(key, media, "image/jpeg")
        deps.state.upsert_file(
            FileRecord(
                capture_id="cap_test",
                path="keyframes/k0.jpg",
                role="keyframe",
                bytes=len(media),
                sha256=hashlib.sha256(media).hexdigest(),
                md5="",
                content_type="image/jpeg",
                state="committed",
                key=key,
            )
        )
        with patch.object(RunCoordinator, "drive", return_value="running"):
            run = deps.hooks.on_finalized("cap_test", packet_sha256(raw), uri)

    hydrated = []
    real_context = Services.stage_context

    def observe(self, *args):
        ctx = real_context(self, *args)
        hydrated.append(ctx)
        return ctx

    monkeypatch.setattr(Services, "stage_context", observe)
    with patch.dict(os.environ, {**worker_env, "MODAL_TASK_ID": "synthetic-worker"}, clear=True):
        result = run_stage_impl(run.run_id, "validate", 1)
        worker = service_module.current()
        assert worker.state is not deps.state
        assert worker.storage is not deps.storage
        assert worker.settings.STATE_DICT_NAME == dict_name
        assert isinstance(worker.storage, GcsStorage)
        assert worker.settings.STORAGE_BACKEND == "gcs"
        packet_dir = Path(hydrated[0].inputs["packet_dir"])
        assert (packet_dir / "packet.json").read_bytes() == raw
        assert (packet_dir / "keyframes/k0.jpg").read_bytes() == media

        # A third, fresh context reads the artifacts written by the first worker.
        stored = worker.state.get_run(run.run_id)
        rec = worker.state.get_stage(run.run_id, "poses")
        ctx = worker.stage_context(stored, rec, "synthetic-next")
        artifact = "packet/validation.json"
        published = deps.storage.get_bytes(result["artifacts"][artifact])
        assert json.loads(published)["ok"] is True
        # Poses does not declare the validation report. The bytes stay in storage.
        assert not (ctx.work_dir / artifact).exists()
        assert deps.storage.get_bytes(result["artifacts"][artifact]) == published
        assert deps.state.get_stage(run.run_id, "validate").status == "done"

    with patch.dict(os.environ, api_env, clear=True):
        restarted_api = create_app().state.deps
        assert restarted_api.state.get_run(run.run_id).artifacts[artifact] == result["artifacts"][artifact]
        assert restarted_api.storage.get_bytes(result["artifacts"][artifact]) == published


def test_explicit_gcs_backend_is_not_replaced_by_scratch_volume(cloud, tmp_path):
    env = {
        "SMP_ENVIRONMENT": "dev",
        "STORAGE_BACKEND": "gcs",
        "STATE_BACKEND": "modal_dict",
        "VOLUME_ROOT": str(tmp_path / "mounted-volume"),
    }
    with patch.dict(os.environ, env, clear=True):
        services = service_module.current()
    assert isinstance(services.storage, GcsStorage)


def test_explicit_gcs_survives_worker_setup():
    env = {"SMP_ENVIRONMENT": "dev", "MODAL_TASK_ID": "synthetic-worker", "STORAGE_BACKEND": "gcs"}
    with patch.dict(os.environ, env, clear=True):
        use_shared_dev_backends()
        assert load().STORAGE_BACKEND == "gcs"


@pytest.mark.parametrize("environment,remote", [("dev", False), ("dev", True), ("prod", False), ("prod", True)])
def test_api_state_shared_and_worker_storage_follows_remote_switch(environment, remote, monkeypatch):
    import server.modal_app as deployment

    monkeypatch.setattr(deployment, "ENV", environment)
    with patch.dict(os.environ, {"HSS_ORCH_MODAL_REMOTE": str(int(remote))}, clear=True):
        capture = capture_runtime_env(environment, "synthetic-sha")
        pipeline = deployment.deploy_env("pipeline")
        assert capture["STATE_BACKEND"] == "modal_dict"
        assert pipeline.get("STATE_BACKEND") == ("modal_dict" if remote else None)
        assert pipeline.get("STORAGE_BACKEND") == ("gcs" if remote else None)


def test_gpu_image_adds_gcs_client_to_existing_prebuild(monkeypatch):
    from server import modal_images

    packages = []

    class Image:
        def pip_install(self, *names, **_):
            packages.extend(names)
            return self

    image = Image()
    monkeypatch.setenv("HSS_GPU_IMAGE_ID", "im-synthetic")
    monkeypatch.setattr(modal_images.modal.Image, "from_id", lambda _: image)
    monkeypatch.setattr(modal_images, "with_stage_package_data", lambda base: base)
    assert modal_images.gpu_image() is image
    assert any(name.startswith("google-cloud-storage") for name in packages)
    assert any(name.startswith("google-auth") for name in packages)
