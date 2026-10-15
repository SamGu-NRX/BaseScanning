"""Meter block mapping. Values in the samples are fake."""

import json
import logging

from server.result.meter import meter_block
from server.result.tests.support import DATA, reads_dict

SAMPLES = {
    "reads.auto_fill.json": "auto_filled",
    "reads.prefill.json": "prefill_confirm",
    "reads.choose.json": "prefill_confirm",
    "reads.unreadable.json": "unreadable",
    "reads.disabled.json": "not_captured",
}


def test_five_reads_samples():
    for name, status in SAMPLES.items():
        doc = json.loads((DATA / name).read_text())
        block = meter_block(doc, autofill="barcode")
        assert block.status == status


def test_agreement_never_auto_fills():
    doc = reads_dict("prefill", "111111111", agree=True)
    assert meter_block(doc, autofill="barcode").status == "prefill_confirm"
    assert meter_block(doc, autofill="barcode").readersAgree is True


def test_never_downgrades_auto_fill():
    doc = json.loads((DATA / "reads.auto_fill.json").read_text())
    assert meter_block(doc, autofill="never").status == "prefill_confirm"


def test_log_has_status_and_not_the_value(caplog):
    doc = json.loads((DATA / "reads.auto_fill.json").read_text())
    with caplog.at_level(logging.INFO, logger="server.result.meter"):
        meter_block(doc, autofill="barcode")
    text = " ".join(caplog.messages)
    assert "000000000" not in text
    assert "111111111" not in text
    assert "auto_filled" in text
    assert "value_sha256_8=" in text


def test_candidates_capped_and_evidence_still():
    doc = reads_dict("choose", "111111111", candidates=["111111111", "000000000", "222222222", "333333333"])
    block = meter_block(doc, autofill="barcode")
    assert len(block.candidates) <= 3
    assert block.evidence is not None
    assert block.evidence.image == "s01"
    assert block.clean is True
