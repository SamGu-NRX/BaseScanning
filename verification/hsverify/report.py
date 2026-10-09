"""Static HTML and Markdown pages for run folders. No external assets, so a folder opens offline."""

from __future__ import annotations

import html
from pathlib import Path

_CSS = """
:root { color-scheme: light dark; --fg: #1d1d1f; --muted: #6e6e73; --line: #d2d2d7;
  --bad: #b3261e; --warn: #8a5a00; --ok: #1b6e3a; --card: #f5f5f7; }
@media (prefers-color-scheme: dark) { :root { --fg: #f5f5f7; --muted: #a1a1a6;
  --line: #3a3a3c; --card: #1c1c1e; --bad: #ff8a80; --warn: #ffcc66; --ok: #7ee2a0; } }
body { font: 15px/1.45 -apple-system, BlinkMacSystemFont, sans-serif; color: var(--fg);
  margin: 32px auto; max-width: 1180px; padding: 0 20px; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 17px; margin: 28px 0 8px; }
.meta { color: var(--muted); font-size: 13px; }
code { font: 12.5px ui-monospace, SFMono-Regular, monospace; }
.problems li { color: var(--bad); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 18px; }
figure { margin: 0; }
figure img { width: 100%; border-radius: 18px; border: 1px solid var(--line); }
figcaption { font-size: 13px; margin-top: 6px; } figcaption b { font-weight: 600; }
.tag { font-size: 11px; padding: 1px 6px; border-radius: 6px; background: var(--card);
  color: var(--warn); margin-left: 4px; }
table { border-collapse: collapse; font-size: 13px; } td, th { text-align: left;
  padding: 3px 12px 3px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
.ok { color: var(--ok); } .no { color: var(--bad); }
"""


def _esc(value: object) -> str:
    return html.escape(str(value))


def _yes(value: bool) -> str:
    return '<span class="ok">yes</span>' if value else '<span class="no">no</span>'


def _cell(text: object) -> str:
    """Markdown-safe text, the same rule as scoreboard._cell: problem messages come from
    exceptions and app logs, so a pipe or newline in them cannot forge rows or bullets."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def write_sim_report(out: Path, data: dict) -> None:
    build = data["build"]
    states = data["states"]
    parts = [
        "<!doctype html><meta charset=utf-8>",
        f"<title>Simulator run {_esc(data['ref'])} {_esc(data['sha'][:8])}</title>",
        f"<style>{_CSS}</style>",
        f"<h1>Simulator run: <code>{_esc(data['ref'])}</code> at "
        f"<code>{_esc(data['sha'][:12])}</code></h1>",
        f"<p class=meta>{_esc(data['started_at'])} · {_esc(data['device'].get('runtime', ''))}"
        f" · {_esc(data['device'].get('appearance', ''))}"
        f" · text size {_esc(data['device'].get('content_size') or 'default')}"
        f" · launch args <code>{_esc(' '.join(data['launch_arguments']) or '(none)')}</code></p>",
        f"<p>{len(states)} states. Ended: {_esc(data['end_reason'])}. Build "
        f"{'passed' if build.get('ok') else 'failed'} in {_esc(build.get('seconds', '?'))} s with "
        f"{len(build.get('warnings', []))} warnings.</p>",
    ]
    if data["problems"]:
        parts.append("<h2>Problems</h2><ul class=problems>")
        parts += [f"<li>{_esc(p)}</li>" for p in data["problems"]]
        parts.append("</ul>")
    parts.append("<h2>States</h2><div class=grid>")
    for s in states:
        tag = (
            '<span class=tag title="replaced before it settled">transient</span>'
            if s["transient"]
            else ""
        )
        parts.append(
            f"<figure><a href='{_esc(s['screenshot'])}'><img loading=lazy "
            f"src='{_esc(s['screenshot'])}' alt='{_esc(s['state'])}'></a>"
            f"<figcaption><b>{s['index']}. {_esc(s['state'])}</b>{tag}<br>"
            f"<span class=meta>+{s['seconds_after_launch']} s</span></figcaption></figure>"
        )
    if data.get("final_screenshot"):
        parts.append(
            f"<figure><img src='{_esc(data['final_screenshot'])}' alt='final screen'>"
            "<figcaption><b>Final screen</b></figcaption></figure>"
        )
    parts.append("</div><h2>Contract C4 in source</h2><table>")
    parts += [
        f"<tr><td>{_esc(k)}</td><td>{_yes(v)}</td></tr>" for k, v in data["static_c4"].items()
    ]
    parts.append("</table>")
    if build.get("warnings") or build.get("errors"):
        parts.append("<h2>Build diagnostics</h2><ul>")
        parts += [
            f"<li><code>{_esc(x)}</code></li>"
            for x in build.get("errors", []) + build.get("warnings", [])
        ]
        parts.append("</ul>")
    parts.append(
        "<p class=meta>Files: <a href=report.json>report.json</a> · "
        "<a href=build.log>build.log</a> · <a href=state.ndjson>state.ndjson</a> · "
        "<a href=app-stdout.log>app-stdout.log</a> · <a href=app-stderr.log>app-stderr.log</a>"
        "</p>"
    )
    (out / "index.html").write_text("\n".join(parts) + "\n", encoding="utf-8")

    lines = [
        f"# Simulator run: `{data['ref']}` at `{data['sha'][:12]}`",
        "",
        f"- Build: {'passed' if build.get('ok') else 'failed'}, {build.get('seconds', '?')} s, "
        f"{len(build.get('warnings', []))} warnings",
        f"- Launch arguments: `{' '.join(data['launch_arguments']) or '(none)'}`",
        f"- States: {len(states)}; ended: {data['end_reason']}",
    ]
    lines += [f"- Problem: {_cell(p)}" for p in data["problems"]]
    lines += ["", "| # | State | +s | Screenshot |", "| --- | --- | --- | --- |"]
    lines += [
        f"| {s['index']} | {s['state']}{' (transient)' if s['transient'] else ''} | "
        f"{s['seconds_after_launch']} | {s['screenshot']} |"
        for s in states
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
