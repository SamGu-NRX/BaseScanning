# Synthetic bug report: frames 0001 and 0002 disagree about the meter anchor

Synthetic fixture data for the capture evidence pack. It describes capture
`capture-synth-0001` and result `result-synth-0001` from the committed example
fixture. No real home, meter, or scan produced any byte of this file.

## What the reporter saw

The synthetic result places the meter anchor 1.2 m along x. Frame 0001 was
taken at the origin and frame 0002 at x = 0.3 m. The reporter expected the
anchor to stay where it is when the camera moves; it did not.

## What this pack proves and does not prove

The pack proves which files this report describes (by hash), which schema
versions govern them, and which capture fields the synthetic device never
produced. It does not claim the geometry in either frame is correct.
