"""Resolve a packet folder to one manifest file (SD-44)."""

from __future__ import annotations

from pathlib import Path

DEFAULT_VARIANT = "arkit-nonlidar"


class PacketLayoutError(ValueError):
    """A packet folder breaks the one-manifest rule."""


def resolve_packet_manifest(
    folder: Path,
    variant: str | None = None,
    *,
    variant_explicit: bool = False,
) -> Path:
    """`packet.json` when present, otherwise `packet.<variant>.json`.

    A folder never holds both. Passing a variant for a `packet.json` folder is an error.
    The default variant is `arkit-nonlidar` and applies only when the caller did not
    name one and the folder has no `packet.json`.
    """

    folder = Path(folder)
    plain = folder / "packet.json"
    variants = sorted(path for path in folder.glob("packet.*.json") if path.is_file())
    if plain.is_file() and variants:
        names = ", ".join(path.name for path in variants)
        raise PacketLayoutError(f"{folder} holds both packet.json and {names}")
    if plain.is_file():
        if variant_explicit:
            raise PacketLayoutError(f"--variant on variant-less folder {folder}")
        return plain
    chosen = variant if variant is not None else DEFAULT_VARIANT
    path = folder / f"packet.{chosen}.json"
    if not path.is_file():
        raise PacketLayoutError(f"missing {path}")
    return path
