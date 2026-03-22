# OpenAPI-First Architecture Plan

**Date:** 2026-03-21  
**Scope:** public HTTP contracts, backend boundary cleanup, CLI/iOS contract usage, local verification scripts  
**Primary goals:** OpenAPI as source of truth, thin router boundaries, generated transport contracts, local drift detection

---

## Summary

The repo already exports a full FastAPI OpenAPI document, derives a filtered CLI schema from it, and has documented architecture boundaries. The missing piece is authority: the schema and the boundaries are helpful references, but they are not yet treated as the canonical source of truth across every public API consumer and every backend edge.

This initiative makes OpenAPI authoritative for the public HTTP surface and tightens backend boundaries so the transport layer, application layer, presenters, repositories, and generated client artifacts all have explicit ownership.

The project is intentionally staying small and local-first. This work does not introduce CI. Enforcement should come from deterministic local scripts and pre-commit hooks.

---

## Why This Initiative Exists

- The full OpenAPI schema exists, but downstream usage is inconsistent.
- The CLI is already derived from a filtered OpenAPI schema, but the broader public API contract is still easy to drift from.
- The iOS app still relies mostly on hand-written transport models, which leaves room for schema drift even when the backend contract is clear.
- The backend architecture docs define good rules, but some seams are still conventions rather than mechanically enforced boundaries.
- The repo is small enough to avoid CI, but still large enough to benefit from contract drift detection and architecture checks.

---

## Goals

- Make the full exported OpenAPI schema the canonical source of truth for every public HTTP route.
- Keep the CLI schema as a pure derivative of the full OpenAPI document.
- Move the iOS client to generated transport DTOs plus hand-written domain/view models.
- Tighten backend boundaries so routers stay thin and transport models do not leak into service/domain code.
- Add local scripts that regenerate and verify contract artifacts deterministically.
- Update the durable architecture docs so the contract and boundary rules are unambiguous.

## Non-Goals

- No broad API redesign unless required to stabilize schema names or generation.
- No ORM or queue-payload redesign in this initiative.
- No UI redesign.
- No CI rollout.

---

## OpenAPI Authority

### Problem

The app emits a useful full OpenAPI document, but it is not yet treated as the only public contract authority. In practice, route schemas, downstream client models, and checked-in artifacts can still drift independently.

### Deliverables

- Treat `docs/library/reference/openapi.json` as the canonical public HTTP contract.
- Keep `cli/openapi/agent-openapi.json` as a filtered derivative of the full schema only.
- Standardize stable `operationId`, tags, and schema names for all externally consumed routes.
- Treat unnamed or unstable route metadata as contract bugs.

### Acceptance Criteria

- Exporting the full OpenAPI schema from the app produces the checked-in contract with no manual edits.
- The filtered CLI schema is fully derived from the full schema and not hand-maintained.
- Public route identifiers are stable enough for downstream generation and documentation.

---

## Backend Boundary Cleanup

### Problem

The current architecture documentation describes the right direction, but the codebase still has areas where transport ownership and backend layering are looser than intended. That makes it harder to use the public schema consistently and harder to keep the application shape obvious.

### Deliverables

- Enforce router modules as thin adapters over application commands and queries.
- Keep repositories responsible for query composition only.
- Keep presenters responsible for response shaping only.
- Prevent service/domain modules from importing router transport DTO modules.
- Split oversized API transport model modules by feature slice so ownership follows the API surface.

### Target Dependency Rules

- Routers may import API DTOs, auth/deps, and application entrypoints only.
- Application entrypoints may orchestrate repositories, presenters, services, and gateways.
- Repositories may depend on DB/ORM and canonical contracts only.
- Presenters may depend on domain objects and public response schemas only.
- Service and domain modules must not depend on router-layer DTOs.

### Acceptance Criteria

- No direct router-to-repository imports remain in the cleaned target surface.
- No service/domain module imports router DTO modules.
- Large transport schema modules are split along stable feature boundaries.

---

## CLI and iOS Contract Usage

### Problem

The CLI is already close to the desired model, but the iOS app still uses mostly hand-written transport types. That weakens the value of having a canonical server-emitted schema because transport decoding can still diverge from the actual contract.

### Deliverables

- Keep the CLI on generated artifacts derived from the filtered OpenAPI schema.
- Move the iOS app to a hybrid contract model:
  - generated request/response transport DTOs from OpenAPI,
  - hand-written domain/view models for UI behavior,
  - explicit transport-to-domain mapping at the client boundary.
- Restrict networking code to decoding generated transport DTOs only.

### Acceptance Criteria

- CLI artifacts remain fully derivable from checked-in schemas.
- iOS networking uses generated transport DTOs at the HTTP boundary.
- UI-facing Swift models remain hand-written and decoupled from generated transport code.

---

## Local Enforcement

### Problem

Without CI, the repo needs local checks that are fast, deterministic, and hard to ignore. The goal is to make schema drift and boundary regressions visible before code review, not to add heavyweight infrastructure.

### Deliverables

- Add `scripts/regenerate_public_contracts.sh` to:
  - export the full OpenAPI schema,
  - export the filtered CLI schema,
  - regenerate CLI client artifacts,
  - regenerate iOS transport DTOs and enum contracts,
  - refresh any contract reference docs.
- Add `scripts/check_public_contracts.sh` to fail when checked-in artifacts drift.
- Add an architecture-boundary checker for the import-direction rules in this plan.
- Wire those checks into pre-commit.

### Enforcement Rules

- Regeneration remains explicit.
- Pre-commit hooks fail on drift or invalid architecture imports.
- Hooks should verify state, not silently rewrite tracked files.

### Acceptance Criteria

- A developer can run one command to refresh all public contract artifacts.
- A developer can run one command to verify that artifacts and boundaries are current.
- Pre-commit catches contract drift and boundary violations locally.

---

## Documentation Alignment

### Deliverables

- Update `docs/architecture.md` to explicitly state:
  - OpenAPI is authoritative for the public wire format.
  - Generated client transport code is authoritative at client boundaries.
  - Routers, application entrypoints, presenters, repositories, and gateways are the intended seams.
- Keep this initiative doc focused on implementation intent and staging.
- Keep durable architectural rules in `docs/architecture.md`.

### Acceptance Criteria

- The initiative doc explains what to change.
- The architecture doc explains the lasting rules after the work is complete.

---

## Test Plan

- Snapshot verification for the full OpenAPI export.
- Snapshot verification for the filtered CLI OpenAPI export.
- Drift checks for generated CLI artifacts.
- Drift checks for generated Swift transport DTOs and enum contracts.
- Static architecture-boundary checks for import direction.
- Representative route tests for stable `operationId` and schema-name expectations.
- iOS decoding tests for generated transport DTOs plus mapper tests into existing domain models.

---

## Assumptions and Defaults

- OpenAPI authority applies to the public HTTP surface only.
- Internal service interfaces and queue payloads remain hand-designed unless explicitly refactored later.
- The iOS client uses a hybrid model rather than fully generated app models.
- Enforcement stays local and pre-commit based; no CI work is part of this initiative.
- If schema cleanup requires small additive or naming-only route fixes, prefer the smallest compatible change.
