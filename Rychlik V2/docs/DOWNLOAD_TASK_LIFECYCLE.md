# Download Task Lifecycle Domain Model (Prompt A2)

## Purpose

`DownloadTask` answers exactly one question, deterministically:

> What phase is this download task in? Which transitions are legal from
> here? Is it terminal? Has a real transfer started? How many transfer
> attempts have begun? Was it paused, cancelled, retried, failed, or
> completed?

It does **not** answer: which queued task starts next, how many may run
concurrently, which worker owns it, when a retry should actually happen,
or how much bandwidth is available. Those belong to a future scheduler/
dispatch phase (A3+).

## Boundary vs. DownloadQueue (the two pauses)

This is a **second, independent** state machine from
`rychlik.core.download_queue.QueueEntryState`. `QUEUED` is never a
`DownloadTaskState`, and nothing in `download_task.py` imports or mutates
`DownloadQueue`.

The single most important distinction in this whole phase:

```text
QueueEntryState.PAUSED
= this task remains in the queue, but a future scheduler must not
  select it. No network transfer needs to exist.

DownloadTaskState.PAUSED
= a real transfer had already started (TRANSFERRING) and the transfer
  lifecycle is now paused.
```

`DownloadTaskState.PAUSED` can **only** originate from `TRANSFERRING` — a
`READY` task cannot be task-paused (`pause_transfer()` raises
`InvalidTaskTransitionError` from `READY`); to hold a `READY` task without
starting it, pause its `QueueEntry` instead.

`DownloadTask READY` + `QueueEntry PAUSED` is a perfectly valid, common
combination — proven by a dedicated structural test
(`test_task_ready_and_queue_paused_is_a_valid_independent_combination`).

## States

```text
CREATED          task object exists; acquisition preparation not yet done
RESOLVING        resolving what must actually be downloaded (e.g. yt-dlp
                 extraction); direct HTTP downloads may skip this
READY            enough information to start a transfer; does NOT mean
                 "about to run" -- a future scheduler decides that
TRANSFERRING     an actual acquisition/transfer is active
PAUSED           a previously active transfer was deliberately paused
RETRY_WAIT       a retryable attempt failed, waiting for future retry
                 eligibility (no delay/backoff/timer modeled here)
VERIFYING        transfer bytes complete, an integrity phase is executing
POST_PROCESSING  post-transfer processing executing (merge/remux/etc.,
                 none of it actually implemented here)
COMPLETED        terminal success
FAILED           terminal failure (requires DownloadTaskFailure)
CANCELLED        terminal cancellation (never carries a fabricated failure)
```

## Transition table

| From | Operation | To | Allowed |
|---|---|---|---|
| CREATED | start_resolving | RESOLVING | yes |
| CREATED | mark_ready | READY | yes |
| CREATED | fail | FAILED | yes |
| CREATED | cancel | CANCELLED | yes |
| RESOLVING | mark_ready | READY | yes |
| RESOLVING | wait_for_retry | RETRY_WAIT | yes (retryable only) |
| RESOLVING | fail | FAILED | yes |
| RESOLVING | cancel | CANCELLED | yes |
| READY | start_transfer | TRANSFERRING | yes |
| READY | fail | FAILED | yes |
| READY | cancel | CANCELLED | yes |
| READY | pause_transfer | — | **no** |
| TRANSFERRING | pause_transfer | PAUSED | yes |
| TRANSFERRING | wait_for_retry | RETRY_WAIT | yes (retryable only) |
| TRANSFERRING | start_verification | VERIFYING | yes |
| TRANSFERRING | start_post_processing | POST_PROCESSING | yes |
| TRANSFERRING | complete | COMPLETED | yes |
| TRANSFERRING | fail | FAILED | yes |
| TRANSFERRING | cancel | CANCELLED | yes |
| PAUSED | resume_transfer | TRANSFERRING | yes |
| PAUSED | fail | FAILED | yes |
| PAUSED | cancel | CANCELLED | yes |
| PAUSED | mark_ready | — | **no** |
| RETRY_WAIT | mark_ready / mark_retry_ready | READY | yes |
| RETRY_WAIT | fail | FAILED | yes |
| RETRY_WAIT | cancel | CANCELLED | yes |
| RETRY_WAIT | start_transfer | — | **no** |
| VERIFYING | start_post_processing | POST_PROCESSING | yes |
| VERIFYING | complete | COMPLETED | yes |
| VERIFYING | wait_for_retry | RETRY_WAIT | yes (retryable only) |
| VERIFYING | fail | FAILED | yes |
| VERIFYING | cancel | CANCELLED | yes |
| VERIFYING | pause_transfer | — | **no** |
| POST_PROCESSING | complete | COMPLETED | yes |
| POST_PROCESSING | fail | FAILED | yes |
| POST_PROCESSING | cancel | CANCELLED | yes |
| POST_PROCESSING | start_transfer | — | **no** |
| COMPLETED / FAILED / CANCELLED | anything | — | **no** (terminal) |

Same-state calls (e.g. `pause_transfer()` while already `PAUSED`) are
idempotent no-ops: no mutation, `updated_at` unchanged. Calling a
terminal-producing operation while already in a *different* terminal
state (e.g. `cancel()` on a `COMPLETED` task) is rejected, not idempotent
— terminal state must never change meaning.

`mark_ready()` accepts `CREATED`, `RESOLVING`, or `RETRY_WAIT` as its
source. `mark_retry_ready()` is a narrower, explicitly-named entry point
that only accepts `RETRY_WAIT` — for a future retry-policy runtime that
wants to fail loudly if the task isn't actually waiting for retry, rather
than silently succeeding via the broader `mark_ready()`.

## Failure model

```text
DownloadTaskFailure
├── code       — stable machine-readable identifier (non-empty, required)
├── message    — human/debug description
└── retryable  — whether wait_for_retry() may be used
```

Never holds a raw `Exception`, traceback, or `requests.Response` —
infrastructure failures are mapped into this shape by a future execution
layer, keeping the lifecycle serializable and backend-independent.

**Invariant**: `state == FAILED` implies `last_failure is not None`,
enforced in `DownloadTask.__post_init__` — a `FAILED` task without a
failure cannot even be constructed.

**Invariant**: `wait_for_retry()` requires `failure.retryable is True`;
otherwise `InvalidRetryFailureError`. A non-retryable failure should go
straight to `fail()`.

`last_failure` is retained across a successful retry and even into a later
`COMPLETED` state — it is historical evidence, not cleared automatically
(tested explicitly).

Cancellation never synthesizes a fabricated failure; `cancel()` never sets
`last_failure` itself — whatever the task already carried (possibly
`None`, possibly a real prior failure from an earlier retry cycle) is
preserved unchanged.

## Attempt count semantics

```text
attempt_count starts at 0
READY -> TRANSFERRING          increments (a NEW attempt)
TRANSFERRING -> PAUSED -> TRANSFERRING   does NOT increment (same attempt)
RETRY_WAIT -> READY -> TRANSFERRING       increments (a retried attempt)
RESOLVING / VERIFYING / POST_PROCESSING   never increment
```

## Pause/resume semantics

`pause_transfer()`: `TRANSFERRING -> PAUSED` only.
`resume_transfer()`: `PAUSED -> TRANSFERRING` only; `attempt_count` and
`started_at` are unchanged — resuming continues the *same* attempt, it is
not a new one.

## Retry semantics

`wait_for_retry(failure)`: `RESOLVING | TRANSFERRING | VERIFYING ->
RETRY_WAIT`, requires `failure.retryable`. No delay, backoff, timer, or
max-attempt policy is modeled — a future retry-policy runtime decides
*when* to call `mark_ready()`/`mark_retry_ready()`; this module only
provides the state and the entry/exit points.

## Timestamp semantics

```text
created_at   — set once at creation, never mutated
updated_at   — bumped by every real mutation; unchanged by idempotent no-ops
started_at   — set once, the FIRST time the task enters TRANSFERRING;
               survives pause/resume and retry cycles unchanged
finished_at  — set exactly once, only on entering a terminal state
```

All four are timezone-aware; a naive `datetime` is rejected at
construction. `now` is always an explicit, required argument to every
mutating method — there is no hidden global clock, matching `ShareLink`'s
established convention from Prompt 06.

## Known limitations (intentionally deferred to A3+)

```text
no scheduler / dispatch / claim semantics
no worker ownership
no concurrency limits
no actual HTTP pause/resume (no socket/thread suspension)
no safe Range-based resume mechanics
no retry delay/backoff/jitter, no max-attempt policy
no progress/speed/ETA
no task persistence, no crash recovery
no automatic coordination between DownloadQueue and DownloadTask
no GUI, no PySide6/requests/yt_dlp imports anywhere in this module
```
