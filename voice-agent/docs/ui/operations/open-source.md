# Open Source

## What this page is for

The Open Source page is the project's landing surface inside the running platform. It mirrors the public `aegisagent.in/open-source` URL and describes Aegis as an Apache 2.0–licensed self-hostable platform. Visitors who reach the UI but aren't yet logged in (or who are tenants evaluating whether to keep using the hosted version vs self-hosting) read this page to understand the project's positioning.

It is the one operational page that is allowed to use the friendlier marketing tone — the rest of the GitBook is technical.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/open-source` (and `/pricing` retained as an alias for back-compat with older links).
- **Keyboard hint**: none.
- **Minimum role**: none. The page is reachable without authentication. The UI shell still requires login to render the sidebar, but the underlying SPA route allows public access. From a browser pointed directly at `https://dev.aegisagent.in/open-source`, the page loads without a token because the gateway's `/auth/*` flow is not required to render a static React page.

## What you see

- **Hero band** — title, one-line description ("Apache 2.0 · Self-hostable · 12 services"), GitHub repo link.
- **Six-number band** — 12 services, 24 containers, 11K+ decisions, 21ms p95, 0 violations, ~50ms hard-deny.
- **Six primitive cards** — the platform's six key concepts (Runtime Authorization, Cryptographic Audit, Kill Switch, Identity Graph, Flight Recorder, Behavioral Firewall) with one-paragraph explanations.
- **3-step quick-start** — clone, compose up, hit `/auth/token`.
- **8-layer architecture diagram** — Mermaid-style stack from Agent through Audit.
- **License / Self-host / Contribute cards** — Apache 2.0 details, the `git clone` one-liner, the CONTRIBUTING.md link.
- **20-feature checklist** — the full feature inventory.
- **BibTeX cite block** — for academic citations of the platform.
- **Closing CTA** — Star and Fork buttons linking to the GitHub repo.

## Backend calls

*None.* The Open Source page is purely static. No API calls, no service consumption.

This matters operationally: the page works even when the rest of the platform is degraded. Self-hosters evaluating Aegis can read the landing copy without authenticating.

## Auto-refresh & realtime

*Not applicable.* No data to refresh.

## Per-agent scoping

*Not applicable.* The page is tenant-agnostic — it describes the project, not the running instance.

## Empty states

*Not applicable.* The content is hard-coded in `ui/src/pages/Pricing.jsx`.

## Edge cases & known gotchas

- **Page redirects from `/pricing`**: both `/pricing` and `/open-source` route to the same component (`Pricing.jsx`) via the SPA router. The "Pricing" → "Open Source" rebrand was done by adding `/open-source` as the canonical path and keeping `/pricing` as an alias so old bookmarks still work.
- **GitHub link missing or stale**: the URL is the `GH_REPO` constant at the top of `ui/src/pages/Pricing.jsx`. Updating the repo URL means editing that one constant; the URL is referenced 4 times across the page.
- **Layout looks cramped**: the page uses `max-w-5xl` for documentation-grade reading measure. The right reading width is intentional; very wide screens leave whitespace on the sides.
- **Mermaid block doesn't render in the SPA**: the SPA does not render Mermaid; the architecture diagram is a static SVG. Mermaid rendering is only used in the published GitBook docs.
- **No GitBook docs link from this page yet**: the GitBook URL is added once the docs are published.

## Related docs

- [What is Aegis](../../introduction/what-is-aegis.md) — the GitBook equivalent of this page
- [Quickstart](../../introduction/quickstart.md) — the curl-based version of the 3-step quick-start
- [Deployment Topology](../../architecture/deployment-topology.md) — what a self-hosted install looks like

## Screenshot

![Open Source](../_screenshots/open-source.png)
