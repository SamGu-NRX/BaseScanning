#!/usr/bin/env python3
"""Copy a UI test result bundle to a stable folder before CI uploads it, and say what is missing.

  snapshot-xcresult.py <bundle> <out-folder>

Why: when the job's time cap cancels xcodebuild, the bundle is left half-written, and files under
its `Staging/` folder go away while they are being read. Run 37221532164 (#216 at 6fa43b9b) lost
its whole journey artifact that way: upload-artifact stopped at the first vanished file (ENOENT on
`Staging/1_Test/Diagnostics/...`) and kept nothing, screenshots of the failed audit included.

What it does: copies every file of `<bundle>` into `<out-folder>/<bundle name>`, then writes
`<out-folder>/SNAPSHOT.txt` saying "complete" or "incomplete" and listing each path that vanished.
Only a file or folder under the bundle's top-level `Staging/` may vanish; that is the transient
state of a cancelled run. Any other error stops with exit 1 and names the path, so a broken bundle
is never passed off as a partial one. A missing bundle (the UI step never started) writes nothing
and exits 0, as the upload's `if-no-files-found: ignore` did. The job's own result, a cancellation
included, is not changed by this step.
"""
import os
import pathlib
import shutil
import sys

EPHEMERAL = "Staging"


def is_ephemeral(relative):
    parts = pathlib.PurePath(relative).parts
    return bool(parts) and parts[0] == EPHEMERAL


def snapshot(bundle, out, copy=shutil.copy2):
    """Copies `bundle` into `out`. Returns (copied, vanished): a file count and the relative paths
    under Staging/ that disappeared mid-copy. Raises on any other error."""
    bundle = pathlib.Path(bundle)
    out = pathlib.Path(out)
    copied = 0
    vanished = []
    walk_errors = []
    for root, dirs, files in os.walk(bundle, onerror=walk_errors.append):
        rel_root = pathlib.Path(root).relative_to(bundle)
        (out / rel_root).mkdir(parents=True, exist_ok=True)
        for name in sorted(dirs):
            if os.path.islink(os.path.join(root, name)):
                os.symlink(os.readlink(os.path.join(root, name)), out / rel_root / name)
        for name in sorted(files):
            relative = rel_root / name
            try:
                copy(os.path.join(root, name), out / relative, follow_symlinks=False)
                copied += 1
            except FileNotFoundError:
                if not is_ephemeral(relative):
                    raise
                vanished.append(str(relative))
    for error in walk_errors:
        relative = pathlib.Path(error.filename).relative_to(bundle)
        if not (isinstance(error, FileNotFoundError) and is_ephemeral(relative)):
            raise error
        vanished.append(f"{relative}/")
    return copied, sorted(vanished)


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    bundle, folder = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    if not bundle.exists():
        print(f"no result bundle at {bundle}; nothing to keep")
        return 0
    try:
        copied, vanished = snapshot(bundle, folder / bundle.name)
    except OSError as error:
        print(f"::error::copying {bundle} failed outside its transient {EPHEMERAL}/ folder: {error}")
        return 1
    if vanished:
        report = [f"incomplete: copied {copied} files; {len(vanished)} entries under {EPHEMERAL}/ vanished while copying"]
        report += vanished
        print(f"::warning::The UI test results are incomplete: {len(vanished)} entries under {EPHEMERAL}/ vanished while copying (listed in SNAPSHOT.txt).")
    else:
        report = [f"complete: copied {copied} files"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SNAPSHOT.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(report[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
