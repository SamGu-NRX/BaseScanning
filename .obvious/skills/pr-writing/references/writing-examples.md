# Writing examples

These examples are made up. They show how much to explain, and none of their facts belong in a real PR.

## Explain the failure and the mechanism

Weak: "Improved session handling. The stale-response guard is robust and all tests pass."

Useful: "Switching accounts while the inbox loaded could show the previous account's messages. The response now carries the account that requested it, and the inbox drops it if that account is no longer active. The regression test starts both loads and finishes the older request last."

The useful version names the trigger, what went wrong and how the fix works. A test count alone wouldn't show that the test covers the race.

## Keep a small change short

Title: "fix(export): keep zero values in CSV downloads"

"CSV downloads left zero-valued cells empty, because the formatter treated every falsy value as missing. It now leaves a cell empty only for null or undefined. `npm run test:csv` passes, including zero, null and undefined cases."

This change needs no architecture story and no screenshot. Fill in the sections the repository requires, and stop there.

## Say what a comparison shows

"A declined payment used to replace the form and throw away the address the customer typed. The error now appears above the submit button, and the address stays filled in for a retry."

Put the matched images right below that paragraph. A caption such as "Same declined-payment fixture at 390 × 844. Before a12bc34, after d56ef78." tells the reviewer the comparison is fair. If no live charge ran, say so once, next to the verification. The images don't prove that payments work end to end, so don't present them that way.

## Keep a limit the reviewer needs

"The migration ran on a copy of the staging schema. Nobody has measured it against production data volume or timed how long it holds locks."

That hands the reviewer a concrete open risk. "Low risk, thoroughly tested" hides it, and a long history of approaches that failed would bury it.
