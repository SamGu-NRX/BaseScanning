# Integration viewer

A local page for filming a capture as it moves from the phone into the capture API. The phone is on the left and the server's stages are in the middle. The result, with the server's point-cloud preview, is on the right. Files move along a line between phone and server as the server acknowledges them.

The viewer has two modes, and the page labels them at all times.

- **Illustrative replay** plays self-authored synthetic events in the browser, under a yellow tape band. It touches no network, and it proves nothing about the backend.
- **Live capture** follows one capture id on one API, read-only. It shows only what that API reported: acknowledged files, stage events, failures, the result body and the preview file. A server that stops answering shows as reconnecting, stale or offline, and the viewer keeps whatever it had already received.

Needs Node 24 or later. There are no dependencies to install.

## Run it

```sh
cd tools/integration-viewer
CAPTURE_API_URL=https://<capture-api-host>/v1 node server.js --synthetic
```

The deployed API's address is kept out of this public repository. The server team's integration notes have it.

Then open `http://127.0.0.1:4317/`. The page starts in replay. For the live mode, pick a source and enter a capture id, or put both in the URL:

```text
http://127.0.0.1:4317/?mode=live&source=api&capture=cap_...
http://127.0.0.1:4317/?mode=live&source=synthetic&capture=cap_SYNTH_COMPLETE_1
```

| Option | Effect |
| --- | --- |
| `--port N` | Viewer port, default 4317. It listens on 127.0.0.1 only. |
| `--api URL` | The deployed capture API, as source `api`. `CAPTURE_API_URL` does the same. It must be https. |
| `--synthetic` | Also starts the synthetic API on a loopback port and offers it as source `synthetic`. |
| `--local-api URL` | Adds a loopback API as source `local`, such as the iOS kit's fake API at `http://127.0.0.1:8765/v1`. |
| `--quiet` | Stops the one-line request log. |

The synthetic API picks its scenario from the capture id and starts that capture's clock on the first request. A new suffix starts a fresh run.

| Capture id | What happens |
| --- | --- |
| `cap_SYNTH_COMPLETE_<any>` | Uploads, eleven stages, an eligible result and a preview cloud, in about 30 seconds |
| `cap_SYNTH_FAILED_<any>` | Uploads, then a `failed` event during validation |
| `cap_SYNTH_DROPOUT_<any>` | The complete run, with dropped connections from 11 s to 19 s |

## What the live view does and does not claim

- A file mark moves when a `files_committed` event arrives. The viewer does not see the phone, so a mark shows receipt, not transfer time or progress. Files acknowledged before the viewer connected update the count without animating.
- Stage rows change only on `stage` events. A stage the server never closed shows "no end reported" once the capture has settled. When no finished stage reports a usable duration (each is missing or under 0.05 s), the viewer says the stage timing cannot be interpreted. Timing does not tell whether real processing ran, and the viewer does not guess.
- The outcome and message come from `GET /result`, verbatim. A `verdict_ready` event alone only triggers that read.
- The model appears only when the result has a `previewUrl` and the file parses as a PLY point cloud. It is labeled as the final model. A zero-point file reads as empty, and any other file reads as preview unavailable. The viewer never draws a model of its own in live mode.
- The identity strip shows the source host and the version, storage and state that the API's health check reports. Check it before filming, because the deployed API's image changes often.
- Event types the viewer does not know go into the log and a notice. They change nothing else.

## The relay

The deployed API sends no CORS headers, so a page cannot call it directly. `relay.js` answers on 127.0.0.1 and relays only these reads:

```text
GET /relay/<source>/healthz
GET /relay/<source>/captures/<id>
GET /relay/<source>/captures/<id>/events?after=<n>&wait=<0-25>
GET /relay/<source>/captures/<id>/result
GET /relay/<source>/captures/<id>/preview?run=<run id>
```

Sources are fixed at startup. The relay refuses other methods and paths, and any other query. It also refuses encoded or dot segments and any Host header that is not loopback, which stops DNS rebinding. Signed URLs in the result are replaced before the page sees them. For `/preview`, the relay reads the result itself and fetches the signed URL only if it points at the source's own host or at `storage.googleapis.com`. It answers 409 if the latest result belongs to a different run than the page asked about. It follows no redirects, reads at most 64 MiB of preview and 8 MiB of JSON, and passes the API's `Retry-After` through. The log records each route and status, never a URL or query.

## Files

| Path | Role |
| --- | --- |
| `server.js` | Command line: starts the relay and, optionally, the synthetic API |
| `relay.js` | Static files and the read-only relay |
| `synthetic-api.js` | Loopback stand-in for the read routes, playing `public/scenario.js` |
| `public/model.js` | Reducer for all viewer state. Every action carries a session number, and actions from an earlier capture selection are dropped. |
| `public/live.js` | Long-poll loop: catch-up reads until history is drained, then long polls that end by the next 5 s status refresh, with a 1 s floor, backoff up to 5 s that honors `Retry-After`, result and preview reads when events call for them |
| `public/replay.js`, `public/scenario.js` | Illustrative replay and the synthetic scenarios both sources share |
| `public/ply.js`, `public/cloud.js` | PLY reader (ascii and binary) and the canvas renderer |
| `public/conduit.js`, `public/count-timers.js`, `public/app.js` | The file line, the timers that update the counts, and the page wiring. Counts update on timers, never on an animation finishing, and a probe turns motion and transitions off when the browser's animation clock is stopped. |

## Tests

```sh
cd tools/integration-viewer
node --test "test/*.test.js"
```

The tests cover the reducer (duplicates, ordering, cursor, stale sessions, unknown events, offline, no claimed result), the PLY reader, the relay through real HTTP requests (allowlist, traversal, Host check, signed-URL handling), and the live loop against the relay and a sped-up synthetic API, including a dropped connection. CI does not run them yet, because no workflow covers `tools/`.

`test/browser.test.js` runs the page in headless Chrome with the animation clock stopped and frames drawn only on request, which is how an embedded preview panel behaved. It checks that the file counts match the events drawn in the same frame and that finished stages show their marker. It skips when no Chrome is found; set `CHROME_PATH` to point at one.

Everything here is synthetic. Do not add real captures, photos or meter numbers.
