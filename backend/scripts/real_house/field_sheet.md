# Real-house field sheet

Crew only. Capture day is not scheduled. Do not invent a house, a date, or a meter number. House ids are `real-001` (expected pass) and `real-002` (expected reject). No street, owner, face, plate, or meter number goes in a filename, a note, or the repo.

Printed copy of this sheet plus `data/meter-models/caliper-protocol.md`. Signed consent is `scripts/real_house/consent-template.md` and stays on paper.

## Do not capture

- No dusk and no dark wall. Daylight on the wall at about 1,000 lux or more. If the phone wants ISO above about 200 at 1/60 s, wait for brighter light. A7: electro-type walls fail near 100 lux, and both test scenes fail near 6 lux.
- No walking blur. Walk slowly. Do not pan while moving. Turn at about 7–10 degrees per second or less (about 4 px of blur at 1/60 s). A7: low-texture walls break near 5.6 px median blur, facade near 11 px. The spec line "drop above 1/60 s while moving" is too loose; drop the frame if the preview is soft.
- Phone stays level, not pitched up, except the short eave looks in the shot list.
- Every wall frame includes the wall base and at least 1 m of ground. No sky-only or lawn-only frames.
- Optional dusk repeat (brief C4 S1b) is cancelled. A7 is a no-go for SfM poses at dusk.
- Nobody in frame. No house number, mailbox, licence plate, or neighbour's window. Frame the walk to leave them out. If one appears, drop that frame in scrub. Do not paint it out.
- No tape, laser, or caliper in any capture frame. Truth is measured after the phones are done.

## How many photos

A10: 40 photos work only when they are dense (one step of 0.5–1 m). Forty frames about 1 m apart on real walks registered 2–7 of 40. The dimmest real session needed 60–70 frames on one side of about 35 m.

Do not thin the walk to 40 pictures around the whole house.

- Guided sessions (S1 and S3): keep the session running through the close-ups. Image-only blank-wall close-ups did not register (`yt-LCPBf86oloE`, 44/183 in 6 fragments).
- Photo session (S2): 60–70 JPEGs, dense on the meter wall, not a thin loop of the whole lot.

## Site check before committing (laser, before any session)

Detached single-family house. Exterior socket meter you can stand 1 m from. Both natural ends of the meter wall reachable on foot. Texas is preferred; any state is accepted. Log the clock time. Sun behind the camera, or overcast.

**real-001, expected pass under A and C.** Meter wall is a side or back wall. On that wall, before you start:

- A clear flush span at least 1.5 m (5 ft) long, starting within 1 m of the meter.
- No window, door, vent, hose bib, wall box, downspout, or AC in that span or within 0.91 m (3 ft).
- No gas meter, regulator, shutoff, or gas pipe within 1.83 m (6 ft) of the span.
- Nearest drive, walkway, or pavers at least 2.13 m (7 ft) from the wall along the span.
- No pool within 3.05 m (10 ft).
- Eave or soffit at least 2.13 m (7 ft) above grade over the span. No porch roof, deck, or stairs over it.
- Ground is lawn, mulch, gravel, or dirt, slope at most 5 degrees over 1.8 m, and clear at least 1.8 m out.
- Nearest fence, hedge, or facing wall at least 1.52 m (5 ft) away.
- Meter working space 0.91 m out by 0.76 m wide is clear. Overhead there is at least 1.83 m.
- Panel is outside on the same wall, or in the garage, with a legible brand and a single main you can see. Do not remove a dead-front.

**real-002, expected reject under C.** Every reachable spot fails, and both natural ends are visible. Typical pattern: the meter wall is fronted by a drive within 0.91 m, or gas plus openings leave no 0.91 m gap, and the adjacent walls are outside a 7.92 m (26 ft) routed reach or blocked by a garage door or a driveway crossing. A bush or fixed AC inside the meter envelope can be a second reason. It must not be the only reason.

If only one house is available, capture real-001 and leave real-002 pending.

## Phones

- One regular iPhone, 12 or later, not Pro, no LiDAR, iOS 16 or later.
- One LiDAR iPhone, 12 Pro or later Pro.
- Primary app: the iOS team's capture build that writes packet format 0.4. If that build is not on the phone, guided fallback is Measure Lab session v2 (zip of `session.json` plus landscape JPEGs), converted later with `session_v2_to_packet.py` and scored offline only. Photo fallback is the stock Camera app, Most Compatible, JPEG, 1× lens, portrait. Fill `photo_purposes.csv` from `photo_purposes.example.csv` (`filename,purpose`).

## S1 — non-Pro guided, 6–10 minutes

Phone upright at chest height (about 1.4 m). Expect 150–300 keyframes.

1. Start 3–4 m from the meter, facing it. Hold 2 seconds until tracking is normal. Tap the meter and confirm.
2. Walk left, parallel, 2–4 m out, at least 2 keyframes per second, about 60% overlap, wall filling the frame, to the natural end. Turn the corner in 2–3 steps. Continue on the next wall to 8 m from the meter or to its end. Tap the corner and the left wall end.
3. Walk back past the meter without stopping the session. Repeat to the right. Tap the right wall end.
4. From 3 m out, one head-on view of every wall face within 8 m, including every entry or garage door.
5. Ground: two passes at 2 m and 4 m out, camera about 45 degrees down, covering the strip out to 3.7 m (12 ft) on open sides.
6. Overhead: from 3 m, a short tilt to the eave or soffit above candidate spots, 2–3 frames per 3 m of wall. Return to level.
7. Tap what is there: gas meter, AC, two corners per door, two bottom corners per ground-floor window within 8 m, fence, driveway edge.
8. Bridge in from 3 m to 1 m with the meter in frame. `meter_close` head-on at about 1 m, then `meter_oblique` about 30 degrees to one side at the same distance (record which side). Cover, ring, and socket face about half the frame. Plate readable, number covered. Then `meter_surround` from about 8 m, plus left, right, adjacent wall, and behind a fence only through an open gate.
9. Panel in the same session if you can reach it: bridge frames, surround, close with the door closed, open with the door open and the dead-front on, main disconnect if it is separate.
10. Close the loop past the start. Accept a TrueDepth prompt if one appears. End.

## S2 — non-Pro photo flow, 60–70 JPEGs

Same A7 rules as S1. Count is a stand-in for spacing: one photo per 0.5–1 m step on the meter wall.

- 20–30 along the meter wall, every 0.5–1 m, at 2–4 m out, half overlap, to both natural ends and past them.
- 10–15 around each corner (45 degrees at the corner) and along the adjacent walls, head-on from 2–4 m, out to 8 m.
- 5 head-on per wall face.
- 6 ground photos looking down from chest height at 2 m and 4 m (strip to about 13 ft).
- 3 eave or overhead, including one looking up above the meter.
- 3 bridge photos walking in (3 m, then 2 m, then 1.5 m).
- `meter_close` head-on and `meter_oblique`.
- 3 `meter_surround` from about 8 m.
- Panel photos as in S1 step 9.

Write `filename,purpose` as you go. Example rows are only `meter-close.jpg,meter_close` and `meter-oblique.jpg,meter_oblique`.

## S3 — LiDAR phone guided

S1 again, with scene depth and confidence on every keyframe. Export `geometry/mesh.ply` if the app offers it. No dusk repeat.

## Truth, after the phones are off

Two people. Laser first. Tape as a check on at least 3 distances. Painter's tape marks stations and is not in any capture frame. s = 0 at the centre of the meter glass, positive to the right as seen from outside.

Photograph each reading and both endpoints. Minimum 12 distances on real-001, at least 3 of them between 4.57 m and 7.92 m (15–26 ft), and at least 2 openings fully measured. real-002 needs at least 6, including every distance the reject rests on and both wall ends.

Record: meter height (grade to glass centre, plus can bottom and top); every corner s; wall length of every chain wall within 8 m; for each opening within 8 m both edges (outer trim face, and write what you touched), bottom, top, width, and the gap to the next opening; equipment distance and s for gas, regulator, AC, hose bib, downspout, wall boxes, vents; facing depth at 3 stations where something is under 4 m; overhead clearance at 3 stations where it is under 3 m; ground distance to drive, walk, and pool edges, plus material and slope over a 1.8 m run at each candidate spot; the capturer's phone height.

Bounds to write on the sheet: laser 0.010 m, tape 0.015 m.

## Meter caliper

Nothing opened. Seal untouched. Follow `data/meter-models/caliper-protocol.md`: two perpendicular cover readings, ring, base if it is visible, rim offset with a depth rod (not the datasheet letter C), material, type code, photo of each reading, plate photo with the number covered.

## Panel truth

Brand as printed, or unreadable. Main breaker amps. Hazard signs you can see. Which wall the panel is on relative to the meter. Whether the panel is inside the house.

## After the day (not on the sheet in the field)

Scrub drops GPS, location, and any service address, and drops frames that fail the privacy rules. `meter_number` in `truth.json` is the literal `REDACTED`. `truth_to_scene.py` refuses to write a scene if a live meter number is still in the file.
