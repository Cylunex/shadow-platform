# Capability Conformance and Recovery Evidence

Shadow Platform now emits a machine-readable capability inventory for every immutable
deployment build. It is a control-plane view of compatibility and operating evidence;
it does not copy domain records, backup contents, request bodies, or user context into
Platform.

## Capability lifecycle

`shadow-capability-status.json` keeps five stages separate. A later stage never implies
that an earlier stage was checked:

| Stage | Meaning | Evidence owner |
| --- | --- | --- |
| `contract` | Plugin schemas, references, versions, risk, and semantic invariants validated | Platform compiler |
| `client` | A selected App, Nexus, or DSH projection was compiled against that contract | Platform compiler and client CI |
| `deployed` | The immutable release and required runtime configuration are present | Deployment automation |
| `observed` | A live health/conformance probe succeeded for the deployed instance | Deployment doctor or domain monitor |
| `restore-tested` | An isolated restore completed and contract, data, and health checks passed | Domain backup/restore automation |

The derived `maturity` is the highest consecutive passing stage. `failed` is explicit;
missing evidence remains `unknown`. Unselected capabilities stay in the inventory as
`not-selected`, so contract drift remains visible without claiming that the capability
runs in the deployment.

Capability references use this stable form:

```text
shadow://capabilities/<plugin-id>/<instance-id>/<capability-id>
```

They associate evidence across repositories without moving domain facts into Platform.

## Cross-project conformance gate

Domain and client repositories emit `shadow.conformance-evidence.v1` files. Every file
is bound to an exact `deployment_id` and `build_id`; evidence for another release is
rejected. Passing evidence must arrive in lifecycle order, and newer evidence supersedes
older evidence for the same capability and stage.

```bash
shadow-conformance-gate \
  --release-dir build/releases/shadow-example/<build-id> \
  --evidence build/evidence/domain-observed.json \
  --require-stage observed \
  --output build/status/observed.json
```

The gate exits non-zero when any selected capability has a failed or missing required
stage. `shadow-deployment-doctor --evidence-output ...` emits deployment evidence that
can be consumed directly by this gate. Its service health probe remains a domain-level
availability signal; it deliberately does not mark every capability `observed`. A domain
monitor or conformance suite must exercise the capability and emit that later evidence.

## Correlation semantics

`shadow.operation-context.v1` and `OperationContext` define identifiers shared by logs,
receipts, probes, conformance evidence, and restore drills:

- `run_id`: one bounded build, probe, or restore-verification run;
- `correlation_id`: one logical workflow across retries and service boundaries;
- `trace_id`: one distributed trace;
- `request_id`: one transport attempt, regenerated on retry;
- `causation_id`: the upstream request or event that caused this operation;
- `idempotency_key`: the stable mutation identity reused across safe retries.

The SDK maps them to `X-Shadow-Run-Id`, `X-Correlation-Id`, `X-Shadow-Trace-Id`,
`X-Request-Id`, `X-Causation-Id`, and `Idempotency-Key`. Logs should record identifiers,
status, latency, capability reference, and bounded error class—not prompts, domain
payloads, tokens, or sensitive entity values.

## Backup restore verification

Platform does not perform domain backups and does not decide whether restored domain
facts are correct. Each domain restores its own backup into an isolated, non-production
target and emits `shadow.restore-drill.v1`. The report must bind the immutable backup,
record RPO/RTO, confirm cleanup, and contain passing checks in all three categories:

1. `contract`: schema/version and migration compatibility;
2. `data`: domain-owned invariants such as counts, hashes, or referential checks;
3. `health`: `healthz`, `readyz`, or equivalent service behavior after restore.

Optional report artifacts are verified by relative path and SHA-256. Absolute paths and
path traversal are rejected.

```bash
shadow-restore-verify \
  --release-dir build/releases/shadow-example/<build-id> \
  --drill build/restore/example-drill.json \
  --output build/evidence/example-restore.json

shadow-conformance-gate \
  --release-dir build/releases/shadow-example/<build-id> \
  --evidence build/evidence/domain-observed.json \
  --evidence build/evidence/example-restore.json \
  --require-stage restore-tested
```

The verifier refuses production targets, mutable backups, mismatched build identities,
failed or incomplete checks, time-order violations, unknown capabilities, and artifact
hash mismatches. Passing validation produces ordinary conformance evidence; this keeps
recovery readiness observable without centralizing backup data.
