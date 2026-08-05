<!--
  Keep this short. The issue holds the "what" and "why"; this holds the "how" and
  anything a reviewer could not infer from the diff.
-->

## What and why

<!-- One or two sentences. If it needs more, the PR is probably too big. -->

Refs #
<!-- Use `Closes #N` only on the PR that finishes the issue's checklist. -->

**Requirements:** <!-- e.g. FR-06, NFR-08. Write "none" for chores and tooling. -->

## Documentation

<!--
  REQUIRED. Pick exactly one. CI fails a PR that changes anything outside
  docs/**/*.md with no docs change and no documentation-impact line below.
-->

- [ ] Documentation updated in this PR: <!-- list the files -->
- [ ] `no-doc-impact:` <!-- reason, e.g. "internal refactor, no observable behaviour change" -->
- [ ] Follow-up issue #___ because: <!-- why it cannot be done here -->

## Issue checklist

<!-- Which boxes in docs/backlog/*.yaml this PR ticks. They must be ticked in this diff. -->

## Definition of done

- [ ] Tests cover the behaviour **and its principal failure mode**
- [ ] State-changing operations emit an audit event (or: not applicable)
- [ ] Authorisation enforced server-side, with a **negative-case** test (or: not applicable)
- [ ] Keyboard operable, labelled, sensible focus order (or: not applicable)
- [ ] Errors say what to do next, not a stack trace or a status code
- [ ] `requirements.yaml` status updated for any requirement now implemented
- [ ] Full test suite green locally before this PR was raised

## Reviewer notes

<!--
  Optional. Anything that would otherwise cost the reviewer ten minutes: a decision you
  went back and forth on, a deliberate omission, a test you could not write and why.
-->
