# UI Primitives

*The shared components that power the Aegis UI — extracted during the 2026-05 audit pass so that the eight settings pages and seven layout shells stay structurally consistent without copy-pasting markup.*

The UI is a Vite + React + Tailwind app (no shadcn, no Next.js). Reusable components live under `ui/src/components/`, split into three lanes:

| Folder | Purpose | Examples |
|---|---|---|
| `Common/` | Generic widgets reusable across any page | `Button`, `Card`, `Modal`, `Toast`, `DataTable`, `ConfirmDialog`, `KeyboardCheatsheet`, `ConnectorPrimitives` |
| `Layout/` | App shell + cross-page navigation | `AgentScopePicker`, `Sidebar`, `Topbar`, `MainLayout` |
| `Charts/`, `Pages/<feature>/` | Feature-scoped components | `RiskTrendChart`, `FlightStepRow`, `DiffViewer` |

Two of these — `ConnectorPrimitives` and `AgentScopePicker` — are the audit pass's biggest unifications. They replaced six near-identical implementations with two shared modules.

## `ConnectorPrimitives` — settings cards

Source: `ui/src/components/Common/ConnectorPrimitives.jsx` (143 LOC). Extraction commit: `4f92665 audit(B3-redo): extract ConnectorPrimitives for SSO/SIEM/Webhook settings`.

Three settings pages had grown their own copies of the same structural shapes: a "secret input with show/hide toggle", a "status badge", and an "integration card" with header / body / actions. The shared module exposes them as named exports:

| Export | Used by | Notes |
|---|---|---|
| `SecretInput` | SSO (client secret), SIEM (API token), Webhook (signing secret) | Two modes — single-line input with eye-toggle, or `rows≥1` textarea with masked dots-fill for SAML certs |
| `StatusBadge` | All three | Accepts `state` ∈ `connected / disconnected / error / pending` and renders a coloured pill |
| `IntegrationCard` | All three | Card wrapper with optional accent colour, header slot, and right-aligned action area |

Each consumer page now imports these and renders page-specific bits inline. The net diff was −152 LOC across pages, +143 LOC shared.

Adding a new connector page (e.g., a future "Slack settings" surface) should follow the same shape: import the three primitives, render the card body inline, pass colour/state via props. Resist the urge to extend the shared module with page-specific code — keep it the structural skeleton.

## `AgentScopePicker` — scope selector

Source: `ui/src/components/Layout/AgentScopePicker.jsx`. Extraction commit: `00cf09d audit(B2-redo): extract AgentScopePicker shared between Sidebar + Topbar`.

The agent-scope dropdown — "All agents / Specific agent / Tenant scope" — appeared in two places: the sidebar collapsed-view picker and the topbar wide-view picker. Two copies meant two places to update when the scope contract changed (it changed twice during sprint 5). The extracted module owns the dropdown shape, keyboard a11y, and the localStorage persistence; the layout components render it inside their own chrome.

Both consumers (`Sidebar.jsx`, `Topbar.jsx`) pass the current scope and a `setScope` callback. The component is layout-agnostic.

## Other Common primitives worth knowing

These predate the audit pass but show up in every page:

| Component | Used by | Notes |
|---|---|---|
| `Button` | Every page | Variants: `primary / secondary / danger / ghost`. `loading` prop renders an inline spinner |
| `Modal` | Confirm dialogs, detail drawers, kill-switch engage confirmation | Portal-rendered with focus trap + scroll lock. z-index sits at 60 in the canonical hierarchy (toast 80 / modal 60 / sidebar-mobile 50 / navbar 40 / sidebar-desktop 30) |
| `Card`, `PageShell`, `PageSection`, `SectionHeader`, `EmptyState` | Layout primitives for all pages | Built during the modal-layout hardening sprint (2026-05-15) |
| `DataTable` | Agents, Incidents, Audit Trail, RBAC | Headless table — pass columns + rows, the component handles sort and selection |
| `Toast`, `NotificationCenter` | Every mutation surface | Pub-sub via `useAuth().addToast(msg, kind)` |
| `KeyboardCheatsheet` | App-global `?` keypress | Reads from the `useHotkeys` registry and renders the live binding table |
| `CommandPalette` | App-global `Cmd-K` | Fuzzy-matches over `Navigation.flat` so new pages auto-register |
| `IncidentOverlay` | Investigation drawer | Pairs with `InvestigationLayout` 3-pane shell |
| `ConfirmDialog` | Destructive actions | Built on top of `Modal` with the standard "type the resource name to confirm" affordance |
| `ErrorBoundary` | App root | Catches unhandled React errors so a downstream bug doesn't blank the whole shell |
| `SkeletonLoader` | Pages that depend on slow endpoints | Renders as a placeholder while the page's primary fetch is pending |

## What got deleted in the audit pass

Commit `90dc17f audit: delete 8 dead UI primitives (Sprint-4 leftovers)` removed eight components that had no remaining consumers after pages were rewritten. The remaining tree under `Common/` is intentionally small — every primitive listed above has at least one current consumer; nothing is kept "just in case".

The same audit also dropped five unused npm packages (`7523efd`) and four unused imports (`38d4345`). Bundle size went down ~12 KB gzipped as a result.

## Conventions

- **No inline tailwind classes longer than ~80 chars without a comment** explaining the visual intent. The intent of a `bg-white/[0.04] border border-white/[0.06] rounded-2xl` stack should be obvious from a glance, but five-line class chains get hard to scan.
- **Every interactive element has an `aria-label` or wraps a labeled child.** Lucide icons are `aria-hidden="true"`; their containing buttons carry the accessible name.
- **Modals use the portal pattern** (rendered into `document.body`) — never inline inside a transformed parent, because a `transform` creates a stacking context that traps fixed-position children. This is the "modal-under-navbar" bug from the 2026-05-15 layout hardening sprint.
- **Sidebar / Topbar / Modal z-index is canonical** — toast 80 > modal 60 > sidebar-mobile 50 > navbar 40 > sidebar-desktop 30. New overlays must fit this hierarchy.

## Next

- [UI Map](../ui/_index.md) — page-by-page index of which primitives each consumer uses
- [System Overview](system-overview.md) — where the React app fits in the deployment
- [Gateway](../services/gateway.md) — the backend the UI's `api.js` talks to
