---
name: email-cleanup
description: Drain a drifted Gmail inbox back to single/double digits by labeling + archiving noise in parallel buckets, then close the loop by authoring the Gmail filters that stop the refill (and unsubscribe links where a filter is the wrong tool). Never deletes. Maintains a per-mailbox status file (email_cleanup_status.<mailbox>.md) as the durable record of taxonomy, keep-exceptions, filters, and senders that must never be filtered. Trigger on "clean up my email", "inbox zero", "archive old email", "my inbox is a mess", "what can I unsubscribe from", "do I need new filters", "email filters".
disable-model-invocation: true
---

# Email Cleanup

An inbox is *supposed* to hold action-items and live human threads; everything else auto-labels and
archives. Inboxes drift anyway. This skill drains the drift **and fixes the cause**, because
archiving alone means you do it again in three weeks.

It uses the standalone **`gmail` MCP server** (registered from agents-shared — see `mcp/personal.group.json`).
That server exposes not just search/label/archive but also `create_filter` and `create_label`, so this
skill can **author filters and labels directly** (automatically, within safe guardrails), not only hand you paste-ready
specs. All tool names are `mcp__gmail__*`.

This runs for **you** — the person invoking it, resolved in Step 0. It reads and maintains a status
file **named after the resolved mailbox**, so two mailboxes (even on one machine, even a work and a
personal address) never share taxonomy or keep-exceptions, and a run on one mailbox never reads or
overwrites another's state.

Two halves, and the second is the one that matters:
1. **Drain** — fan out parallel bucket sweeps, label + archive, never delete.
2. **Prevent** — every bucket that needed sweeping is evidence of a missing or wrong filter. Author
   the filter. Where a filter is the wrong instrument (cold outreach, opt-in newsletters), say so
   and use the right one.

State lives in a **per-mailbox** status file — the taxonomy, the keep-exceptions, which filters exist
vs. still pending, and the senders that must never be filtered. Read it first; rewrite it at the end.

**The filename is keyed to the mailbox resolved in Step 0**: `email_cleanup_status.<mailbox>.md`, e.g.
`email_cleanup_status.greg@quovy.com.md`. Use the full address verbatim (the `@` and `.` are valid in a
filename). This is the fix for the shared store below holding more than one mailbox's history: a bare
`email_cleanup_status.md` or a *different* address's file belongs to another mailbox — **never read or
overwrite it**. If only a legacy bare file exists for the mailbox you resolved, treat this as a first
run (empty state) rather than adopting it.

**Resolve the directory in this order.** The store is machine-local and **untracked** — it lives inside
the agents-shared checkout this skill installs from, under `var/`, which is gitignored, so mailbox state
never gets committed:
1. `$EMAIL_CLEANUP_STATUS_DIR` if set — explicit override, wins.
2. `<agents-shared>/var/email-cleanup/`, where `<agents-shared>` is the repo this skill is symlinked
   from. Resolve it from the skill's own install path and create the dir if missing:
   ```bash
   skill="$(readlink ~/.claude/skills/email-cleanup 2>/dev/null || echo ~/.claude/skills/email-cleanup)"
   statedir="$(cd "$(dirname "$skill")/.." && pwd)/var/email-cleanup"   # <repo>/var/email-cleanup
   mkdir -p "$statedir"
   ```
3. `$HOME` otherwise (skill not installed via the symlink).

## Hard rules

- **Never delete. Never trash. Never mark spam.** (Exception: *recommend* spam-reporting to the user
  for cold outreach — never apply it yourself.) "Archive" here means *add a label, remove `INBOX` and
  `UNREAD`* via `batch_modify_messages`. Everything stays recoverable by label. This is what makes the
  sweep safe to run aggressively. Do not call `batch_delete_messages`, `delete_message`, or `trash_*`.
- **Never send, reply, or draft** mail as part of a cleanup. Archiving a thread is not answering it.
  Don't touch `send_message`, `create_draft`, or `send_draft`.
- **Auto-create the filters and labels — don't stop to confirm each one.** This server exposes
  `create_filter` / `create_label`, so create them as part of the run once a bucket has earned a rule.
  What keeps auto-creation safe is that every filter is **reversible** (`delete_filter`) and **bounded**
  to non-destructive actions — so it's held to hard guardrails, not a confirmation prompt:
  - **Only** `addLabelIds` (a user label), `removeLabelIds: ["INBOX"]` (skip inbox), and/or
    `removeLabelIds: ["UNREAD"]` (mark read). **Never** a filter that deletes, trashes, or marks spam —
    never put `TRASH` or `SPAM` in `addLabelIds`, never author a delete action. A filter must never be
    able to destroy mail.
  - **Never** author a rule that would catch a NEVER-FILTER sender or any human sender, and always carry
    the mailbox's keep-exceptions as `criteria.negatedQuery`.
  - **Dedup and verify first:** `list_filters` and skip if an equivalent rule already exists; run the
    candidate query and eyeball the hit count (the filter-traps in Step 3) before creating — a rule that
    buries real mail is worse than the noise.
  - **Report everything created** (filters and labels) in the final summary, noting it's reversible.
  `create_filter` does **not** retroactively apply to existing mail (that's a UI-only option), so you
  still archive the current backlog with `batch_modify_messages` and let the filter catch future mail.
- **Can't click unsubscribe.** You can extract the URL from the HTML; the human clicks it.
- **A human sender is never noise.** If a message reads as a person writing prose to you, it stays in
  the inbox (the one exception is the aged-thread rule below, which is age-based and reported).

## Step 0 — Resolve who's running, load profile, read state, then measure

**Resolve the running user's Gmail address first** — this skill is shared, so never assume a specific
mailbox. Get it from `get_profile`, or from a `list_messages q:"in:sent"` (the From address), or ask.
Everything downstream (self-addressed automation, `to:` filters) keys on that address.

**Then load the user profile: `~/.claude/email-cleanup/profile.md`.** This is machine-local, per-user
identity — separate from the untracked per-mailbox status file (which is run history). If it exists, load `name`,
`email`, and `label_preferences` and skip to reading the status file. **If it does NOT exist, this is
the user's first run — do the personalization step below before anything else.**

### Step 0a — First-run personalization (only when no profile exists)

1. **Capture identity.** Confirm the resolved Gmail `email`, and ask the user's `name` (for how you
   address them in reports). Don't guess the name from the email local-part.
2. **Recon the inbox.** `list_messages q:"in:inbox" maxResults:100`, then cluster the results by sender
   domain and by subject shape (a one-liner Python pass over the JSON is fine). This shows what this
   user's noise actually looks like — do not assume it matches anyone else's.
3. **Reconcile against existing labels.** `list_labels` to see what user labels they already have.
4. **Suggest a taxonomy.** From the recon clusters + existing labels, propose a label set and which
   sender/subject clusters map to each. Anchor on the **common taxonomy** below, but only suggest a
   label if the recon shows mail that would fill it. Present the suggestions and let the user confirm,
   rename, add, or drop — their choices are authoritative. Once the taxonomy is agreed, **create any
   missing labels directly with `create_label`** (no separate per-label confirmation — the taxonomy is
   the confirmation); note the ones you create.
5. **Write the profile** to `~/.claude/email-cleanup/profile.md` (create the dir). Format:

   ```markdown
   # email-cleanup profile
   name: <name>
   email: <email>

   ## label_preferences
   <label> — <what it's for, in the user's words> — exists: yes|no
   ...

   ## notes
   <any per-user quirk surfaced during recon — e.g. "routes GitHub via a personal fork account">
   ```

   Keep it small and factual. On later runs, re-read it but don't rebuild it unless the user asks to
   re-personalize.

Read `email_cleanup_status.<mailbox>.md` in the resolved dir (first run — no file for *this* mailbox,
or only a bare/other-address file exists: treat every section as empty, do not adopt another mailbox's
file). Load its **NEVER-FILTER** list and **keep-exceptions** before touching anything.

Load tools on demand — the full Gmail MCP set is large (~60 tools):
`ToolSearch` → `select:mcp__gmail__list_messages,mcp__gmail__get_message,mcp__gmail__list_labels,mcp__gmail__create_label,mcp__gmail__batch_modify_messages,mcp__gmail__list_filters,mcp__gmail__create_filter`

Then measure and classify:
- `list_labels` — **discover this user's labels and their IDs at runtime. Never hardcode label IDs**;
  they differ per mailbox. Match label *names* to buckets, read the ID from the live list.
- `list_messages q:"in:inbox" maxResults:100` — sample the head.

**`resultSizeEstimate` is an estimate, not the inbox size**, and it caps low. A "200-message" inbox has
been over 1,000 in practice. Never size the job or report progress from it. To find the real tail, probe
with age slices (`in:inbox older_than:32d`, `older_than:90d`, `older_than:1y`) — if `older_than:1y`
returns hits, there's a deep backlog the head sample won't show.

Cluster the sample by sender domain and by subject shape (a one-liner Python pass over the JSON is
fine) before designing buckets. **Design buckets from what's actually there, not from this file's
examples.**

**Common label taxonomy** — most inbox-zero setups have some subset of these user labels. Map buckets
to whichever ones this user actually has (read the real IDs from `list_labels`); don't invent labels
the user hasn't confirmed.

| Label | Use for |
|---|---|
| `notification` | auth/token/account/system notices, bot relay |
| `newsletters` | newsletters, vendor marketing, webinars |
| `github` | GitHub/CI notifications |
| `linear` | Linear ticket notifications |
| `sentry` | Sentry error notifications |
| `alerts` | monitoring/alert mail (Datadog, PagerDuty) |
| `receipts` | receipts, invoices, statements |
| `interviews` | interview scheduling |
| `invitations` | calendar invites |
| `archive-personal` | aged human threads |

## Step 1 — Fan out bucket sweeps

One subagent per **non-overlapping** bucket, all launched in a single message so they run
concurrently. Non-overlapping matters: two agents on the same ids race and double-report.

Each subagent prompt MUST include:
- `ToolSearch` with **only** `select:mcp__gmail__list_messages,mcp__gmail__batch_modify_messages`.
  Inheriting the full toolset blows the subagent prompt limit — known failure mode.
- The exact queries, the exact label id to add (from the live `list_labels`), and
  `removeLabelIds: ["INBOX","UNREAD"]`.
- **The loop instruction:** `list_messages q:<query> maxResults:100` → collect ids →
  `batch_modify_messages` (≤1000 ids per call) → **re-run the same search** → repeat until it returns 0.
  Do **not** use `pageToken` paging: archived messages drop out of `in:inbox`, so a fresh search is the
  correct iteration and paging will skip records.
- **Keep-exception ids, verbatim**, with "never include these in any modify call."
- "Never delete, trash, or mark spam. Skip anything a human wrote; report the id instead of guessing."
- A **word cap** (~150) and "report counts per query + any ids skipped and why." Raw message text
  stays in the subagent; only structured counts come back.

Bucket shapes that recur (adapt to the actual inbox):

| Bucket | Query sketch | Label |
|---|---|---|
| Bot-relay mail | `to:<bot-mailbox>` OR `from:<relay-group>` | `notification` |
| CI failures | `subject:"Run failed"` OR `from:notifications@github.com` | `github` |
| Error/alert mail | `from:noreply@sentry.io`, `from:*@pagerduty.com` | `sentry` / `alerts` |
| Calendar/interviews | `label:invitations`, `subject:(Accepted OR Declined)` | keep own label |
| Newsletters/marketing | sender list | `newsletters` |
| Auth/token/account | `subject:"New sign-on notification"`, `subject:"[GitHub]"` | `notification` |
| Aged human threads | `in:inbox older_than:30d category:personal` + `category:forums` | `archive-personal` |

**The aged-human-thread bucket is the delicate one.** Rule: a human thread whose newest message is
>30 days old is dead — it resolved or went quiet, and it isn't an action item. Have the agent
**verify each message `date` client-side** against the cutoff rather than trusting `older_than:` alone,
and **require it to report any archived thread that looks like an unanswered direct ask** (invoices,
contract questions, "please confirm"). That report is the valuable output — surface it to the user
verbatim, since these are archived-but-maybe-live. Also: a message whose thread has newer inbox
messages is fine to archive; the thread stays via the newer message.

Expect this bucket to be far larger than the head sample implies (hundreds, back many months). Say so
plainly when reporting rather than letting a big number pass as routine.

## Step 2 — Sweep the residual tail yourself

Category-based queries miss old mail carrying **no** `CATEGORY_*` label. After the agents finish,
re-run `list_messages q:"in:inbox"` and hand-classify what's left — it's usually 30-60 messages and
faster to batch directly than to brief another agent. Then re-run once more to confirm the count.

Report the **verified** final count from a real search, never an arithmetic estimate.

## Step 3 — Author the filters (the actual point)

Every bucket that needed sweeping is a filter gap. Diagnose which:

**a) Missing filter** — no rule covered it. Straightforward: write one.

**b) Wrong filter — the KEEP-in-inbox leak.** A rule that labels but keeps mail in the inbox on a
*recurring* sender is a slow leak. Vendors re-send the same cluster weekly; calendar invites duplicate
the calendar. These are top accumulators precisely because they were "handled." Fix: Skip Inbox with a
**narrow keep-exception** for the dated/actionable subset, not blanket keep.

**c) Blind spot — envelope rewriting.** A Google Group relaying vendor mail to a bot mailbox *rewrites
the sender*, so every sender-based rule misses it — **and Gmail `to:` filters do not reliably match
Google-Group-relayed mail either.** Best filtered on **`from: <the group address>`**, not on
`to: <bot mailbox>`. Whenever a bucket is large and its senders look unrelated, check the recipient
*and* the relaying group.

### Creating the filter with the MCP

Unlike aggregator Gmail integrations, this server's `create_filter` authors the rule directly. Map the
Gmail dialog to the API:

| Gmail dialog | `create_filter` field |
|---|---|
| From / To / Subject / Has the words | `criteria.from` / `criteria.to` / `criteria.subject` / `criteria.query` |
| Doesn't have (keep-exception) | `criteria.negatedQuery` |
| Skip the Inbox | `action.removeLabelIds: ["INBOX"]` |
| Mark as read | `action.removeLabelIds: ["UNREAD"]` |
| Apply label | `action.addLabelIds: ["<label id from list_labels>"]` |

So a Skip-Inbox + label + keep-exception filter is one call:

```
create_filter
  criteria: { from: "grp.<group>@<domain>", negatedQuery: "subject:\"API tokens\"" }
  action:   { addLabelIds: ["Label_123"], removeLabelIds: ["INBOX","UNREAD"] }
```

**Create each filter directly** once it clears the guardrails (non-destructive actions only, no
NEVER-FILTER/human sender, keep-exceptions as `negatedQuery`, deduped against `list_filters`, hit count
verified). No per-filter confirmation. After creating, remember: `create_filter` only affects *future*
mail — archive the existing matching backlog in Step 1/2 via `batch_modify_messages`.

If a rule is genuinely too subtle for `create_filter` (rare — `negatedQuery` covers most keep-exceptions),
fall back to a paste-ready spec for the Gmail Settings → Filters dialog and say so.

### Filter traps — check every one before proposing a rule

These are real misfires from actual runs. Re-verify rather than trusting the list.

- **Don't filter a whole vendor domain when humans use it.** `no-reply@`/`ar@`/`noreply@` at a vendor
  is automation; the named account team (`csm@`, a person's first name @) is the live relationship —
  leave open threads in the inbox.
- **Internal names collide with vendor names.** A vendor subject clause can match internal mail that
  happens to share the word. Test every subject clause with a `list_messages` search and read the hit
  count before proposing it.
- **Mixed-purpose senders.** Some senders mix marketing *and* real product/pipeline alerts (analytics
  vendors, data-platform EOL notices). Either exclude by subject or leave them alone.
- **Self-addressed automation.** Some reminders arrive *from your own address* with a display name —
  sender rules can't catch them, filter on subject.
- **Verify the hit count before creating.** Run the candidate query and eyeball the results. A filter
  that quietly buries real mail is worse than the noise it removes.

## Step 4 — Unsubscribe, and when not to

A filter hides mail; unsubscribing stops it. For opt-in newsletters and vendor marketing,
unsubscribing is strictly better — fewer filters, less hidden volume.

Triage the `newsletters` label by sender and volume, then split three ways:

1. **Unsubscribe** — opt-in newsletters and real vendor marketing. Extract URLs (below).
2. **Report as spam, do NOT unsubscribe** — cold sales and recruiter outreach. Clicking unsubscribe on
   cold outreach confirms a live mailbox and typically *increases* volume; the domains rotate every few
   weeks so neither unsubscribe nor a sender filter holds. Spam-reporting trains the classifier;
   recommend it, let the user do it.
3. **Leave alone** — operational mail miscategorized as marketing (see mixed-purpose senders above).

**Extracting URLs.** Delegate to one read-only subagent (`list_messages` + `get_message` only, the
latter with `includeBodyHtml: true`). Bodies are 50KB+, so instruct it explicitly: keep HTML in its own
context, and return **only** `sender — full URL` lines. Find `href` values near "unsubscribe", "opt
out", "email preferences", "manage preferences", "notification settings". Demand the **complete,
untruncated** URL (these are opaque tracking tokens — a clipped one is worthless) and "never fabricate a
URL; say 'no unsubscribe link found'."

Watch for: `&amp;` in raw HTML must be unescaped to `&` or the link 404s. One newsletter brand can be
several subscriptions with distinct tokens — get each. Preference-center links (Gainsight, HubSpot) are
not one-click: tell the user which boxes to uncheck and which to leave on.

**Deliver as a local HTML file** (`/tmp/unsubscribe-links.html`, then `open` it). Long tracking URLs are
miserable to paste out of a chat transcript. Use `a:visited` styling so the user can track progress down
the list, group by category, and **write the exclusions and the do-not-touch senders into the page
itself** so the reasoning outlives the session. HubSpot/preference-center tokens can expire — fallback is
the footer link in a recent mail from that sender. `/tmp` is not durable — regenerate the file if it's
gone and links are still pending.

## Step 5 — Write state, report

Rewrite `email_cleanup_status.<mailbox>.md` in the resolved status dir (Step 0) — the per-mailbox
filename, never a bare `email_cleanup_status.md`:

```markdown
# Email Cleanup Status

Last run: <YYYY-MM-DD HH:MM TZ> · inbox after: <verified count>

## 🚫 NEVER FILTER
- <sender/pattern> — <why: human account team / mixed-purpose / operational>

## 📌 KEEP-EXCEPTIONS (current actionable mail — re-evaluate each run)
- <id> — <subject> — <the deadline or reason>

## 🧹 FILTERS
### Created (live in Gmail)
- <name> — <criteria> → <actions> — created <date>
### Pending (proposed, not yet created)
- <name> — <criteria> → <actions>

## ✉️ UNSUBSCRIBE
- Done: <senders>
- Pending: <senders> — links at <path>
- Spam-report instead: <senders> — <why>

## 📊 RUNS
- <date> — <before> → <after>; buckets: <bucket:count, ...>; <root causes found>
```

Rules: **NEVER-FILTER only grows** — never drop an entry without the user saying so, it's the guardrail
against burying real mail. Keep-exceptions are point-in-time (today's actionable cluster is next month's
noise) — re-evaluate every run. Track created-vs-pending honestly; a pending filter means the inbox
*will* refill and the next run should lead with that.

Then report to the user, short:
- Verified before → after count, with the caveat if the real backlog exceeded the estimate.
- Bucket table (count + label).
- What's left and why it's genuinely live.
- **Root cause** — which filter gaps caused the drift. This is the insight, not the archive count.
- Anything flagged from the aged-thread sweep that may still be unresolved — verbatim, prominent.
- **Filters** — which you created this run (they're live and reversible via `delete_filter`), and any
  that fell back to a paste-ready spec because they were too subtle to auto-create. If a run genuinely
  found no gap, say "no new filters needed this run" explicitly rather than omitting it. Then note the
  unsubscribe file path.
