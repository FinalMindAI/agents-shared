---
name: dependabot-triage
version: 1.0.0
description: |
  Triage and remediate open Dependabot pull requests and security alerts in a
  repo end-to-end: enumerate both surfaces, assess each bump's real blast radius
  by grepping actual usage and reading the real changelog, split the work into
  batched-low / individual-medium / spike-high buckets, apply the fixes in
  parallel worktrees, close transitive CVEs with bounded pnpm overrides, and
  sequence merges around the pnpm-lock cascade. Bundles a Workflow-tool script
  that automates the enumerate-and-assess half (steps 1-4).
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Agent
  - Workflow
  - WebFetch
  - AskUserQuestion
---

# /dependabot-triage — Dependabot PRs & security alerts → ranked plan → fixes

Turns a pile of open Dependabot PRs and security alerts into a risk-ranked plan
and a set of merged (or PR-ready) fixes. The judgement that matters — is this
bump safe? — comes from checking the **real changelog** against **our real
usage**, never from memory. The bundled Workflow script does the enumerate +
per-bump risk assessment for you; the rest of this doc is the operational
procedure for turning that assessment into merged code.

## Arguments

- `/dependabot-triage [owner/repo]` — defaults to the current repo's origin
  (`gh repo view --json nameWithOwner -q .nameWithOwner`).
- Assumes `gh` is authenticated for the repo. Work repos: stop at PR creation
  (human approval required to merge); personal repos may auto-merge on green CI.

## The two surfaces (they are separate — check both)

Version-update PRs and security alerts are **different** Dependabot surfaces. A
repo can have zero open PRs and still have open critical alerts, or an alert
whose fix is a transitive bump no PR will ever raise. Always enumerate both.

## Step 1 — Enumerate

```bash
# Version-update PRs
gh pr list --author "app/dependabot" --state open \
  --json number,title,headRefName,createdAt,labels,mergeStateStatus

# Security alerts (separate API — needs security_events scope)
gh api -X GET /repos/OWNER/REPO/dependabot/alerts -f state=open --paginate \
  --jq '.[] | {ghsa: .security_advisory.ghsa_id, sev: .security_advisory.severity,
        pkg: .dependency.package.name, vuln: .security_vulnerability.vulnerable_version_range,
        patched: .security_vulnerability.first_patched_version.identifier,
        scope: .dependency.scope, manifest: .dependency.manifest_path}'
```

Note which alerts already have a PR raised against them (dedupe against step 1)
and which are transitive with no PR — those get fixed via overrides in step 6.

## Step 2 — Triage & prioritize by risk

For each PR pull the body/changelog and CI state:

```bash
gh pr view <n> --json title,body,headRefName,files,labels
gh pr checks <n>
```

Classify each by:

- **Blast radius** — transitive / dev / test dependency (low) · direct runtime
  dependency (medium) · security-critical or auth/realtime/crypto path (high).
- **CI state** — already green vs. red (a red check is often just a stale
  lockfile, see step 5, not a real incompatibility).
- **Shape** — single-package bump · a **grouped** PR (npm-major group, the
  github-actions group) that moves several packages at once · a **duplicate** of
  a bump another open PR already carries.

Grouped and duplicate PRs get flagged here; they are handled in the split plan.

## Step 3 — Risk assessment via parallel agents (the Workflow)

Run the bundled Workflow script. It fans out one risk-assessment agent per
bump, in parallel, each of which:

1. **greps ACTUAL usage** in the repo — imports and callsites of the package,
   not just its presence in the manifest;
2. **fetches the REAL changelog / migration guide** via WebFetch (GitHub
   releases, `CHANGELOG.md`, the project's migration doc) — the actual release
   notes between `from` and `to`, never recalled from memory;
3. **cross-checks each breaking change against our usage** — does the breaking
   change touch an API we call, at a specific `file:line`?
4. **returns LOW / MED / HIGH** with the concrete `file:line`, the exact fix,
   and an effort estimate.

Invoke it via the **Workflow** tool, passing the repo:

```
Workflow(workflow: "{skill_dir}/workflow.mjs", args: { owner: "OWNER", repo: "REPO" })
```

It returns the ranked table plus a first-cut split/merge plan. Treat that as the
input to steps 4-7 — **verify anything load-bearing** before you act on it
(re-read the cited line; the agent can misread). Never accept a rating you can't
trace to a real release note and a real callsite.

## Step 4 — Split plan

Bucket every bump from the assessment:

- **LOW → one batched PR.** Transitive / dev / test bumps with zero source
  changes. Group them into a single PR — they share a lockfile change and there
  is no per-bump risk to isolate.
- **MED → individual PRs.** A contained source change plus a gate (a type
  migration, a small behavioral change, anything needing visual QA). One PR each
  so the gate and the revert boundary are per-change.
- **HIGH → dedicated spikes, never grouped.** Compiler/toolchain swaps (a major
  TypeScript, a bundler), auth SDKs, realtime/websocket libraries, tightly
  coupled toolchains. Each gets its own branch and its own investigation. Do not
  batch these with anything.

Then: **drop** any bump that another open PR already owns, and **close** the
duplicate PRs (`gh pr close <n> --comment "superseded by #<m>"`).

## Step 5 — Fix (parallel worktree agents)

Spin one worktree per non-trivial fix (`isolation: "worktree"` agents) so the
fixes don't collide. Common fixes seen in practice:

- **Stale lockfile sync** — a bump touched `package.json` but not
  `pnpm-lock.yaml`, so every check fails with `ERR_PNPM_OUTDATED_LOCKFILE`
  (it cascades into *all* checks, not just one). Fix: `pnpm install` to
  regenerate the lock, commit, push. This alone turns most "red" Dependabot PRs
  green.
- **Type migrations** — a `@types/*` or TS-facing bump changes a signature;
  follow the migration note to the exact callsite.
- **Behavioral mitigations** — a new default that is wrong for us. Example seen
  in the wild: `undici` v8 defaults `allowH2: true`; at an SSRF-sensitive
  fetch boundary that widens the surface, so pin `allowH2: false` at our call
  site as part of the bump.

Each worktree fix **verifies locally before it opens a PR**: `pnpm typecheck`,
`pnpm lint`, `pnpm test`, `pnpm build` (or the repo's equivalents). A fix that
hasn't been built locally is not done.

## Step 6 — Security sweep

Close the alerts from step 1 that no PR covers.

- **Transitive CVEs → bounded `pnpm.overrides`** in the root `package.json`.
  **CRITICAL: always bound the override per major.** An unbounded `">=x"` spec
  resolves to the *newest* published version — i.e. the next major — and forces
  that incompatible major onto every parent that depended on the old major,
  breaking the build. Bound it:

  ```jsonc
  "pnpm": {
    "overrides": {
      "js-yaml@3": ">=3.15.1 <4",        // patch the vulnerable v3 line, stay on v3
      "undici@>=7 <8": ">=7.29.0 <8"     // patch within v7, don't jump to v8
    }
  }
  ```

- **Direct low-risk deps → a normal spec bump** in the manifest (no override).
- **Watch for a vulnerable copy pinned by a direct dep.** If a direct dependency
  pins a vulnerable transitive version, bumping the leaf isn't enough — you need
  a **patch-scoped override** (`pkg@major`) to move the pinned copy too.

After editing overrides: `pnpm install`, then re-run `gh api .../dependabot/alerts`
to confirm the alert closed, and `pnpm build` to confirm nothing broke.

## Step 7 — Merge sequencing (the lockfile cascade)

**Every PR that touches `pnpm-lock.yaml` re-conflicts every other open one the
moment one merges.** So merge serially: merge one, rebase the next, repeat.

- **Detect real conflicts fast** — don't wait on GitHub's async mergeability
  field. Compute it locally:

  ```bash
  git merge-tree --write-tree --name-only origin/main origin/<branch>
  ```

  A `pnpm-lock.yaml` in the output is expected and resolvable (see below); a
  conflict in *source* files is a real one to handle by hand.

- **Dependabot's own `@dependabot rebase` FAILS here.** Its runner can't
  regenerate our lockfile under the repo's supply-chain settings (7-day rule,
  blocked-exotic-deps), so it either errors or produces a bad lock. Rebase the
  lockfile **manually** instead:

  ```bash
  git fetch origin && git checkout <branch>
  git merge origin/main                        # source conflicts, if any, resolve here
  git checkout origin/main -- pnpm-lock.yaml    # take main's lock as the base
  pnpm install                                  # regenerate on top of this branch's manifest
  git add pnpm-lock.yaml && git commit && git push
  ```

## Gotchas

- **pnpm 7-day supply-chain rule** blocks bumping to a version published less
  than 7 days ago (`ERR_PNPM_NEW_VERSION`). That's intentional. If a Dependabot
  bump targets a just-published version, it will fail install until the window
  passes — see the repo's `guides/pnpm-supply-chain.md` for the override.
- **UI / chart / component bumps need visual QA** before merge — a green CI
  doesn't catch a rendering regression. Gate these behind a browser pass.
- **Work repos require human approval to merge** — stop at PR creation, don't
  self-merge. Personal repos may auto-merge on green CI.

## Report

When done, report: the ranked table (package, from→to, risk, why), the split
plan (which bucket each landed in), what merged vs. what's a PR awaiting
approval, which alerts closed and how (bump vs. bounded override), and anything
that needs a human decision (a HIGH spike, a bump blocked by the 7-day rule, a
UI change awaiting visual QA).
