# Three arrival checks for the capture packet validator

Three checks we propose for the server's validator, each catching a mistake that each part of a
packet can pass on its own. We read the server's spec and parts of its code but did not audit its
whole validator, so some of these may already exist there. They are described here in our own
words; nothing is copied from the server's private code. Each ran in the packet 1.1 validator (`t3/packet` `d5439cf`, `packet/validate.py`)
with a test that breaks it on purpose. Whether they reach the server as a pull request or stay a
proposal is Sam's and Hunter's call.

## 1. Keyframes lie on the pose stream

For each keyframe, find the `arkitPoses` row of the same epoch nearest its `timestamp`. It must be
within one frame of the stream's rate (1/60 s at 60 Hz), and its position within 1 cm of the
keyframe pose's translation.

**What it catches.** Keyframes and the pose stream written in different frames, or on different
clocks: for example, keyframe poses converted to another frame while the stream stays raw, or
timestamps taken from `Date` on one side and uptime on the other. Every other check passes such a
packet, because each part is valid on its own.

**Limits.** It compares positions only: a keyframe with the right position and a wrong rotation
(an inverted transform, other camera axes) passes. A rotation tolerance needs a measured basis
first. Packet 1.1 had one world per packet, so matching within an epoch is part of the proposal,
not something 1.1 tested.

**Evidence.** On the ADVIO sample, 79 keyframes matched their stream rows exactly. Moving one
keyframe 10 cm, or its timestamp past the stream's end, fails the check (1.1 tests
`test_a_photo_off_its_trajectory` and `test_a_photo_between_trajectory_samples`).

## 2. Depth has its keyframe's shape

A depth map covers its keyframe's field of view at lower resolution, so:
- its `w / h` equals the image's within 1%, and it is no larger than the image;
- values are finite and non-negative, with 0 for no measurement, and at least 1% are measured;
- confidence values are at most 2.

**What it catches.** A depth map rotated 90° or cropped to another aspect (for example a portrait
map with a landscape image), which a byte-count check passes because `w × h` is unchanged. It
also catches NaN written for "no reading", and an all-zero map.

**Limits.** It checks shape, not alignment. A map flipped 180°, mirrored, or taken from another
keyframe with the same size passes. Proving alignment would need orientation and capture-link
fields the contract does not have.

**Evidence.** On ETH3D electro with laser-rendered depth, ETH3D's own 3-D points reprojected
through the packet's poses to a median 0.65 to 1.01 px, and the depth agreed with them to a median
0.2 to 0.6%. The aspect, NaN and confidence tests in 1.1's `tests/test_packet.py` each fail their
case.

## 3. The mesh file is what the manifest says

`geometry/mesh.ply` is a binary little-endian PLY. 0.4 does not fix its index and count types, so
this check proposes one layout as well:
- `float x, y, z` per vertex, and per face a `uchar` count followed by that many `int` indices,
  then `uchar classification` when `mesh.classified` is true. The property may be absent only
  when the flag is false, so a mesh cannot claim face classes without carrying them;
- the body length equals exactly what the vertex and face counts imply;
- vertex and face counts are non-negative, and every vertex coordinate is finite;
- every face is a triangle, and every index satisfies `0 <= index < vertex_count`;
- classification values are 0 to 7 (`ARMeshClassification`), and all 0 when `mesh.classified` is
  false.

**What it catches.** A truncated upload that still hashes as sent, a negative index or one past
the vertex list (which crashes or corrupts a reconstruction reader), `NaN` or infinite vertices, `double` coordinates or `uint` counts from a
different exporter, and classes written when classification was off, which cannot be told from
ARKit's own 0 ("none").

**Evidence.** 1.1's `tests/test_checks.py` breaks each of these in turn (index out of range,
class 8, a truncated body, a changed property type). The ARKit mesh has not yet been exercised
from a device.

## Hazards the 1.1 review found

Reviewers of the 1.1 validator (#22) found ways a packet passed that it should not. Each applies to
any validator that reads packets the same way:

- **Symlinks.** A folder packet whose file is a symlink to an identical file outside it passes
  a path-spelling check. Resolve each path and require it to stay inside the packet.
- **Duplicate zip entries.** Python's `ZipFile` reads the last entry of a repeated name, while
  other unzip tools take the first, so a bad first copy can hide behind a good second one. Refuse
  a zip with repeated names.
- **Truncated JPEGs.** Pillow's `Image.open` reads only the header, so format, size and EXIF can
  look right on a truncated file. Decode the pixels (`Image.load()`) before accepting it.
- **Malformed binaries.** Parsing a short mesh or depth body can raise before the length check
  runs. Report it as a problem, not an exception.
- **Out-of-range numbers.** `json.loads` accepts `NaN` and `Infinity` by default, and parses the
  valid JSON number `1e400` as infinity without calling `parse_constant`. It parses a 400-digit
  integer as an exact `int`, on which `math.isfinite` raises `OverflowError` rather than
  returning `False`. So check floats with `math.isfinite`, check integers against the range their
  field allows (a count, an index, a size) before any conversion to float, and report a value
  that fails either check, or cannot be converted, as a problem, not an exception.
- **Stream times outside the capture.** A stream with samples far outside the session's time span
  passed with only a warning. Decide whether that is an error.
