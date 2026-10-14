# How the meter close-up numbers were made

This file says how each number in [README.md](README.md) was produced and how far to trust it. The tables themselves are in `results/`, each with the command that generated it.

## Photos

83 photos from Wikimedia Commons, all CC0, public domain, CC BY or CC BY-SA. They come from the `Electricity meters (kWh)` category and meter-brand searches. `manifest.csv` records each photo's URL, license and author. `make images` downloads them to `~/house-scanning-data/meter`; they are never committed.

Three photos are excluded: two show several meters, and one label shows a customer's name and address. Of the 80 usable photos, 22 carry a US-style class label (CL200, 200 CL, CL20) and 57 an IEC current rating such as `10(60)A`. Most meters are Taiwanese, European or Canadian; US residential meters are a minority.

## Labels

Two AI models, not people, read every photo, so every number is only as good as their labels. Reader 1 (Claude Opus, the experiment's author) zoomed into each original. Reader 2 (a separate Claude Sonnet agent) read each photo without seeing reader 1's labels. The label is always reader 1's number, so reader 1 chose which printed identifier is the meter number: a utility plate over a maker serial.

`labels.py` records two agreement rules:

- **Loose** (73 photos, used for the headline): reader 1's number equals any number reader 2 listed, or one contains the other and the shorter has at least 6 characters, and neither reader doubted a character.
- **Strict** (62 photos): both readers' main numbers are identical and both were sure. Reading and ranking results are reported under both rules.

To confirm the labels by hand, run `make review` and open `~/house-scanning-data/meter-closeup/review.html`. Each photo shows whole, beside the agreed number. Press K to keep the number, or F to type a correction. Then run `uv run python -m meter_eval.review ingest <downloaded csv>` and rebuild with `uv run python -m meter_eval.labels`. A kept number counts as agreed under both rules; a corrected one does not until someone keeps it. A Keep of the readers' number saved in the browser stays only while the page shows that number, so after a label correction that photo opens again; a correction typed there, and a Keep of that correction, stay. Ingest merges new answers into earlier ones, and a rebuilt page shows each earlier correction in place of the readers' number, so Keep confirms the correction even in a new browser.

## Keeping meter numbers out of the repository

`CONTRIBUTING.md` forbids committing meter numbers. `manifest.csv` stores each one as an HMAC-SHA256 digest. An unkeyed hash of a short number can be reversed by trying every number of that length. The keyed digests cannot be reversed without the key, which lives in `~/house-scanning-data/meter-closeup/hmac.key` and in the `METER_HMAC_KEY` repository secret. To start over without the key, run `uv run python -m meter_eval.labels --new-key` and rebuild the manifest from the plaintext labels.

`make leakcheck` digests every run of 4 or more digits in every tracked file of the repository, not only this folder, and fails on any identifier a reader transcribed; the shortest is a four-digit utility plate number. Only cells in the measurement columns listed in `leakcheck.py`, which this code fills with measured numbers, are exempt. Any other cell is scanned whatever its shape, and other files are scanned whole with every separator dropped, as `normalize()` drops them, so an identifier written with dots, slashes, colons or line breaks is still caught. Before the labels' free-text notes reach `manifest.csv`, every digit the leak check could count toward an identifier becomes `#`: any digit within 24 letters and digits of three others, once separators are dropped.

Pull-request code never receives the key, because the key would let it reverse the digests. CI runs the keyed check only on `main`, after merge or when dispatched from `main`; run `make leakcheck` locally before asking for review.

## Reading (question 1)

`meterocr/` is a Swift tool that calls Vision's `VNRecognizeTextRequest` (revision 3, `en-US`, `minimumTextHeight` 0) on macOS, the API the iPhone app would call. The headline uses the `accurate` level without language correction. A number counts as read when the whole labelled number appears in one recognized line, or in lines sharing a row. `results/clean.md` has every configuration. The `fast` level misses a quarter of numbers, and language correction adds nothing.

The class-label matcher was loosened after the first run to ignore separators and read `×` as `x`, because every first-run miss was one of those. The meter-number matcher was never changed.

## Retake checks (question 2)

The 71 photos whose agreed number read correctly were degraded in five controlled ways: Gaussian blur, horizontal motion blur, downscaling, a white glare patch over the number, and cropping the frame into the number. These are synthetic changes to real photos. Each level is scaled to the height of the number's line.

Each degraded image is saved as a JPEG, and Vision reads that JPEG. Every check is computed on the same decoded JPEG. An earlier run computed the checks on the pixels before encoding. Moving to the decoded JPEG changed no read result and moved the focus threshold from 6.63 to 6.68, 0.8%.

Each check is measured three ways. The whole photo needs no locator. The top candidate of the number-finding ranking is what the phone can compute. The true number's box, from the undegraded read, is unknown to the phone, so those rows only show what a perfect locator would allow.

For each photo and degradation, the break is the first level from which the photo never reads again. A check's retake threshold is the value at or below which 95% of photos have broken. Every threshold meets the pass criterion by construction, so two tests decide which checks work: the AUC over all degraded reads, and how many of the 75 real photos that read correctly each threshold would reject. `results/sweep.md` has the curves, thresholds and rejection counts.

Only two checks work on the phone. Glare and framing checks work on the true box but not on the top candidate, because a washed-out or cut number usually stops being the top candidate: it was the number on 1,366 of 3,309 degraded reads. "Rejects 0 of 75" is weak evidence, because no real photo is near a threshold. The smallest label is 22 px, and the lowest whole-photo sharpness is 58.7. Even above every break, 0.7–2.6% of degraded reads failed anyway.

Each photo's rows are written to one file under `~/house-scanning-data/meter/sweep/rows/` by an atomic rename, tagged with the digest of the label they were scored against and a digest of the measurement inputs: the photo's bytes, its number box and the degradation levels. A changed box changes every degraded image even when the same levels run, so it forces a new sweep. `degrade.expected_levels` gives the exact levels each photo takes, from its size and number box; a photo skips only the downscaling and edge levels it cannot reach. Rows also carry a digest of the measurement code: the Swift reader and the degradation, check, matching and ranking modules. `make sweep` skips a photo only when its file holds exactly those levels under the current label, inputs and code. If a shard fails, `make sweep` fails too. `make q2` refuses to run, naming the photos, when any file falls short.

## Second pass (question 3)

Every failed degraded read was retried on a crop around the top candidate, and on that crop upscaled 2x. Either retry recovered 2–5% of failures, and 10% for numbers made too small. A retake beats a second pass.

## Barcodes and finding the number (questions 4 and 5)

`meterocr` also runs `VNDetectBarcodesRequest` (revision 4). Candidates are the digit-bearing tokens of every recognized line. A candidate is right when it equals the labelled number after dropping separators and any leading letters. `locate.py` scores candidates by barcode confirmation, a `No.`, `Nr.` or `#:` label, standing alone on the line, and length, and penalizes specification lines, rotated text and runs of zeros.

The rules and weights were written on the odd-numbered photos only and committed before the even-numbered photos were scored (`results/locate_dev.md`, then `results/locate.md`).

The held-out set is split by physical meter, not by photo number, so no meter the rules saw is scored as held out. A meter is identified by its labelled number's keyed digest. A meter with any odd-numbered photo goes wholly to the design side (`locate.meter_splits`). One meter had three photos across both halves; moving its even-numbered photo to the design side changed barcode precision from 6/8 to 5/7.

The held-out headline counts every agreed photo: the top three held the number on 27 of 34 (79%), and the first candidate on 20 of 34 (59%). Two of the 34 are photos Vision did not read at all, which cannot offer the number, so they count as misses. Over only the 32 photos Vision read, the rates are 27/32 (84%) and 20/32 (62%); `results/locate.md` reports both. The earlier write-up reported 28/33 (85%), which both excluded the unread photos and let one meter sit in both halves. Both criteria fail under every count. Two photos have no labelled number, so their meter is unknown; neither is held out. A cross-check against every number either reader transcribed found one more shared string, a model code printed on two different meters whose labelled numbers differ, so those stay separate.

The number-finding misses split evenly:

- **Several real identifiers on one plate.** The rule picked a maker serial, a second barcode or a `SERIAL#` line. Base needs to say which identifier it uses.
- **Vision split the number.** A prefix or digits landed in a separate observation, so the exact number never became a candidate.

The US-only barcode result (2 of 2 held out) was chosen after scoring, so it is exploratory.

## Limits

- Commons photos are mostly deliberate, well-lit close-ups, so homeowners' photos will read less often than 97%.
- The degradations cover one glare model and horizontal motion only, with no low light, noise or perspective. Class labels were not swept.
- Sharpness depends on the camera's processing. [FIELD_TEST.md](FIELD_TEST.md) checks both thresholds on an iPhone.
- macOS Vision stands in for iOS Vision, with the same API and revision, and is not verified on a phone.
- The rules were written after the author had read every photo, so the held-out half limits fitting but does not remove it. Its 33 photos give wide intervals.
- This branch's history was rewritten on 2026-09-26 to remove example meter numbers and unkeyed digests. Older commits may stay reachable on GitHub by their IDs.

## Reproduce

These commands need macOS with Xcode 26, uv and the HMAC key. Run them from this folder:

```sh
make images   # download the photos (about 200 MB)
make q1       # results/clean.md, about a minute
make sweep    # the degraded reads, two processes of up to 2 GB, about 25 minutes
make q2       # results/sweep.md
make q45      # results/locate.md
make check    # lint and unit tests
make leakcheck
```
