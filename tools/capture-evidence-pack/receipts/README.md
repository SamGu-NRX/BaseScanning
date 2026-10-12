# Receipts

Recorded command-line runs of the packer and the reader. Each run lives in its own dated directory and is never edited afterwards; a later run gets a new directory.

- [`2026-10-12/`](2026-10-12/): runs of the current tools. The example pack's SHA-256 is `623d69e59235cdc81be0a42855c5a530be2deb04de8f9de92e82c200b85df9e2`, and both packer runs in `pack-example-twice.txt` produce it.
- The files directly in this directory (`byte-stability.txt`, `pack-example.txt`, `pack-refusal-missing-source.txt`, `read-example.txt`, `read-refusal-*.txt`) were recorded on 2026-10-10 on another machine, before the format gained `result_schema_version`. They describe pack `53857e0a...`, which no longer exists in the repository, and they print absolute paths from that machine. Kept as history.
