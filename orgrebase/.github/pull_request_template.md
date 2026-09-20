## Outcome

<!-- Which invariant, failure boundary, or reviewable evidence does this change preserve or add? -->

## Changes

<!-- Smallest meaningful set of interface or behavior changes. -->

## Verification

- [ ] `make check-core` from `orgrebase/` (required for ordinary pull requests)
- [ ] `make check` when the change touches OAC, PostgreSQL, deployment, or release qualification
- [ ] New behavior has a test for the invariant or failure boundary
- [ ] New evidence declares an existing evidence class (static assets, replay, and fixtures are never `LIVE_AGENTTEAMS`)
- [ ] No credentials, raw prompts, model output, or private paths in the diff

## Privacy and recovery

<!-- Note synthetic vs live data, state writes, rollback, and any change to authority or Skill permissions. -->
