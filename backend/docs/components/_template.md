# Component: <name>

## Purpose

What this package does, in one paragraph.

## Owner WS and brief

WS id, brief path, and the PR that last changed the contract.

## Inputs / outputs (artifact names)

Inputs this package reads and the artifact names it writes. Use the stage folder names from the build plan.

## How to run on a fixture

The command that runs this package against a committed or fetched fixture. Name `mini-arkit` explicitly (`data/fixtures/packets/mini-arkit/packet.json`); do not glob `packet*.json`.

## How to test

The make target or pytest path, and what it does not cover.

## Rebuild from scratch (no VM-only state)

Commands that recreate every output from the store or from public downloads. No path that exists only on one machine.

## Last gate table (pasted)

| Check | Expected | Observed |
|---|---|---|
| | | |

## Known limits

What is stubbed, skipped, or waiting on another workstream.
