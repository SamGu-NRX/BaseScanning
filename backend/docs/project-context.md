# Base Hackathon: project context

## Goal

Automate Base Power's battery-eligibility site survey: from customer photos of a house exterior, decide eligibility in one capture session with no follow-up photo requests.

## Team split (divide and conquer)

- **iOS team (not us):** guides the homeowner through capture on the iPhone and sends a packet of photos plus all useful iPhone/ARKit metadata (poses, intrinsics, gravity, IMU, compass, GPS, optional LiDAR depth, taps).
- **Our team:** server-side processing only. Packet in, then a metric 3D asset or point cloud, scene understanding, and evaluation of the eligibility criteria, plus the views still needed back to the iOS team.
- We must not be blocked by the iOS team: use online house-exterior photos and generated metadata until real packets arrive.

## Working principles

- Real research findings over simulated tests. Unit tests that assert static expected outputs are low value. Value work that builds 3D models or point clouds from real images and evaluates the criteria against those assets.
- Breadth first across candidate paths (traditional reconstruction, feed-forward models, world models), then depth on the paths with the highest return.
- Maximize asynchronous, parallel agent work.
- Agent model policy (Hunter, 2026-09-26): Fable 5.1 Max (`claude-fable-5-1-thinking-max`) for anything a builder reads as its spec — master plan, per-workstream subplans, contracts, architecture reviews such as the SMP review; Opus 5.5 Medium (`claude-opus-5-5-medium`) for research and experiment agents to save cost; Grok 4.7 for building, PR monitoring, and infra execution.
- Reuse real findings and code from the house-scanning PRs; ignore slop.
- Target device is a regular (non-Pro, no LiDAR) iPhone with iPhone-quality photos; LiDAR is a bonus, not an assumption.
- Two flows: a rich one (guided app streams many ARKit frames) and, of most interest, a weak one relying on up to about 60 ordinary regular-iPhone photos.
- LiDAR-first: when the phone has LiDAR, use it by default (object placement, scale, coverage, dense geometry); fall back to image-only paths when it doesn't.
- Vision models: GPT-5.5 plus Grok 4.7 (no Anthropic models in the pipeline).
- Processing must be fast; GPU compute runs on Modal for now (GCP is the likely production home, so keep the code container-portable).
- GCP layout (Hunter, 2026-09-26): reuse as much of the SMP shared prod substrate as possible (Terraform state bucket `smp-substrate-tfstate-prod` with prefix `house-scanning/<env>`, the GitHub Actions WIF pool, Artifact Registry and shared logging at migration); project-specific resources live in a new `house-scanning` GCP project under the same billing account whenever a separate project adds no significant cost (capture bucket, runtime service account, Modal OIDC WIF provider, budget; Cloud Run, Pub/Sub and Cloud SQL Postgres at migration). SMP practices adopted per `docs/smp-adoption-review.md`; factory, Spanner/Redis baseline, WorkOS, Stripe and the edge stack are skipped.
- No customer tools: never require the homeowner to use a tape measure or similar; capture must be low-friction.
- A simplified single-wall map is not enough to evaluate the criteria; houses differ (corners, obstacles, overhead, ground out to 12–14 ft).

## Key references

- Repo: https://github.com/SamGu-NRX/house-scanning
- Notion: https://app.notion.com/p/huntertcarver/Base-Power-Hackathon-3e7071360df8807caae9e2943a93ebdf
- [Server pipeline plan](/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/docs/server-pipeline-plan.md)
- Hunter's overnight research: PR #18 (research handoff)
