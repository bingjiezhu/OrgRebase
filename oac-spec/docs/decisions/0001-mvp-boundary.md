# ADR 0001: OAC scope and implementation boundary

**Status:** Accepted

**Date:** 2026-08-23

## Context

A portable organizational contract must remain usable by different Agent and human runtimes. Coupling its semantics to one product, workflow engine or generated topology would make independent verification and reuse depend on that implementation.

## Decision

Maintain OAC as an adjacent contract project, with OrgRebase as a reference consumer. OAC owns admitted organizational resources, obligation derivation and bounded Plan/Outcome acceptance. Runtime scheduling, enterprise IAM, source collection and canonical business writes remain application responsibilities.

The reference implementation begins with a zero-effect Supplier Profile. A frozen Snapshot and semantic change derive obligations; a compiler proposes a Plan and a separate verifier checks the declared relation. Multiple valid topologies may satisfy the same contract. Schemas, registries, detached digests, TCK and registered negative controls define the inspectable boundary.

## Consequences

Public enterprise documents can support exploratory inputs but do not supply organizational Ground Truth. Independent human annotation, externally maintained implementations, real enterprise outcomes and production authorization require separate evidence. Bounded technical results cannot establish those qualifications.

New Profiles may extend the supported contract while preserving versioned inputs, explicit Unknown and compiler-independent acceptance. OAC does not add a runtime, general workflow DSL or automatic promotion authority.
