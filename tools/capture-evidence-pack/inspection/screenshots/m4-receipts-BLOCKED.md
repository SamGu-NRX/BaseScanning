# M4 receipt pack: BLOCKED — every case NOT_RUN

**Every case in this pack is NOT_RUN — blocked: agent-browser Chrome cache absent in the capture sandbox. The failure occurred before any page navigation, not after.**

## What these files are

The six plain-text receipt files listed below in this directory are launch-failure
receipts, not interaction receipts. agent-browser failed to start Chrome in the capture
sandbox (Chrome cache absent), so every one of them contains only the same
agent-browser "Chrome not found" launch-failure text, and all six share one identical
git blob:

    blob: 4bfa38d799568400fee7b59f121f651737c1b810

(Verified against the head commit bcd80ffdfb37cf2ca5d3974b920a449a975e1a0d — the six files are present with the shared
blob above, confirmed via git ls-tree / Git contents API.)

## The six files (preserved as-is, unchanged)

| File | Label | Shared blob |
|---|---|---|
| m4-clip-click.txt | NOT_RUN (blocked) | 4bfa38d799568400fee7b59f121f651737c1b810 |
| m4-clip-key.txt | NOT_RUN (blocked) | 4bfa38d799568400fee7b59f121f651737c1b810 |
| m4-fb-click.txt | NOT_RUN (blocked) | 4bfa38d799568400fee7b59f121f651737c1b810 |
| m4-fb-denied.txt | NOT_RUN (blocked) | 4bfa38d799568400fee7b59f121f651737c1b810 |
| m4-fb-key.txt | NOT_RUN (blocked) | 4bfa38d799568400fee7b59f121f651737c1b810 |
| m4-stub.txt | NOT_RUN (blocked) | 4bfa38d799568400fee7b59f121f651737c1b810 |

None of these files has been modified, renamed, or deleted by this commit. They are
preserved exactly as the blocked run left them. Other files in this directory are
outside this ledger's scope and are not relabeled by it.

## Verbatim failure text (identical in all six files)

    > ✗ Auto-launch failed: Chrome not found. Checked:
    >   - agent-browser cache: /home/user/.agent-browser/browsers
    >   - System Chrome installations
    >   - Puppeteer browser cache
    >   - Playwright browser cache
    > Run `agent-browser install` to download Chrome, or use --executable-path.

## What the blocked receipts added in this commit prove — and do not

The blocked receipts in this commit contain no executed behavior of any kind:

- NO copy behavior — no copy interaction was performed
- NO clipboard content — no clipboard read or write produced any content
- NO keyboard activation — no keyboard interaction was performed
- NO viewport rendering — no page was ever rendered
- NO axe result — no axe accessibility audit was ever executed

The browser never launched and no navigation occurred, so nothing on-page was ever
reached. These receipts are evidence of a tooling block, not evidence of behavior. (No
axe result exists in these six receipts; any axe evidence in separately produced files
is not part of this blocked pack.)

## Real receipts

Genuine, executed m4 receipts will be added separately under
inspection/screenshots/m4-real/ (not in this directory). When they land, they will carry
the actual interaction, clipboard, keyboard, rendering, and axe evidence that this pack
cannot provide.
