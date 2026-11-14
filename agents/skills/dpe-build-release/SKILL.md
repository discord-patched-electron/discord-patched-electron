---
name: dpe-build-release
description: Build or qualify local DPE packages, reuse verified Electron artifacts, investigate build/discovery state, or prepare an authorized budgeted Electron compilation or release.
---

# Build and package DPE

Read [release packaging](../../../docs/agent/releases.md). For compilation also
read [CI runners and budget](../../../docs/agent/ci-setup.md) and
[build caching](../../../docs/agent/releases.md#runtime-reuse). Use current configuration
and locks; historical run IDs, budgets and example timestamps are not inputs to
reuse blindly. Run commands from the repository root.

For iterative helper edits and reusable local profiles, use
[dpe-dev-environments](../dpe-dev-environments/SKILL.md). Managed dev payloads
are separate from the unmodified portable apps used for release qualification.

## Decide whether compilation is needed

Client modules, application adapters, settings and packaging can normally reuse
a verified patched runtime. Trace `scripts/fingerprints.py` versus
`scripts/runtime_fingerprints.py` to separate application and native identities.
An upstream client exception or a changed stock executable does not by itself
justify rebuilding Electron. Upstream candidates need the
[complete update review](../dpe-upstream-review/SKILL.md).

Fetch exact immutable client snapshots with `scripts/fetch_client_snapshot.py`
and retained runtime artifacts with `scripts/fetch_reference.py`. Verify hashes
and provenance. Keep runtime inputs, packaged output and test profiles separate.
Generated work belongs under `out/`.

## Package and qualify

Follow the release guide's current local sequence: `scripts/build_local_app.py`
with an explicit client lock and verified artifact, relocate the portable app,
run `scripts/check_desktop_launch.py --relaunch`, then use
`scripts/package_release.py` and `scripts/check_release_packages.py`. Inspect
current `--help` and required tools before running. For minimal installed
bootstraps, seed unpublished `application.zip` and runtime archives through the
check's explicit verified archive options.

Check the staged support inventory, application/client compatibility identities,
shared libraries and package dependencies. New required system dependencies can
require a bootstrap API increment so old installations keep working. Preserve
sandboxing and scoped namespace rules; do not expand a native dependency
exception just because a new binary fails. Run the affected
[runtime checks](../dpe-runtime-checks/SKILL.md) and mod preservation checks.

## Compile only when necessary and authorized

Inspect queued/running work and retained compatible outputs first. New hosted
compilations use `build.yml` with the budget reservation and source preflight;
do not bypass its limits or retries. Preserve healthy compilers, fixed workspace
identity paths, compatible Ninja objects and bounded checkpoints. A package
rebuild does not require restarting a compiler or deleting retained objects.

Distinguish source assembly, dependency application, compilation, qualification,
candidate publication and approval. `ci-state` is generated durable state and
must not be merged into `main`. Local artifacts and passing tests do not imply
permission to dispatch paid work, publish, approve, install packages or update
AUR. Proceed with externally mutating steps only within the user's authorized
task, using the existing signed-release and manual approval policy.

## Change CI or test coverage

Use the [workflow ownership and test design policy](../../../docs/agent/ci-setup.md#workflow-ownership)
before adding jobs, workflows or fixtures. Share pinned setup; keep distinct
candidate, runtime, package and approval boundaries. Add coverage for observable
failures, not assertions about script spelling. Read final failed-step results:
expected negative-test diagnostics can contain “error” even when the suite passes.
