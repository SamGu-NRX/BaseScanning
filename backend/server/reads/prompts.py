"""Reader prompts. `meter/v0` and `panel/v0` are the B2 strings, unchanged.

`panel/v1` adds enclosure and mains counts. It is not the default: the cached
answers that pass the gate were collected with `panel/v0`, and v1 has not been
measured live. Set `HSS_READS_PANEL_PROMPT=panel/v1` to try it.
"""

from __future__ import annotations

METER_PROMPT = """This is a photo of an electricity meter, taken for a utility site survey.
Transcribe only what is printed and visible. Never guess characters you cannot see; if part of a
string is unreadable, give null rather than a guess.

Return one JSON object and nothing else, with these keys:
- "meter_number": the meter's identifying number exactly as printed. When the plate carries
  several identifiers, prefer the utility's meter number (often large print, next to a barcode or
  after "No."/"Nr."/"#") over the manufacturer's serial, model or catalogue codes. null if not legible.
- "number_candidates": up to 5 distinct identifier strings printed on the meter (meter numbers,
  serials, barcode numbers), most likely meter number first. Exclude ratings, voltages, Kh, form,
  register readings and dates.
- "class_label": the ANSI class marking exactly as printed (for example "CL200", "CL 320",
  "CL20"); for meters without a CL marking, the current rating as printed (for example
  "10(60)A", "30(200)A"). null if none is legible.
- "manufacturer": maker name as printed, or null.
- "form": ANSI form as printed (for example "FM 2S"), or null.
- "meter_can": {"visible": true|false, "type": one of "ringless_socket", "ring_type_socket",
  "meter_main_combo", "multi_meter_bank", "pole_or_pedestal", "cabinet_or_panel_mounted",
  "no_socket_bolt_in_meter", "not_visible", "other"; "notes": short}
- "condition": {"rust": "none"|"light"|"heavy"|"cannot_tell", "physical_damage": true|false,
  "notes": short}
- "legibility": "good"|"partial"|"poor"
"""

PANEL_PROMPT = """This photo was taken for a residential battery-install site survey. It may show an
electrical panel (breaker box, load center, fuse box), its door label, a main breaker, a meter-main
combination, or something else.
Report only what is visible. Never guess text you cannot read; use null when unsure.

Return one JSON object and nothing else, with these keys:
- "equipment": one of "breaker_panel", "fuse_box", "meter_main_combo", "main_disconnect",
  "panel_label_only", "single_breaker", "industrial_or_non_residential", "not_electrical", "other"
- "manufacturer": the panel maker if visible as text or badge (for example Square D, Federal
  Pacific / FPE, Zinsco, Sylvania, GTE, Challenger, GE, Siemens, ITE, Eaton, Cutler-Hammer,
  Westinghouse, Murray, Crouse-Hinds, Bryant, Wadsworth, Pushmatic/Bulldog), or null.
- "brand_evidence": "printed_text"|"logo_badge"|"breaker_style_only"|"none"
- "product_line": series name as printed (for example "Stab-Lok", "QO", "Homeline", "Magnetrip",
  "Pushmatic"), or null.
- "main_breaker_amps": the main breaker's rating as an integer if its marking is legible, else null.
- "panel_rating_amps": the panel or bus rating from the label as an integer if legible, else null.
- "hazard_line": one of "federal_pacific_stab_lok", "zinsco_sylvania_magnetrip", "challenger",
  "pushmatic", "none_identified", "cannot_tell"
- "condition": {"rust": "none"|"light"|"heavy"|"cannot_tell", "damage_or_scorching": true|false,
  "notes": short}
- "text_seen": up to 10 short strings you can read that identify the maker, line, or ratings.
- "legibility": "good"|"partial"|"poor"
"""

PANEL_PROMPT_V1 = PANEL_PROMPT.replace(
    '- "legibility": "good"|"partial"|"poor"\n',
    '- "enclosures_visible": integer count of separate service enclosures visible, or null.\n'
    '- "mains_visible": integer count of independent main breakers visible, or null.\n'
    '- "legibility": "good"|"partial"|"poor"\n',
)

METER_MODEL_PROMPT = """This is a close-up photo of an electricity meter. Identify the physical meter model so its
datasheet dimensions can be looked up. Return one JSON object and nothing else, with keys:
  manufacturer: brand printed or clearly recognisable, else null.
  model_family: product family/model (e.g. "Itron CENTRON", "Itron OpenWay CENTRON", "Landis+Gyr Focus AX",
    "Aclara I-210+", "GE I-210", "GE kV2c", "Elster REX2", "Elster A3 Alpha", "Sensus iConA",
    "Westinghouse D4S"), else null.
  model_evidence: "printed" if the model name/type code is legible on the plate, "visual" if inferred
    from the housing look, null if unknown.
  model_confidence: 0-1.
  cover_material: "glass", "polycarbonate" or "unknown".
  ansi_socket_meter: true if it is a round North-American socket (S-base) meter, false otherwise.
  base_flange_visible: true if the meter's base rim/flange (the widest ring where the meter meets the
    socket, often under a sealing ring) is visible around most of its circumference.
  socket_type: "ring" (separate sealing ring around the base), "ringless" (socket cover clamps the rim),
    or "unknown".
"""

PROMPTS = {
    "meter/v0": METER_PROMPT,
    "panel/v0": PANEL_PROMPT,
    "panel/v1": PANEL_PROMPT_V1,
    "meter_model/v0": METER_MODEL_PROMPT,
}


def prompt_text(prompt_id: str) -> str:
    try:
        return PROMPTS[prompt_id]
    except KeyError as exc:
        raise KeyError(prompt_id) from exc
