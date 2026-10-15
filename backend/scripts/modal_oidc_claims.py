"""Print Modal OIDC claims used by the WIF condition. Never prints the token."""

from __future__ import annotations

import base64
import json
import os
import sys


def claims() -> dict:
    token = os.environ.get("MODAL_IDENTITY_TOKEN", "")
    if not token or token.count(".") < 2:
        raise SystemExit("MODAL_IDENTITY_TOKEN is not set")
    payload = token.split(".")[1]
    pad = "=" * (-len(payload) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(payload + pad))
    return {
        "workspace_id": decoded.get("workspace_id"),
        "environment_name": decoded.get("environment_name"),
        "exp": decoded.get("exp"),
    }


def main() -> int:
    print(json.dumps(claims()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
