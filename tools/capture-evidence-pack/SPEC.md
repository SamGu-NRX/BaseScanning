# Capture evidence pack format

A capture evidence pack is a zip archive that accompanies a bug report about one capture and one result. It preserves which capture and which result the report describes, and it carries nothing else. The pack references the capture inputs; it never embeds them. Everything here is synthetic data from this repository's fixtures.

This document is the reference for the format. Version 1.0 is the only version.

## Pack layout

A pack is a zip archive with exactly two entries:

- `manifest.json`: the identity evidence described below.
- `report.md`: the bug report text the pack accompanies, as UTF-8.

The entry names are fixed. The packer reads the report from `report.md` at the fixture root and packs it under the same name, so a fixture cannot name its report anything else. The archive limits below bound how the reader opens a pack.

`manifest.json` hashes every other entry under `packaged_files`. A manifest cannot contain its own hash, so manifest integrity is pinned by the receipt the packer prints and the reader repeats. Anyone who relays a pack records that receipt.

## manifest.json

The manifest is one JSON object. Every field below is required; a manifest missing any of them is invalid, and both the packer and the reader refuse it. A required field that is present with a JSON `null` is invalid in the same way, except `geometry_correctness.assertion`, which must be null. Unknown top-level fields are invalid. A key that appears twice in one object is invalid, because two parsers can disagree about which value counts.

- `evidence_pack_format_version`: string, `1.0`.
- `capture_schema_version`: non-empty string naming the schema the capture inputs follow, for example `synthetic-capture/2026.10`.
- `result_schema_version`: non-empty string naming the schema the result file follows, for example `synthetic-result/1.0`.
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

Every path in a pack (`capture_root`, each `path`, and the zip entry names) is relative POSIX: no leading slash, no backslash, no empty, `.` or `..` segment, and no control character (U+0000 to U+001F, U+007F to U+009F). A NUL makes the operating system's path calls fail, and the other controls cannot be shown faithfully in a receipt, so both tools refuse such a path by name instead of trying to open it.
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

Refs point at files; the pack does not contain them. A reader that wants the bytes resolves each path against `capture_root` (or an explicit override) and checks the hash and size. A path that resolves outside the root through a symlink does not satisfy its ref. A capture input written as a JSON object that declares a `schema_version` field must declare the manifest's `capture_schema_version`; anything else, including a declared `null` or the key appearing twice, is the `schema-drift` refusal.

A referenced file counts as a JSON object when it is UTF-8 without a byte-order mark, parses as JSON with Python's `json` module (which accepts `NaN` and `Infinity`), and has an object at the top level. Only such a file can declare `schema_version` or `result_id`; every other file is opaque and is checked by its bytes alone. Both tools apply this one definition, so a file cannot make a declaration to one tool and be opaque to the other, and a repeated key is a refusal, never "the last value wins". The example pack points into `tools/capture-evidence-pack/fixtures/example/`, which is committed synthetic data. A pack about a real capture would point into `captures/`, which git ignores.

### missing_fields

Real captures have gaps: a depth map the device never produced, an intrinsic that never arrived. A pack enumerates every such gap in `missing_fields` instead of letting absence stay silent. Each entry names the capture `field` and a non-empty `detail` explaining the gap; an entry with a blank or absent `detail` is invalid. An empty list means the described inputs were complete. Omitting the key is invalid, and so is an explicit `null`: a reader cannot tell a null from a forgotten one, so the packer writes the key either way and both sides refuse a null.

### Result identity

The manifest claims which result the report is about through `result_id`, `path`, `sha256`, and `bytes`. The result is resolved under the capture root like a source ref, and its bytes must match the recorded hash and size.

The result follows its own schema. When the result file is a JSON object that declares `schema_version`, the declaration must equal `result_schema_version`, never `capture_schema_version`. The two versions move independently: a capture format and a result format change on different schedules.

When the result file is a JSON object with a `result_id` key, it names itself, and the packer and the reader compare that name with the claim. The key's value must be a non-empty string equal to the manifest's `result_id`. A `null`, a number, a blank string, or the key appearing twice is the `mismatched-result` refusal, the same as a different name. Only a file without the key, or a file that is not a JSON object, makes no self-identification. Then the tools state a narrower guarantee instead of implying more: the receipt says the exact bytes were verified and that the file does not itself say which result it is.

### reproduction

- `command`: non-empty string. The exact command a reader runs to re-derive the receipt from the pack and the referenced inputs.

The packer fills this field with the pack path quoted for a POSIX shell, so a path with spaces or shell characters survives `shlex.split`. The reader repeats it in its receipt, and the inspection page shows it so a reader can paste it.

### geometry_correctness

- `assertion`: must be JSON `null` in a conforming pack.
- `note`: non-empty string explaining why the slot is empty.

## Schema versions

Three versions ride in every manifest, and all are explicit:

- `capture_schema_version` describes the capture inputs the report is about.
- `result_schema_version` describes the result file.
- `evidence_pack_format_version` describes the pack itself.

None is inferred, defaulted, or optional. `report.md` is the report's prose and is never checked against a schema version. A reader that meets an unknown pack format version refuses the pack instead of guessing.

## Identity is not correctness

This format records identity only: which capture, which result, which bytes, which versions, which gaps. It makes no claim that any geometry is correct, plausible, or verified.

The only slot where such a claim could live is `geometry_correctness`, and a conforming pack keeps `assertion` null. The packer refuses to write a manifest whose assertion is non-null, and the reader refuses a pack whose assertion is non-null. A future format that carries verified correctness needs a new format version, new fields, and its own evidence chain. It does not grow inside this one.

## Archive limits

The reader reads a pack file once, at most 8 MiB, and opens the archive from those bytes. It refuses with `invalid-archive`, before decoding any entry, when the central directory shows:

- a number of entries other than two, a repeated name, or a name other than `manifest.json` and `report.md`;
- a name that is absolute, empty, contains a backslash, or has a `..` part;
- a directory, a symlink, or another non-regular Unix file type;
- an encrypted entry, or a compression method other than stored or deflated;
- a declared size over the entry's budget (1 MiB for `manifest.json`, 2 MiB for `report.md`);
- a declared size more than 100 times the compressed size.

It then decodes each entry through a cap of its budget plus one byte, so a size field that lies cannot widen the read, and refuses when the decoded length differs from the declared one or the CRC fails. Referenced inputs are read once each, at most 64 MiB.

The packer holds its output to the same limits before it writes anything. It refuses a manifest that would encode to more than 1 MiB and an archive over 8 MiB, and it stores an entry without compression when deflate would shrink it past 100:1, as a long run of one character does. That choice depends only on the entry's bytes, so repacking stays byte-identical. A pack the packer reports as PACKED is one these limits accept.

These numbers are resource bounds, not measurements. No real pack exists to calibrate against. The committed example is about 1.4 KB with a 1.6 KB manifest, so the bounds leave three orders of magnitude of headroom while keeping a hostile archive from forcing an unbounded read, an unbounded decompression, or a scan of thousands of entries. Deflate cannot exceed about 1032:1, and text manifests and reports compress below 10:1, so 100:1 is far outside honest input. A pack about real captures with inputs over 64 MiB needs a reader with a streaming hash and a new version of this section.

## One read per input

The packer and the reader each read every file once and derive everything from those bytes: the recorded and checked hashes, the schema and identity checks, the packed report, and the pack hash in every receipt. A file that changes while a tool runs cannot be hashed as one version and packed, parsed, or reported as another. The packer builds the archive in memory and moves it into place with an atomic rename, so a refused or failed run leaves any earlier file at the out path untouched.

## Refusals

The packer and the reader refuse, exit 2, and name what mismatched:

- `missing-source`: a source ref whose file is absent under the capture root, resolves outside it, or is not a regular file, or whose bytes do not match the recorded hash and size. A pack path that does not exist is refused with the same kind.
- `mismatched-result`: the result file's bytes do not match the recorded result hash and size, or the file embeds a `result_id` that is malformed or differs from the manifest's claim.
- `schema-drift`: a capture input declares a `schema_version` other than `capture_schema_version`, or the result declares one other than `result_schema_version`.
- `invalid-manifest`: the manifest violates the field rules in this document, including a `captured_at` that is not a strict zero-padded real UTC timestamp, `missing_fields` entries with a blank or absent `field` or `detail`, a required field set to `null`, and a repeated key.
- `invalid-archive`: the pack breaks the layout or a limit in Archive limits, is not a readable zip, or carries a `report.md` that is not UTF-8.
- `wrong-pack-format`: `evidence_pack_format_version` is not `1.0`.

The packer validates the flat `pack-spec.json` it consumes before building anything and refuses with the same named kinds, so a malformed fixture cannot crash the packer or half-write a pack. Its keys are `capture_id`, `captured_at`, `capture_schema_version`, `result_schema_version`, `result_id`, `source_refs` (each with `role` and `path`), `result_path`, `missing_fields`, and an optional `report_path` that must be `report.md`; any other key is refused. Packaged-file mismatch refusals name the rejected bytes' actual hash and size next to the recorded ones.

Every reader refusal prints `rejected pack sha256:` and `rejected pack bytes:` for the bytes the reader actually checked, followed by `reader arguments:` with the arguments as given, whatever their order. When the reader refused before it held the pack bytes (the file is missing or over the pack budget), the hash line says `unavailable` instead of hashing a partial read.

## Determinism

`pack.py` writes byte-stable archives: entries sorted by name, fixed timestamps (1980-01-01 00:00:00), fixed file permissions, no platform-dependent metadata. Packing the same fixture twice to the same out path yields the same bytes, so the pack's own SHA-256 is identity evidence too. The out path is part of `reproduction.command`, so regenerations must reuse the same out path to stay byte-identical.

## Replayable refusals

Refusals are evidence too, and the repository commits the inputs that produce them. `fixtures/missing-captured-at`, `fixtures/invalid-captured-at`, `fixtures/null-missing-fields`, `fixtures/missing-detail`, and `fixtures/mismatched-result-id` are inputs the packer refuses by name, and `fixtures/missing-source` and `fixtures/mismatched-result` are capture roots the reader refuses for the example pack. The tests under `tests/` replay every refusal kind through the real command line, each next to a passing control: `test_mutations.py` rebuilds the example pack with one defect at a time, `test_archive_limits.py` covers every archive limit, `test_cli_contract.py` covers result identity, schema independence, quoting, refusal binding and output paths, and `test_snapshot.py` changes inputs between reads. The `receipts/` directory records actual runs of the example and of the reader refusals.
