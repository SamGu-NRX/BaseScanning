# Caliper protocol for meter covers

Team tool, after the capture, with the seal untouched and the meter closed. A 0–200 mm caliper is required. A 150 mm caliper cannot span a 6.95 in base. Nothing in this protocol is a customer step.

Photograph every reading so the jaws and the scale are in frame. Photograph the plate with the meter number covered. The row that lands in `meter_models.v1.json` uses `meter_number` never; the truth file says `REDACTED`.

## What to measure

1. Cover outside diameter, two readings at right angles. Record both in `readings_m`. If they differ by more than 0.5 mm, do not average them into `cover_od_m`; leave the cover null and say so in `notes`.
2. Sealing-ring outside diameter, if a ring is fitted and the jaws can sit on it without moving the ring.
3. Base flange outside diameter, only where the flange is visible past the ring or the ringless cover. If it is hidden, leave `base_od_m` null.
4. Rim offset: the distance from the socket face (the wall of the meter base that sits on the socket) out to the cover rim, with a depth rod. This is not drawing letter C on the CENTRON sheet. If you cannot seat the rod on the socket face, leave `rim_offset_m` null.
5. Cover material: `polycarbonate` or `glass`.
6. `type_code` as printed (for example `C1SD`). Do not infer a code from the family name.

## Row shape

`cover_od_prov` (and any other measured field) is `caliper`. Also set `measured_by` (initials), `n_units`, `measured_on` (`YYYY-MM-DD`), and `readings_m` (metres). `usable_for_scale` may be true only for a cover measured this way or taken from a datasheet. One unit is enough for a house row. A family row that Base wants in the shared table should be at least three units of the same type code before `usable_for_scale` is turned on for that family. Until then the house row can still be `usable_for_scale: true` for that house's own scale run, with `n_units` 1, and `notes` must say it is a single unit.

`source.url` is not required on a caliper-only row. Do not copy a guessed diameter from another model.

Bump `table_version` patch when a new source replaces a number, minor when a row is added.
