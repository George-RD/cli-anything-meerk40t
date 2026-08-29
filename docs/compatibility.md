# MeerK40t compatibility contract

Issue #60 establishes the released-runtime contract for the harness. This file
is the authoritative list of MeerK40t releases a contributor must preserve.
The CI policy behind it is recorded in
[ADR-0007](decisions/ADR-0007-meerk40t-compatibility-ci-policy.md).

## Supported released runtimes

The supported release window is:

```text
meerk40t>=0.9.8230,<=0.9.9100
```

The releases in that window are currently:

| MeerK40t | Required CI | Evidence |
|---|---:|---|
| `0.9.8230` | yes | canonical integration seam + 340-test behavioral gate pass |
| `0.9.8930` | yes | canonical integration seam + behavioral gate pass |
| `0.9.9000` | yes | canonical integration seam + behavioral gate pass |
| `0.9.9100` | yes | canonical integration seam + behavioral gate pass |

`.github/workflows/compatibility.yml` exact-pins every supported release. A
failure in any released-runtime lane is blocking. The package dependency uses
the same closed interval so a newly published MeerK40t version is not silently
accepted before it has evidence.

## Boundary evidence

The initial #60 probe deliberately tested releases below the final floor as
well as the supported tail:

- `0.9.1000` failed the canonical seam while booting the real kernel because
  `meerk40t.extra.coolant` does not exist in that release.
- `0.9.3001` failed at the same missing native module boundary.
- `0.9.8230`, `0.9.8930`, and `0.9.9000` completed the required seam and
  behavioral gates; `0.9.9100` is retained as the current-release clean-install
  invariant and required matrix endpoint.
- Candidate releases between `0.9.3001` and `0.9.8230` were exploratory and do
  not expand the declared support window. Supporting them later requires a
  deliberate compatibility change backed by a complete required gate.

The floor is therefore conservative: it is the first release in a contiguous
published tail for which every release through current is part of the required
contract. We do not infer support from version ordering alone.

## Upstream early warning

Current MeerK40t `main` runs the same seam and behavioral checks in the
`upstream main (informational)` job. The job is `continue-on-error`, because
unreleased upstream code must not veto a harness merge. It runs on repository
changes and on the weekly schedule so breakage is visible before the next
release.

When upstream publishes a new release:

1. add the exact version to the released matrix and run the required gates;
2. only after a pass, widen `setup.py` and this contract to include it;
3. keep any necessary adaptation behind the canonical integration seam from
   ADR-0006, using capability detection rather than scattered version checks.

Issue #18 remains the separate watch for MeerK40t PRs #3249 and #3250. The
compatibility workflow does not replace or duplicate that maintenance watch.
