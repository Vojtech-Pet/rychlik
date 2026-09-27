# Open Validation Debt

Tracks real end-to-end scenarios that are known to be untested (or only
partially tested) even though the underlying unit/integration coverage is
green. An item here is closed only when the actual combined scenario is
executed, never merely because its component parts pass separately.

---

## A9-CRASH-RANGE-E2E

**Status:** OPEN
**Blocking A10:** NO
**Blocking functional GUI (A11):** NO
**Blocking beta/release gate:** YES

**Description:** A real combined end-to-end scenario —

```text
real subprocess starts a real Range-capable HTTP download
    -> a durable partial checkpoint is written
    -> the process is SIGKILLed mid-transfer (real, ungraceful)
    -> a fresh process opens the same database
    -> RestartRecovery runs
    -> normal A3/A5 dispatch issues a REAL HTTP Range request
       using the recovered, locally-re-validated partial
    -> byte-exact completion
```

has not been executed as ONE subprocess test. What exists instead,
separately:

- Prompt A8's `test_real_crash_recovery_e2e`
  (`tests/test_real_process_crash_e2e.py`): a real `SIGKILL` against a
  real child process, real recovery, and a real redownload — but against
  the plain `/slow` fixture route (no `ETag`/`Range` support), so the
  redownload after recovery is a full restart from byte 0, not a Range
  resume.
- Prompt A9's real pause/resume/retry-resume E2E tests
  (`tests/test_runtime_pause_resume_e2e.py`,
  `tests/test_dispatch_coordinator_resume.py`): real `Range`/`If-Range`
  resume against the `/resumable/<key>` fixture, including a real dropped
  connection mid-transfer — but no process is ever actually killed;
  interruption is simulated by closing the fixture's own connection or by
  a Python-level `DownloadPaused`/retry, all within the same test process.
- Focused non-subprocess tests
  (`tests/test_restart_recovery.py::test_paused_with_valid_partial_stays_paused`
  and friends) prove `RestartRecovery`'s local partial-validation logic in
  isolation, without a real process boundary at all.

Each piece is real and independently proven, but the exact combination —
a genuinely killed process whose surviving partial state is then actually
validated over the network via a real `Range` request in a new process —
has never run as a single test.

**Why not closed during A9 or A10:** explicitly scoped out of A9 (see
`docs/SAFE_PARTIAL_RESUME_RESULT.md`, KNOWN LIMITATIONS) given the time
that phase already took; A10 is an application-facade phase and
intentionally does not touch acquisition/resume mechanics, so it is not
positioned to close this either.

**What would close it:** a test that starts a real child process against
a `/resumable/<key>`-style fixture route, waits for real evidence of a
durable partial checkpoint (not just `TRANSFERRING`), `SIGKILL`s it, then
in a fresh process runs recovery and normal dispatch and asserts the
resulting real HTTP request carries `Range: bytes=<durable_bytes>-` (not
`bytes=0-`) before completing byte-exact.

---

*(Future validation debt items should be appended below, each with the
same Status/Blocking/Description/Why-not-closed/What-would-close-it
shape.)*
