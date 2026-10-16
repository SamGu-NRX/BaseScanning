"""One SfM problem per epoch. Tiny epochs fold into the anchor without priors."""

from __future__ import annotations

from dataclasses import dataclass

from server.poses.packet_io import Frame
from server.poses.params import PoseParams


@dataclass(frozen=True)
class EpochJob:
    epoch: str
    frames: list[Frame]
    prior_names: frozenset[str]


def anchor_epoch(frames: list[Frame], meter_ids: list[str]) -> str:
    by_id = {frame.id: frame for frame in frames}
    for fid in meter_ids:
        frame = by_id.get(fid)
        if frame is not None and frame.epoch:
            return frame.epoch
    counts: dict[str, int] = {}
    for frame in frames:
        if frame.kind == "keyframe" and frame.epoch:
            counts[frame.epoch] = counts.get(frame.epoch, 0) + 1
    if not counts:
        return frames[0].epoch if frames else ""
    return max(counts, key=lambda key: counts[key])


def epoch_jobs(frames: list[Frame], *, meter_ids: list[str], params: PoseParams) -> list[EpochJob]:
    """Groups that may be paired. Pairs must not cross these groups.

    An epoch with fewer than ``min_epoch_frames`` keyframes is appended to the
    anchor job and those frames get no position prior.
    """
    anchor = anchor_epoch(frames, meter_ids)
    buckets: dict[str, list[Frame]] = {}
    for frame in frames:
        buckets.setdefault(frame.epoch or anchor, []).append(frame)
    jobs: dict[str, list[Frame]] = {}
    folded: dict[str, list[Frame]] = {}
    for epoch, group in buckets.items():
        keyframes = [frame for frame in group if frame.kind == "keyframe"]
        if epoch != anchor and len(keyframes) < params.min_epoch_frames:
            folded.setdefault(anchor, []).extend(group)
        else:
            jobs.setdefault(epoch, []).extend(group)
    if anchor not in jobs:
        jobs[anchor] = []
    jobs[anchor].extend(folded.get(anchor, []))
    out: list[EpochJob] = []
    for epoch, group in jobs.items():
        if not group:
            continue
        prior = frozenset(
            frame.name
            for frame in group
            if frame.epoch == epoch and frame.tracking == "normal" and frame.pose is not None
        )
        out.append(EpochJob(epoch=epoch, frames=group, prior_names=prior))
    return out
