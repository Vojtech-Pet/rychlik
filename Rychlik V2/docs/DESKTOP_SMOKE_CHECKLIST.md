# Desktop Smoke Checklist (Prompt A12)

A concise manual verification list for a future real graphical session.
Every item here is already covered by an automated headless (`QT_QPA_
PLATFORM=offscreen`) test — this checklist exists for the times a human
wants to eyeball the real thing on a real desktop, not to replace the
automated suite.

## Launch

- [ ] Launch from the desktop icon (`rychlik.desktop`) — window appears,
      no error dialog.
- [ ] Launch from a terminal via `launch.sh`, from a directory unrelated
      to the repository (e.g. `cd /tmp && /path/to/Rychlik-app/launch.sh`)
      — window appears identically.
- [ ] On a clean, empty database: window shows the empty state message
      ("No downloads yet. Paste a URL above to start."), not a bare/blank
      table.

## Destination

- [ ] "Save to:" field shows a real, existing directory by default (the
      platform's Downloads folder where available).
- [ ] "Browse…" opens a real native folder picker; choosing a folder
      updates the field; cancelling leaves it unchanged.

## Adding downloads

- [ ] Paste a real URL, click "Download" — a row appears promptly; the
      URL field clears and regains focus.
- [ ] Paste a URL and press Enter instead of clicking — same result.
- [ ] Rapidly double-click "Download" once — exactly one row is added,
      not two.
- [ ] Add a second download while the first is still in progress — both
      rows are visible, progress advances independently.

## Queue / transfer control

- [ ] Select a waiting item, click "Hold" — status shows "On hold";
      select it again, click "Release" — it resumes normal queueing.
- [ ] Select an actively-downloading item, click "Pause" — status stays
      "Downloading" briefly, then changes to "Paused" once the backend
      confirms it (not instantly).
- [ ] Click "Resume" on a paused item — it eventually starts downloading
      again (a real Range-resume, not a restart from zero, if the server
      supports it).
- [ ] Click "Cancel" on an active download — it disappears from the table
      once the backend confirms cancellation.
- [ ] Force a transient failure (or wait for one) to see "Waiting to
      retry"; click "Retry now" — a new attempt starts immediately rather
      than waiting out the full backoff.
- [ ] Change an item's priority via the combo box — the table reorders
      according to the new priority once the backend confirms it.
- [ ] Use "Up"/"Down" on same-priority items — they swap order.

## Completion / Share / Open Folder

- [ ] Let a download complete — it disappears from the active table (per
      the existing snapshot policy).
- [ ] Immediately after completion (while you still know the file name),
      verify the file exists in the chosen destination.
- [ ] For a *just-completed* item still visible in an intermediate state
      or via a fresh add-then-complete cycle: select it, click "Open
      Folder" — the real file manager opens to the correct directory.
- [ ] Click "Share…" on a completed item — the existing Share dialog
      opens with the correct file/Artifact.

## Restart

- [ ] Close the application normally (no active transfers) — it exits
      immediately, no confirmation dialog.
- [ ] Start a slow download, then close the window — a confirmation
      dialog appears ("Downloads are still active…"); choose "Cancel" —
      the window stays open and the download continues.
- [ ] Repeat, this time choosing "Close" — the window shows "Shutting
      down…" briefly, then exits.
- [ ] Relaunch the application against the same data — previously
      queued/held/paused downloads reappear with the correct state; a
      previously-completed download's "Open Folder"/"Share" still work
      without re-downloading.

## Fault handling

- [ ] (Optional, harder to trigger manually) If the backend ever reports
      a fault, verify every mutating control becomes disabled and a
      persistent message is shown rather than the app silently failing on
      the next click.

---

This checklist is descriptive, not a blocking gate for any phase — see
each phase's own RESULT document for whether a manual visible-desktop
smoke was actually performed (`PASS`) or deferred (`DEFERRED`, with the
environment reason) for that specific phase.
