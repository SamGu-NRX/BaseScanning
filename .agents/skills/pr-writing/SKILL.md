---
name: pr-writing
description: Writes or revises pull request titles, descriptions and visual evidence. Use when preparing a PR, improving how it reads, or updating it after its scope changes. Writing the text does not publish the PR.
---

# Write a reviewable PR

Before drafting, read the repository's template and contributor guide, the diff against the PR's real base, and the current PR body. Apply `unslop` and `technical-writing` to everything you write. Keep what others added: human edits, evidence links and sections a bot manages. Fetch the body again right before you publish, so you don't overwrite someone else's change.

## Explain the change

Open with the problem and what the code does now. Name the screen, operation or interface it touches, so someone who never saw the task can follow. For a large change, say what used to happen, what that cost, and how the change fixes it. A new feature needs no broken predecessor. Describe what it adds.

Order independent changes by how much they matter to users or to the rest of the code. Each heading names a change. Filenames and commit order are not an outline. Explain the mechanism when a reviewer needs it to judge correctness or a trade-off. Link the relevant part of the diff, or the file at the reviewed commit when no diff link exists. Cite earlier research only when a choice rests on it, with the source and why it matters.

Write whole sentences in which someone does something. Leave out clipped status phrases, internal names nobody explained, claims that the work was thorough, and narration about the reviewer or your process. Keep technical detail a reviewer can use, and cut any sentence that changes no decision. Read the short [writing examples](references/writing-examples.md) before drafting. Match how much they explain, not how their sentences are built or how long they are.

Size the body to the change. A small fix gets a short explanation and the one check that proves it. A broad PR can use one section per behavior, with evidence beside each claim. Put long logs, inventories and test matrices in linked files. Never paste a task transcript, a subagent report or a commit-by-commit diary into the description.

## Show evidence

If the change is visible, read [visual evidence](references/visual-evidence.md) and include matched before-and-after captures from the running app. Add a short recording when the change is motion, timing or interaction. Show only the behavior that changed. A new screen gets an after capture alone, labeled as new. If evidence is missing, say so in the body.

Report the checks you ran and what they returned, with the command or a link to the run. Put each limit next to the claim it weakens: fixture data, simulator-only behavior, a check that failed or was skipped, a service nobody tested. Keep steps to reproduce apart from checks you finished. Green CI says nothing about how the screen looks or whether the product owner will accept it.

## Use the repository template

`.github/pull_request_template.md` has sections for What changed, Why, UI changes, Verification and Risk, then a four-item checklist. Keep every section the repository requires. Delete an optional section that would be empty, and don't explain the same thing under two headings. Where a checklist item doesn't apply, replace it with `N/A` and the reason. Tick only work you finished. A large change that belongs together is fine. Don't call it small to tick a box.

Write the title about the final change, in the repository's title format. In stacked work, name the base branch and any PRs this one needs, and describe only what this PR adds. When the scope changes, update the title, the body and the affected evidence in the same edit. Keep captures that still hold, labeled with the commit they show, so nobody mistakes them for the newer build.

Publish with a body file or a structured tool argument. Then open the saved PR and check that the text, links, side-by-side images and videos render. A local draft or a successful upload doesn't prove the published body looks right. Opening the PR, pushing and answering reviews are separate steps, and each needs the user's go-ahead.
