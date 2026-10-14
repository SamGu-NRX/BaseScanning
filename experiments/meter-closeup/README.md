# Meter close-up readability

**Question.** From one close-up, can the phone read the meter number, find it among everything on the plate, and know when to ask for a retake? Pass criteria, set before each run:

- reading the number on at least 90% of close-ups;
- filling it in unasked at 95% precision, or holding it in a tap-to-confirm list of three on 95% of photos;
- a retake check that keeps 95% of photos above its threshold readable.

**Run.** `make images q1 sweep q2 q45` on macOS with Xcode 26, uv and the HMAC key ([METHODS.md](METHODS.md)).

**Result.**

- Apple's on-device Vision read the number on 71 of 73 real photos (97%) and the US class label on 20 of 22.
- No rule can fill the number in unasked: the best is right on 5 of 7 held-out picks. The top three held it on 27 of 34 held-out photos (79%), short of the bar; two photos Vision did not read count as misses. Held out means meters the rules never saw.
- Two phone-side retake checks work: whole-photo sharpness of 6.68 or less, and a top candidate 33.9 px tall or less. Glare, framing and hand shake have none.
- The labels come from two AI readers, not people.

These figures use the earlier matcher that discarded arbitrary leading serial letters. The current
ranking and read scorer share a row matcher: labels and ratings are boundaries, adjacent
numeric groups cannot be omitted, and an admissible exact target match ranks first. Rebuild the private number-core
labels and rerun the ranking evaluation before attributing those figures to the current code;
that dataset rerun has not been performed.

**What it changed.** The app offers three candidates to tap, barcode match first, and asks for a retake when the photo is blurry or the number too small ([PORTING.md](PORTING.md), [FIELD_TEST.md](FIELD_TEST.md)). Tables are in [results/](results/).
