"""SD-44: packet.json first, else packet.<variant>.json, never both."""

from pathlib import Path

import pytest

from server.contracts.packet_resolve import PacketLayoutError, resolve_packet_manifest


def test_variant_less_folder(tmp_path: Path) -> None:
    manifest = tmp_path / "packet.json"
    manifest.write_text("{}")
    assert resolve_packet_manifest(tmp_path) == manifest
    with pytest.raises(PacketLayoutError, match="variant-less"):
        resolve_packet_manifest(tmp_path, "arkit-lidar", variant_explicit=True)


def test_variant_default_and_explicit(tmp_path: Path) -> None:
    chosen = tmp_path / "packet.arkit-nonlidar.json"
    other = tmp_path / "packet.arkit-lidar.json"
    chosen.write_text("{}")
    other.write_text("{}")
    assert resolve_packet_manifest(tmp_path) == chosen
    assert resolve_packet_manifest(tmp_path, "arkit-lidar", variant_explicit=True) == other


def test_folder_cannot_hold_both(tmp_path: Path) -> None:
    (tmp_path / "packet.json").write_text("{}")
    (tmp_path / "packet.arkit-nonlidar.json").write_text("{}")
    with pytest.raises(PacketLayoutError, match="holds both"):
        resolve_packet_manifest(tmp_path)
