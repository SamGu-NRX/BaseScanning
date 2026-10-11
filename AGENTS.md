# Agent guide

A homeowner scans the wall around their electric meter with an iPhone. The server decides whether a Base Power battery fits and where, and the app shows the spot in AR. [README.md](README.md) maps the repository, and [docs/00-overview.md](docs/00-overview.md) holds the plan, the decisions and the evidence.

## Who owns what

- **Client team.** Sam, working with AI agents, owns the iOS app in `ios/` and the capture packet it sends. Aiden films video and gathers sample datasets.
- **Server team.** Hunter, working with his own agents, owns everything after the packet: the 3D model and the rule checks, in `server/` and `recon/`.

Most agent work arrives from a manager session that holds the plan's history and the discussions behind it. Report results, blockers and open decisions back to that manager, and let it settle questions about the plan or the product.

Edit the other team's files only with that team's OK. Talk to the author before editing files that an open pull request also changes, because two writers on one file cost more time than asking does.

## Where to look

A component's README wins over the docs. Files marked with a pull request exist only on that branch until it merges.

| Task | Read |
| --- | --- |
| The iOS app | `ios/README.md` (PR #10 for guided capture) |
| The capture packet | `packet/README.md` (PR #22) |
| The rules engine, API and scene contract | `server/README.md` (PR #11), especially "What settles each check" |
| Photos to a 3D model | `recon/HANDOFF.md` (PR #20) |
| Accuracy evals and the field test | `experiments/evals/README.md` (PR #12), and the first phone run in `experiments/device-field-test/README.md` (PR #23) |
| Measurement conventions the code relies on | `docs/00-overview.md`, section "Conventions the code relies on" |
| Public rule values, code citations, model and imagery licenses | `docs/04-prior-art-and-codes.md` |
| The live guided-survey design | `docs/05-live-guided-survey-hld.md` |
| Branches, CI checks and TestFlight | `CONTRIBUTING.md` |

## Rules

- **Keep Base's materials in `private/`, which git ignores.** The repository is public, so tracked files carry none of that material: no quotes, summaries, prompt text, prompt names, output field names or internal thresholds. Team notes go in `private/internal-notes.md`. Real captures, photos of homes and dataset images go in `captures/`, `fixtures/real/` or `data/`, which git also ignores.
- **Make placement decisions in deterministic code.** Models build geometry and recognize things, and plain code passes or fails each check, so every answer traces to a rule and a measurement. Clearance numbers live in rules files with their sources, never in code.
- **Treat unseen as unsure, never as clear.** A gap in the scan could hide a gas meter. When a check depends on an area nobody observed, return UNSURE and name the view that would settle it.
- **Use StratMap or NAIP for aerial imagery.** Google's and Mapbox's terms forbid running ML on their imagery, and Esri allows it only inside ArcGIS for non-commercial use.
- **Place AR results relative to the meter's anchor, with `.gravity` world alignment.** ARKit corrects anchors as tracking improves, so a result tied to the meter moves with it while raw world coordinates drift. `.gravityAndHeading` depends on the compass, which is unreliable next to a house.
- **Rotate the intrinsics whenever you rotate a camera image.** Camera images are landscape sensor images, and the intrinsics match that orientation. A rotated image with unrotated intrinsics produces wrong 3D geometry without any error.
- **Use LiDAR when present, and never require it.** Most homeowners' phones lack LiDAR, so every check must also work from the camera and the phone's poses.

## Writing, planning and review

The repository ships shared skills in `.agents/skills/`, which Codex reads directly. `.claude/skills/` links to the same folders for Claude Code. Agents without skill support can read each `SKILL.md` as a plain guide.

- `unslop` for any prose, and `technical-writing` for docs and READMEs.
- `pr-writing` for pull request titles and descriptions.
- `prompt-writing` for AGENTS.md, skills and prompts that brief another agent.
- `efficient-implementation-plans` for implementation plans.
- `deslop` for reviewing or cleaning a diff.

A user's instruction outranks a skill.

## Working in the repository

- `make check` runs every suite on the branch. `make ios`, `make server`, `make web`, `make scoring`, `make measure-lab`, `make evals`, `make recon` and `make meter-closeup` run one each.
- Branch from `main`, keep one writer per branch, and open a pull request. People merge, and agents never push to `main` or merge, so a person sees every change before it lands.
- Several agents share one Mac. Exit code 137 means the system killed the process, usually for memory. Find the large allocation before rerunning, because one runaway process can freeze the whole machine.
- Put `DEVELOPMENT_TEAM` in `ios/Config/Local.xcconfig` (copy `Local.xcconfig.example`), not in Xcode's Signing & Capabilities pane. The pane writes into `project.pbxproj`, and CI fails on that drift.
- After editing `ios/project.yml`, run `make ios-project` (it needs XcodeGen 2.46.0) and commit the regenerated project. CI regenerates it and fails on any difference.
- `sites/landing` is a submodule. Change the landing page in its own repository.

<!-- agent-skills:start -->
## Agent skills

Before starting work, read the skills below that apply to the task; each SKILL.md says when to use it. They are Sam's working playbooks and Lauren Tan's pstack (`pstack-*`), chosen for this repo. Obvious Autobuild loads them from `.obvious/skills/` on the default branch.

- [`anti-ui-slop`](.obvious/skills/anti-ui-slop/SKILL.md): Run the Uizze reference-guided design playbook or UI finish check when explicitly requested
- [`blast-radius`](.obvious/skills/blast-radius/SKILL.md): Assess what a change could break beyond its diff
- [`pstack-architect`](.obvious/skills/pstack-architect/SKILL.md): Sketch types, signatures, and module structure before code, then stay in the loop while implementation fills in
- [`pstack-benchmark-checklist`](.obvious/skills/pstack-benchmark-checklist/SKILL.md): Vet a perf measurement (limiter, tuning, limits, errors, repeatability, relevance, and whether the work happened) before you report or act on it
- [`pstack-create-verification-skill`](.obvious/skills/pstack-create-verification-skill/SKILL.md): Generate a project-local verification skill that drives your app the way a user does — any language, framework, or platform
- [`pstack-principle-boundary-discipline`](.obvious/skills/pstack-principle-boundary-discipline/SKILL.md): Apply when wiring validation, error handling, or framework adapters
- [`pstack-principle-explain-the-number`](.obvious/skills/pstack-principle-explain-the-number/SKILL.md): Apply before you trust, report, or act on a number you measured: a speedup, a regression, a throughput, a latency, or an eval result *(needed by `pstack/benchmark-checklist`)*
- [`pstack-principle-prove-it-works`](.obvious/skills/pstack-principle-prove-it-works/SKILL.md): Apply after completing a task, before declaring done *(needed by `pstack/principle-explain-the-number`)*
- [`pstack-principle-test-behavior-not-implementation`](.obvious/skills/pstack-principle-test-behavior-not-implementation/SKILL.md): Apply when you write, change, or keep a test
- [`pstack-principle-type-system-discipline`](.obvious/skills/pstack-principle-type-system-discipline/SKILL.md): Apply when designing types, reviewing a function signature, or writing code in any statically-typed language
- [`pstack-technical-writing`](.obvious/skills/pstack-technical-writing/SKILL.md): Layered technical-writing standard: Diátaxis structure, Google developer style sentences, STE instruction rules, Global English syntax
- [`pstack-arena`](.obvious/skills/pstack-arena/SKILL.md): Spawn N parallel candidates at the same task, pick a base, graft the strongest parts of the losers into it *(needed by `pstack/architect`)*
<!-- agent-skills:end -->
