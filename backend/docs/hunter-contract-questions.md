# Swift `Codable` parity — verification decisions and open contract questions

From the decoder-contract work (`server/api/tests/test_capture_device_decoder_contract.py`,
branch `obv/burst11-bs-swift-contract-parity`). The test file carries a Python mirror of
Swift `Codable` decoding for exactly the structs the app declares, and runs every served
body — upload walk, events (including an empty long-poll timeout), result for each
reachable status, review/preview refusals, and every error body the walk produces —
through it. 26 tests, all green; `make schemas` reports no drift; `lint-imports` 5 kept /
0 broken. This file records the decisions behind the test file and the questions that
stay open for the contract owner.

## Verified this pass

- **Served shapes already match Swift.** No key-name or shape changes were needed on the
  upload side (PR #257 pinned those) or on the result envelope. The only server change is
  error-shape enforcement for framework-level answers (below).
- **`Int` is strict on the wire.** Swift's `Int` decode fails on a fractional literal
  (`1.0`), a bool, or a string. The only client-decoded ints are `seq`/`next` (events),
  `Limits`, and `etaS` — all server-generated integers. `Double` fields accept any finite
  JSON number; the AR `Mat4`/`SIMD3<Float>` fields decode as number arrays.
- **Unknown enum strings decode as `unknown(...)`, not as errors.** `status`,
  `outcome.kind`, criterion `outcome`, event `type`, and error `code` are decoded as
  plain strings by the app. A new server enum value degrades to generic handling; it does
  not fail the decode. New event `type` values with `data: null` also decode.
- **Unknown keys are ignored; `null` vs absent matters.** A `null` in a non-optional Swift
  field fails the whole body (`keyNotFound`-class). The result route's overlay keeps
  `outcome` absent (never `null`) for statuses without one, matching
  `statusesWithoutOutcome` = {failed, expired} and the `outcome: T?` decode.
- **Event `data` is the fragile field.** Stored as `dict[str, Any]`; Swift pins six
  `String?` keys (`code`, `kind`, `stage`, `status`, `runId`, `next`). Any non-string
  value under one of those keys fails the entire `EventsResponse` decode — the device
  would end the scan `answerUnreadable`. Every event on the verified walk carries only
  strings or nulls there, including the typed emitters (`StageData`, `VerdictReadyData`,
  `RetakeRequestData`) and the sweeper's `capture_expired`.

## Server change made

`server/api/app.py` now enforces INTEGRATION.md's "Errors are ApiError" for answers the
route handlers never produce:

- route miss → 404 `not_found`
- method mismatch → 405 `method_not_allowed`
- unhandled exception → 500 `internal` (Starlette still logs the traceback)

Before this, those three answers were plain text / plain `{"detail": ...}` bodies, and
the app's `CaptureAPI.errorCodes` would read nothing from them.

## Open questions for the contract owner

1. **Should event `data` values be validated server-side?** The six Swift-pinned keys are
   only strings-or-null by convention today — nothing in `append_event` enforces it. A
   future emitter that logs a numeric `next` or a dict `stage` would break every device
   poll at runtime, far from the code that caused it. A cheap guard on the emitter (or a
   schema on `data`) would make the test file's guarantee structural rather than
   observed.
2. **Terminal-without-outcome combinations.** The result route serves 200 for any capture
   status; `complete` or `manual_review` with no run/blob decodes fine (outcome is
   Optional) but shows the app nothing. Is that the intended "stuck terminal" rendering,
   or should those combinations 409 so the app surfaces a retry? Left servable; Swift
   tolerates it.
3. **Client copy for the new framework codes.** `not_found`, `method_not_allowed`, and
   `internal` land in Swift's `.unknown(code)` and show generic error copy. If a 500
   during a scan deserves specific homeowner guidance (retry vs. abandon), the app needs
   a case for `internal` — that is an app-side change, not a server one.
4. **Finalize checks `packetId` against the capture after the missing-files short-circuit**
   (`server/api/finalize.py`: the awaiting-files renders precede the 409
   `packet_id_mismatch` guard). A finalize whose `packetId` disagrees with the capture can
   therefore return 202 `awaiting_files` while nothing is committed, and 409 only after
   files land. Probably harmless — the device never sends a mismatched packet — but the
   order is worth a look if packet identity is ever load-bearing.
5. **Finalize on an `expired` capture was not probed.** The expired path is covered
   through the result/events reads (the sweeper's `capture_expired` event and the
   outcome-less final read) rather than a finalize-while-expired call, whose guard order
   was not verified in source this pass.
