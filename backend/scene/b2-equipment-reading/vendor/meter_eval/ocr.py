"""Stream read requests through one long-lived meterocr process."""

import json
import subprocess
from pathlib import Path
from typing import Self

from meter_eval.paths import EXPERIMENT_DIR

BINARY = EXPERIMENT_DIR / "meterocr" / ".build" / "release" / "meterocr"

# The three configurations Q1 compares. The app would use one; the others show what the
# choice costs.
CONFIGS = {
    "accurate": {"level": "accurate", "language_correction": False},
    "accurate_lc": {"level": "accurate", "language_correction": True},
    "fast": {"level": "fast", "language_correction": False},
}
PRIMARY = "accurate"


class Reader:
    def __init__(self) -> None:
        if not BINARY.exists():
            raise FileNotFoundError(
                f"{BINARY} is missing; build it with `swift build -c release --package-path meterocr`"
            )
        self.process = subprocess.Popen(
            [str(BINARY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
        )

    def read(
        self,
        path: Path,
        config: str = PRIMARY,
        crop: list[float] | None = None,
        barcodes: bool = False,
    ) -> dict:
        request = {"path": str(path), **CONFIGS[config], "barcodes": barcodes}
        if crop is not None:
            request["crop"] = crop
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"meterocr exited with {self.process.wait()} while reading {path}")
        result = json.loads(line)
        if result.get("error"):
            raise RuntimeError(f"meterocr failed on {path}: {result['error']}")
        result["config"] = config
        return result

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=30)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
