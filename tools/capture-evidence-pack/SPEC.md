# Capture evidence pack format

A capture evidence pack is a zip archive that accompanies a bug report about one capture and one result. It preserves which capture and which result the report describes, and it carries nothing else. The pack references the capture inputs; it never embeds them. Everything here is synthetic data from this repository's fixtures.

This document is the reference for the format. Version 1.0 is the only version.

## Pack layout

A pack is a zip archive with exactly two entries:

- `manifest.json`: the identity evidence described below.
- `report.md`: the bug report text the pack accompanies.

`manifest.json` hashes every other entry under `packaged_files`. A manifest cannot contain its own hash, so manifest integrity is pinned by the receipt the packer prints and the reader repeats. Anyone who relays a pack records that receipt.

## manifest.json

The manifest is one JSON object. Every field below is required; a manifest missing any of them is invalid, and both the packer and the reader refuse it. Unknown top-level fields are invalid.

- `evidence_pack_format_version`: string, `1.0`.
- `capture_schema_version`: non-empty string naming the capture schema the described inputs follow, for example `synthetic-capture/2026.10`.
- `capture`: object identifying the capture the report is about.
- `result`: object identifying the result the report is about.
- `missing_fields`: list of absent capture fields, possibly empty.
- `reproduction`: object carrying the exact command a reader runs.
- `geometry_correctness`: object whose `assertion` must be JSON `null`.
- `packaged_files`: non-empty list of file records covering every zip entry except `manifest.json`.

### capture

- `capture_id`: non-empty string.
- `captured_at`: UTC timestamp in `YYYY-MM-DDTHH:MM:SSZ` form, strictly zero-padded, and a real calendar date. `2026-1-5T00:00:00Z` (not zero-padded) and `2026-02-30T00:00:00Z` (no such day) are invalid.
- `capture_root`: repository-relative path to the directory source refs resolve against. No `..`, no leading slash.
- `source_refs`: non-empty list of source references.

### result

- `result_id`: non-empty string.
- `path`: relative POSIX path under `capture_root`. No `..`, no leading slash.
- `sha256`: lowercase 64-digit hexadecimal digest of the file's bytes.
- `bytes`: file size in bytes, a positive integer.

### Source refs

Each entry in `capture.source_refs` names one capture input file the report is about:

- `role`: non-empty string, for example `frame`.
- `path`: relative POSIX path under `capture_root`. No `..`, no leading slash.
- `sha256`: lowercase 64-digit hexadecimal digest of the file's bytes.
- `bytes`: file size in bytes, a positive integer.

Refs point at files; the pack does not contain them. A reader that wants the bytes resolves each path against `capture_root` (or an explicit override) and checks the hash and size. A capture input written as JSON that declares a `schema_version` field must declare the manifest's `capture_schema_version`; anything else is the `schema-drift` refusal. The example pack points into `tools/capture-evidence-pack/fixtures/example/`, which is committed synthetic data. A pack about a real capture would point into `captures/`, which git ignores.

### missing_fields

Real captures have gaps: a depth map the device never produced, an intrinsic that never arrived. A pack enumerates every such gap in `missing_fields` instead of letting absence stay silent. Each entry names the capture `field` and a non-empty `detail` explaining the gap; an entry with a blank or absent `detail` is invalid. An empty list means the described inputs were complete. Omitting the key is invalid, and so is an explicit `null`: a reader cannot tell a null from a forgotten one, so the packer writes the key either way and both sides refuse a null.

### Result identity

The manifest claims which result the report is about through `result_id`, `path`, `sha256`, and `bytes`. When the result file names itself — a JSON object carrying a `result_id` field — the packer and the reader compare the embedded name with the claim and refuse `mismatched-result` on a mismatch. When the file carries no self-identification, the tools state a narrower guarantee instead of implying more: the receipt says the exact bytes were verified and that the file does not itself say which result it is.

### reproduction

- `command`: non-empty string. The exact command a reader runs to re-derive the receipt from the pack and the referenced inputs.

The packer fills this field, the reader repeats it in its receipt, and the inspection page shows it so a reader can paste it.

### geometry_correctness

- `assertion`: must be JSON `null` in a conforming pack.
- `note`: non-empty string explaining why the slot is empty.

## Schema versions

Two versions ride in every manifest, and both are explicit:

- `capture_schema_version` describes the capture inputs the report is about.
- `evidence_pack_format_version` describes the pack itself.

Neither is inferred, defaulted, or optional. A reader that meets an unknown pack format version refuses the pack instead of guessing.

## Identity is not correctness

This format records identity only: which capture, which result, which bytes, which versions, which gaps. It makes no claim that any geometry is correct, plausible, or verified.

The only slot where such a claim could live is `geometry_correctness`, and a conforming pack keeps `assertion` null. The packer refuses to write a manifest whose assertion is non-null, and the reader refuses a pack whose assertion is non-null. A future format that carries verified correctness needs a new format version, new fields, and its own evidence chain. It does not grow inside this one.

## Refusals

The packer and the reader refuse, exit non-zero, and name what mismatched:

- `missing-source`: a source ref whose file is absent under the capture root, or whose bytes do not match the recorded hash and size.
- `mismatched-result`: the result file's bytes do not match the recorded result hash and size, or the file embeds a `result_id` that differs from the manifest's claim.
- `schema-drift`: a referenced capture input declares a `schema_version` that differs from the manifest's `capture_schema_version`.
- `invalid-manifest`: the manifest violates the field rules in this document, including a `captured_at` that is not a strict zero-padded real UTC timestamp and `missing_fields` entries with a blank or absent `field` or `detail`.
- `wrong-pack-format`: `evidence_pack_format_version` is not `1.0`.

The packer validates the flat `pack-spec.json` it consumes before building anything and refuses with the same named kinds, so a malformed fixture cannot crash the packer or half-write a pack. Packaged-file mismatch refusals name the rejected bytes' actual hash and size next to the recorded ones. Every reader refusal prints `rejected pack sha256:` with the SHA-256 of the pack bytes it refused, so a rejected pack can be identified and excluded by hash alone.

## Determinism

`pack.py` writes byte-stable archives: entries sorted by name, fixed timestamps (1980-01-01 00:00:00), fixed file permissions, no platform-dependent metadata. Packing the same fixture twice to the same out path yields the same bytes, so the pack's own SHA-256 is identity evidence too. The out path is part of `reproduction.command`, so regenerations must reuse the same out path to stay byte-identical.

## Replayable refusals

Refusals are evidence too, and the repository commits the inputs that produce them. `fixtures/missing-captured-at`, `fixtures/invalid-captured-at`, `fixtures/null-missing-fields`, `fixtures/missing-detail`, and `fixtures/mismatched-result-id` are inputs the packer refuses by name. `tests/test_mutations.py` rebuilds the committed example pack with one defect at a time — five packer cases, seven reader cases — and replays each through the real command line, asserting the named refusal and that nothing is written. The `receipts/` directory records actual runs of the example and of two reader refusals.
