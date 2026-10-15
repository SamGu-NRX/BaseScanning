"""Customer-safe hints. Copy comes from WS-14 later; these strings name the action only."""

from __future__ import annotations

from server.contracts.api import ConfirmPrompt, HintData
from server.reads.types import MeterRead, PanelRead, StillResult

_MESSAGES = {
    "meter_plate_unreadable": "The meter label was not readable. Take another close-up of the nameplate.",
    "meter_number_confirm": "Confirm the meter number on the nameplate.",
    "panel_label_unreadable": "The panel label was not readable. Photograph the label inside the open panel.",
}


def _meter_hint(result: StillResult, meter: MeterRead) -> HintData | None:
    if meter.status == "disabled":
        return None
    if meter.status == "unreadable":
        return HintData(
            code="meter_plate_unreadable",
            message=_MESSAGES["meter_plate_unreadable"],
            promptId="hint.meter_plate_unreadable",
            retake="meter_close",
        )
    if meter.status in {"prefill", "choose", "single_reader"}:
        values = [item.value for item in meter.candidates][:3]
        preselected = meter.value if meter.status in {"prefill", "single_reader"} else None
        return HintData(
            code="meter_number_confirm",
            message=_MESSAGES["meter_number_confirm"],
            promptId="hint.meter_number_confirm",
            retake=None,
            confirm=ConfirmPrompt(
                imageId=result.still.still_id,
                fieldId="meter_number",
                mode="pick",
                candidates=values,
                preselected=preselected,
                allowNone=True,
            ),
        )
    return None


def _panel_hint(result: StillResult, panel: PanelRead) -> HintData | None:
    if panel.legibility == "poor" and not panel.brand and panel.hazard != "confirmed":
        return HintData(
            code="panel_label_unreadable",
            message=_MESSAGES["panel_label_unreadable"],
            promptId="hint.panel_label_unreadable",
            retake="panel_open",
        )
    return None


def _type_confirm(result: StillResult) -> HintData:
    """SD-51: an unreadable plate is `mode: type` with no candidates, plus the retake hint."""
    return HintData(
        code="meter_number_confirm",
        message=_MESSAGES["meter_number_confirm"],
        promptId="hint.meter_number_confirm",
        confirm=ConfirmPrompt(
            imageId=result.still.still_id,
            fieldId="meter_number",
            mode="type",
            candidates=[],
            preselected=None,
            allowNone=True,
        ),
    )


def hints_for(result: StillResult) -> list[HintData]:
    found: list[HintData] = []
    if result.meter is not None and result.still.purpose.startswith("meter"):
        hint = _meter_hint(result, result.meter)
        if hint is not None:
            found.append(hint)
        if result.meter.status == "unreadable":
            found.append(_type_confirm(result))
    panel_purpose = result.still.purpose.startswith("panel") or result.still.purpose == "main_disconnect"
    if result.panel is not None and panel_purpose:
        hint = _panel_hint(result, result.panel)
        if hint is not None:
            found.append(hint)
    return found
