# Verification (S4)

**Question.** Do the app and server branches do what the plan says? Pass: the app at
`t3/ios-mvf`'s head reaches a result from the real ADVIO replay and keeps its scan; every server
answer at `t3/server`'s head validates against the result schema and never passes without the
coverage its checks need; hostile inputs are refused, or answered with a valid result, within
10 s and 500 MB of server growth; each plan metric has evidence at the current head.

**Run**, from `verification/`, with uv (and Xcode 26 with an iOS Simulator for `sim-app`):

```
make sim-app      # REF=, SERVER_REF= or SERVER_URL=
make e2e          # ARGS="--app-export <sim report>"
make scoreboard   # writes SCOREBOARD.md
make test
```

Reports go to `~/house-scanning-data/reports/`, outside git, because replay frames come from
non-commercial datasets. What each check asserts is in the docstrings of `hsverify/e2e.py`,
`resultcheck.py` and `simrun.py`.

When a case's battery width or corner allowance differs from public rules, e2e uses the
value reported in the result's spot or rule-keyed check threshold. If the result omits it,
the report says "assumed public rules; server may override" and marks the case
`assumptions unverified`. It skips the rule-dependent checks and case expectations, while
retaining rule-independent invariants. These records cannot establish acceptance. Exit
code 2 means the run is inconclusive for this reason; exit code 1 still means another
failure or incomplete check. The verifier does not read private backend files.

**Result.** At `t3/server` `3baa338`, with the app's upload from `c820cf1` (#10's freeze, synthetic
replay, hosted server), 49 of 50 scenes pass (45 answered and checked; slowest real scene 0.48 s).
The ETH3D scene fails: after its one requested capture the server leaves pool unsure with no
request or reason.

**What it changed.** S2 fixed seven defects it found:
- unseen ground passing the pool check;
- a short reach cutoff;
- unbounded input cost;
- incomplete photo requests;
- repeated requests past a wall end;
- ground past a real end ignored;
- a facing gap called too close to call.

The friction audit (#61) became team decision 8.
