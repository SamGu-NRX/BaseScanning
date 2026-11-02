"""Post each example scene to a placement server and print its decision.

    python3 server/examples/smoke.py URL [--key-file PATH]

The key file holds a line HOUSESCAN_API_KEY=<key> (server/.env.private.local for the private
deployment). Standard library only, so it runs without the server's environment. Exits 1 when a
request fails.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent


def read_key(path: Path) -> str:
    for line in path.read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "HOUSESCAN_API_KEY" and value.strip():
            return value.strip()
    raise SystemExit(f"{path}: no HOUSESCAN_API_KEY=<key> line")


# Plain http may carry the key only to this machine, where it never crosses a network.
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def check_keyed_url(url: str) -> None:
    """Refuse to send the key where someone on the network could read it."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in LOOPBACK):
        raise SystemExit(f"{url}: the key is only sent over https (or to localhost)")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib copies Authorization onto a followed redirect, even to another server; a keyed
    request reports the redirect instead of following it."""

    def redirect_request(self, *args: object) -> None:
        return None


# An explicit empty proxy table: with http_proxy set, a keyed request would otherwise go through
# the proxy, key included.
_KEYED = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def post(url: str, body: bytes, key: str | None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    opener = urllib.request.urlopen
    if key:
        check_keyed_url(url)
        headers["Authorization"] = f"Bearer {key}"
        opener = _KEYED.open
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        if key and 300 <= error.code < 400:
            where = error.headers.get("Location")
            return error.code, {
                "error": {"code": "redirect_refused", "message": f"not following to {where}"}
            }
        text = error.read().decode(errors="replace")
        try:
            return error.code, json.loads(text)
        except json.JSONDecodeError:
            # Not the placement server's error format: another server, or its host.
            return error.code, {"error": {"code": "not_json", "message": text[:120]}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "url", help="server root, for example https://house-scanning-server.vercel.app"
    )
    parser.add_argument("--key-file", type=Path, help="file with HOUSESCAN_API_KEY=<key>")
    args = parser.parse_args()
    key = read_key(args.key_file) if args.key_file else None
    if key:
        check_keyed_url(args.url)
    endpoint = args.url.rstrip("/") + "/v1/placements"
    failed = False
    for scene in sorted(EXAMPLES.glob("*.json")):
        status, body = post(endpoint, scene.read_bytes(), key)
        if status == 200:
            # Which rules answered, not the private policy's name.
            rules = "+".join(body["policy"]["sources"])
            print(f"{scene.stem:30} {body['decision']:14} rules {rules}: {body['summary']}")
        else:
            failed = True
            error = body.get("error", {})
            print(f"{scene.stem:30} HTTP {status}      {error.get('code')}: {error.get('message')}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
