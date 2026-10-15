"""Rewrite the 22 make/model answers into WS-08-shaped reads stubs.

The source answers have no digit runs. m28's answer named a polycarbonate cover;
§8 item 5 requires that replayed stub to carry cover_material unknown so the
glass/poly mixture is 0.5/0.5. The other 21 stubs keep the answer's material.
"""

from __future__ import annotations

import json
from pathlib import Path

ANSWERS = [
    ("m01", "Tatung", "Tatung D4S", "glass", 0.95),
    ("m03", "Tatung Co.", "D4S", "glass", 0.9),
    ("m09", "Chung-Hsin Electric & Machinery", "Chung-Hsin I-70-S", "glass", 0.96),
    ("m11", "Chung-Hsin Electric (中興電工)", "GE kV2c", "polycarbonate", 0.95),
    ("m13", "Ta Tung Co.", "Ta Tung D4S", "glass", 0.95),
    ("m15", "Chung-Hsin Electric (中興電工)", "kV2c", "polycarbonate", 0.98),
    ("m22", "Aclara", "Aclara I-210+", "polycarbonate", 0.99),
    ("m23", "Aclara", "Aclara kV2c", "polycarbonate", 0.99),
    ("m27", "Chung-Hsin Electric & Machinery (中興電工)", "I-70-S", "glass", 0.95),
    ("m28", "Itron", "Itron CENTRON", "polycarbonate", 0.92),
    ("m29", "Landis+Gyr", "Landis+Gyr FOCUS RXR-SD", "polycarbonate", 0.98),
    ("m31", "General Electric", "General Electric I-60-S", "glass", 0.98),
    ("m32", "Elster", "Elster AB1", "glass", 0.93),
    ("m34", "Elster", "Elster REX R1S", "polycarbonate", 0.86),
    ("m35", "Elster", "Elster A3 Alpha", "unknown", 0.98),
    ("m37", "Sangamo", "Sangamo K2A", "glass", 0.98),
    ("m39", "Itron", "Itron OpenWay CENTRON", "polycarbonate", 0.97),
    ("m44", "Sensus", "Sensus iConA", "polycarbonate", 0.88),
    ("m45", "Sensus", "Sensus iConA", "polycarbonate", 0.95),
    ("m50", "TATUNG", "Tatung TT-001", "polycarbonate", 0.95),
    ("m51", "TATUNG", "Tatung TT-001", "polycarbonate", 0.95),
    ("m57", "Westinghouse", "Westinghouse D5S", "glass", 0.97),
]


def _record(manufacturer, model_family, cover, confidence, *, agree=True, images=None) -> dict:
    return {
        "status": "ok",
        "meter_identity": {
            "manufacturer": manufacturer,
            "model_family": model_family,
            "model_evidence": "printed",
            "model_confidence": confidence,
            "cover_material": cover,
            "ansi_socket_meter": True,
            "base_flange_visible": True,
            "socket_type": "ring",
            "agree": agree,
            "images": ["s1"] if images is None else images,
        },
        "meter": {"form": "2S"},
    }


def write_all(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for photo_id, manufacturer, family, cover, confidence in ANSWERS:
        material = "unknown" if photo_id == "m28" else cover
        path = directory / f"{photo_id}.reads.json"
        path.write_text(json.dumps(_record(manufacturer, family, material, confidence), indent=2) + "\n")
    (directory / "disabled.reads.json").write_text(json.dumps({"status": "disabled"}) + "\n")
    (directory / "no_identity.reads.json").write_text(json.dumps({"status": "ok", "meter": {"form": None}}) + "\n")
    (directory / "low_confidence.reads.json").write_text(
        json.dumps(_record("Itron", "Itron CENTRON", "unknown", 0.4), indent=2) + "\n"
    )
    (directory / "agree_false_printed.reads.json").write_text(
        json.dumps(_record("Itron", "Itron CENTRON", "polycarbonate", 0.9, agree=False), indent=2) + "\n"
    )
    (directory / "on_device_only.packet.json").write_text(
        json.dumps(
            {
                "scaleReference": {
                    "meterCloseUp": {"captured": True},
                    "meterIdentity": {"source": "on_device_text", "make": "Itron", "model": "CENTRON"},
                }
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    write_all(Path(__file__).resolve().parent / "reads")
