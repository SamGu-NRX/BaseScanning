---
cursor:
  subagentId: "bc-93048192-3bbf-5d44-a35a-4f4828072eba"
---

# internal/criteria

- `SSR_Prompts.md`: **missing.** The original attachment on the Notion page could not be downloaded (checked 2026-09-26 ~16:00 UTC):
  - `notion-fetch` on the page returns the file block only as an internal `attachment:4cc463e9-…:SSR_Prompts.md` reference, not a signed URL; fetching the block itself returns an empty page.
  - `notion-download-attachment` only serves files uploaded by this integration (404 for this id).
  - No Notion Custom Agent exists to read it; Gmail MCP needs interactive auth; the file is not in Slack, Google Drive, or any branch/PR of `SamGu-NRX/house-scanning` (it is kept in the git-ignored `private/`).
  - **To unblock:** Hunter downloads the attachment and drops it here as `internal/criteria/SSR_Prompts.md`, or pastes its text into any Notion page body (page bodies are fetchable).
- `ssr-quick-reference-notion.md`: copy of Hunter's Notion digest of all 13 prompts. This is the working source for `docs/criteria-spec.md` until the original arrives.
- `criteria.yaml`: machine-readable thresholds per placement profile and per-criterion outcome vocabulary, derived from the digest.
- `house-labels.json`: machine-readable draft ground-truth labels (mirror of `docs/house-labels.md`).
