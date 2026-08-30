# Shadow Personal Context OS

## Product direction

Shadow is a private personal context operating layer. Domain applications remain the
systems of record; Shadow Nexus becomes the daily surface for attention, action,
search, context and receipts; Shadow App provides the native shell and device
capabilities. Nexus is therefore more than an application launcher, but it is not a
replacement database for Health, Ledger or future domains.

The home surface optimizes for three questions:

1. What needs my attention now?
2. What can I do without opening another application?
3. What did the Agent do, and can I verify it?

## Ownership model

| Layer | Owns | Must not own |
| --- | --- | --- |
| Domain application | Canonical facts, validation, history, domain workflows | Cross-domain navigation and orchestration |
| Shadow Platform | Contracts, capability/risk policy, profile compilation, identity and shared infrastructure | User-facing copies of domain fact tables |
| Shadow Nexus | Read-only projections, attention ranking, quick actions, context, activity and receipts | Canonical health, finance or content records |
| Shadow App | Native shell, authentication hand-off, notifications, capture/share, device permissions | A second web product or hidden business logic |

## Core projections

### Entity Registry

An entity is a stable, domain-owned concept exposed through the domain's primary
summary surface in P0. Examples include `health.weight` and `ledger.net-spending`. The compiled
registry records identity, class, value/detail pointers, sensitivity, freshness and
related quick actions. Nexus resolves those pointers against the existing summary
response; it does not issue an additional query or persist the value as a new fact.

Entity identifiers are stable within one domain. The globally stable identifier is
`<domain>.<entity-id>`. A missing value is `unavailable`, an old declared observation
is `stale`, and a current value is `fresh`. When a domain is offline, its declared
entities remain discoverable but unavailable.

Sensitivity is part of the contract, not a CSS concern:

- `public`: safe for broad presentation.
- `personal`: ordinary personal context.
- `sensitive`: health, finance and similarly private values.
- `restricted`: values that should require an explicit future reveal policy.

P0 renders sensitivity labels and never copies entity values into the Nexus activity
ledger. P1 must add per-surface reveal and notification policies before restricted
entities are used outside the foreground home surface.

### Activity and Receipt Ledger

Nexus normalizes Proposal state into `pending`, `completed`, `rejected`, `failed` and
`prohibited` activities. It exposes summary, actor, risk, time and whether a receipt
exists. Structured Proposal fields stay out of the activity projection.

This is an execution ledger, not an event-sourced replacement for domain history.
The domain receipt remains the authority for a completed write. Reconciliation may
repair a missing receipt; it must never invent a domain fact.

### Trust Center

The trust model is exception-driven:

- L0–L2: execute automatically after domain validation and retain a receipt.
- L3: require an explicit user review; signed confirmation can bind actor, resource
  and arguments.
- L4: prohibit execution through Nexus.
- Any automatic failure: stop and promote to review. Do not hide it or repeatedly
  write without a new idempotent attempt.

The Trust Center reports automatic, manual, pending, failed and prohibited outcomes
globally and by domain. It is an observability surface, not a global permission
override.

## Adaptive Today

The Today surface is ordered by utility rather than application boundaries:

1. **Needs you**: failed automation, high-impact reviews and policy exceptions.
2. **Now**: stable entities, stale state first, with related quick actions.
3. **Common actions**: bounded operations declared by domains.
4. **Suggestions**: evidence-backed and dismissible proactive work.
5. **Recent activity**: concise execution history with receipt state.
6. **Domain cards and applications**: deeper context and full workflows.

This avoids the “fancy bookmarks” failure mode. A healthy home surface reduces the
need to visit every application and does not require the user to continuously watch
a wall of metrics.

## Contract and security invariants

- Every entity source references one declared summary surface.
- Every related action references one declared quick-action surface.
- Platform validates references at build time; Nexus validates the compiled runtime
  again before use.
- Domain credentials are runtime-only and never projected to the client.
- Nexus only resolves explicitly declared JSON pointers.
- Quick actions continue through domain validation, risk policy, idempotency and
  receipt handling.
- Cross-domain intelligence consumes Context Packs and stable references, not direct
  joins across private databases.

## Evolution roadmap

### P0 — implemented foundation

- Entity Registry contract and compiled runtime projection.
- Health and Ledger entity declarations.
- Adaptive Today, Activity Ledger and Trust Center.
- Existing automatic L0–L2 execution and exception review retained.

### P1 — proactive but quiet (implemented)

- Daily/weekly briefs derived from entity freshness, suggestions and activity.
- Native App notifications with quiet hours, deduplication and sensitivity-safe text.
- User controls for cadence, quiet hours, notifications and sensitive previews.
- Entity-level trend and attention rules declared by domains.

### P2 — governed memory and ecosystem (implemented foundation)

- Durable memory with provenance, expiry, versioned correction and forget operations.
- Connector/plugin SDK based on capability manifests, entity/surface declarations
  and sandboxed UI contributions.
- MCP Apps exposure for reusable interactive domain tools where the host supports it.
- Automation recipes with dry-run, replay protection, receipts and rollback hints.

### P3 — local-first continuity (client and protocol implemented)

- Android Keystore-encrypted offline action queue and encrypted multi-device sync envelope.
- Conflict semantics appropriate to each domain rather than generic last-write-wins.
- Optional household/shared spaces with explicit ownership and visibility boundaries.
- Portable export of entity projections, activity metadata, memories and domain references.

The repository now contains the client behavior and versioned contracts for all stages. A remote encrypted
sync relay and real household membership still require separately deployed infrastructure and enrolled devices;
the application does not pretend those external nodes exist before they are provisioned.

## Review checklist for new domains

1. Is there one authoritative domain owner for every exposed fact?
2. Can the common action be bounded, validated, idempotent and receipted?
3. Are entity identity, sensitivity and freshness explicit?
4. Does the summary expose only what Today needs?
5. Are high-impact and prohibited operations correctly classified?
6. Can failures become reviewable exceptions without losing context?
7. Does the domain still work independently when Nexus is unavailable?
