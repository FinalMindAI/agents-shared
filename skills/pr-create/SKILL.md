---
name: pr-create
description: Run tests, lint, typecheck, format, then create a well-described PR for a Quovy repo. Trigger on "create a PR", "open a PR", "push this", "ship it", "make a PR", "/pr".
---

# PR Creation

You create pull requests for Quovy repos. Validate code quality (tests, lint, typecheck, format), create a well-named branch, commit, push, and open a PR with a description built from the full diff. Quovy is a work org: **stop at PR creation** — a human approves the merge. (To babysit CI to green after, use the `pr-babysit` skill.)

## Own the work

Everything this skill writes goes out under the user's name: the commit message, the PR title, and the PR body. It must read like they wrote it, not like a model generated it.

- **No em dashes (—).** Use a period, comma, colon, or parentheses. The em dash is the clearest AI tell.
- **High information density.** No filler, no restating the diff back, no hedging. If you'd skim past a line, cut it.
- **Interpreted, not raw.** State the conclusion and the fact behind it, not a dump of tool output.
- **Plain engineer voice.** Direct, the way an engineer actually writes. No marketing adjectives or bullet padding.
- **No AI attribution.** Never append "Generated with Claude Code" or any bot signature to the commit or PR.

If a draft wouldn't pass as something the user would personally send, rewrite it. (If they have the `voice` skill, apply it to the PR body.)

## Step 1: Detect the stack

```bash
git diff --name-only "$(git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main)"..HEAD 2>/dev/null || git diff --name-only HEAD
```

| Changed files | Stack |
|---|---|
| `.ts`, `.tsx`, `.js`, `.jsx`, `package.json` | TypeScript/JavaScript (pnpm) |
| `.py`, `pyproject.toml` | Python |
| `.sql`, `drizzle/`, `packages/db` | Drizzle/Postgres migrations |

Only run the checks for stacks actually present in the diff.

## Step 2: Validate (per Quovy's post-change pipeline)

Run in order; if any step fails, stop and report — do not proceed to PR.

| Stack | Test | Lint | Typecheck | Format |
|---|---|---|---|---|
| TypeScript/JS | `pnpm test` | `pnpm lint` | `pnpm typecheck` | `pnpm format` (if defined) |
| Python | `python -m pytest` | `ruff check --fix` | — | `ruff format` |

If a linter/formatter modifies files, stage the changes — they go in the commit. If lint or type errors remain after auto-fix, report them and ask how to proceed. For DB/migration changes, confirm the migration was generated and applied per the repo's Drizzle workflow.

## Step 3: Branch (if needed)

If already on a feature branch (not `main`/`master`), keep it. Otherwise create one — never commit to `main`:

```
<type>/<short-description>          # feat/fix/docs/refactor/chore/test/perf/ci
<type>/<ticket>-<short-description> # with a Linear ticket, e.g. feat/quo-123-fix-payment-overflow
```

- `<type>`: same set as commit types below.
- `<ticket>`: include the Linear ticket id if one is in the conversation or commits (lowercased, e.g. `quo-123`); omit the segment otherwise.
- `<short-description>`: kebab-case, 3-5 words.

## Step 4: Commit

```
<type>: <description>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`. First line under 72 chars, imperative mood. Optional body explains what and why, not how. Write the message from the **full diff**, not just the last edit. No AI attribution footer.

## Step 5: Push & create the PR

```bash
git push -u origin <branch-name>
```

Build the PR body from the **full diff between the base branch and HEAD**:

```bash
gh pr create --base main --title "<title>" --body "$(cat <<'EOF'
## Summary
<2-5 bullets: what changed and why>

## Changes
<key changes by file or area>

## Test plan
- [ ] <checklist items>
EOF
)"
```

- **PR title:** under 72 chars, imperative ("Fix column overflow"). Prefix a ticket if present: `[QUO-123] Fix column overflow`.
- **Test plan:** only check a box (`- [x]`) with explicit evidence the item ran (CI/tests passed, or the user confirmed a manual step). Code existing is not evidence. Leave the rest unchecked.

## Step 6: Report

- PR URL
- Test / lint / typecheck results
- Files-changed count
- Reminder that it's awaiting human review (offer `pr-babysit` to watch CI to green).
