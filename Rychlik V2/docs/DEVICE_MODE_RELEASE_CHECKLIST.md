# Device Mode Release Checklist

A reusable checklist for validating a Rýchlik + FriendSend release
candidate before it is considered ready to hand to a real user. Intended
to be run in full whenever a physical Android device becomes available,
and re-run before any future release. Introduced by Prompt A17.

Each box should be checked only against **real** evidence (a real
subprocess, a real second device, a real APK) -- never a simulated
stand-in for the specific item it covers. Where this checklist was last
run without a physical device, boxes under Sharesheet/mDNS/physical
restart/forget-re-pair are left unchecked and the run is recorded as
BLOCKED in `docs/PHYSICAL_ANDROID_VALIDATION.md`, never silently marked
done.

## Desktop

- [ ] `git status` clean, `git rev-parse HEAD` recorded
- [ ] `ffmpeg -version` / `ffprobe -version` recorded, required encoders present (`libx264`, `aac`)
- [ ] Full desktop pytest suite green, run >=3x for stability
- [ ] Launch via `launch.sh` / `launch_studio.sh` and via desktop icon (where applicable)

## Download manager

- [ ] Destination chooser works
- [ ] Real download completes with progress
- [ ] Pause / resume works
- [ ] Retry / cancel works where practical
- [ ] Completed row shows; Open Folder / Share work

## A9 resume

- [ ] Combined real-subprocess-crash -> durable-checkpoint -> `SIGKILL` -> restart -> real HTTP `Range`/`If-Range` resume -> byte-exact completion (`tests/test_a9_crash_range_e2e.py`)

## FriendSend installation

- [ ] Release-candidate APK installs on a real device (`adb install -r`)
- [ ] Fresh install starts unpaired, no fake trusted state

## Pairing

- [ ] Real one-time pairing secret / transcript / HMAC proof, no test-only registration or hardcoded trust
- [ ] Pairing secret never appears in logs/UI after pairing completes

## Trust persistence

- [ ] Trust survives FriendSend restart
- [ ] Trust survives desktop restart
- [ ] Trust survives both restarting together

## mDNS

- [ ] Real Android `NsdManager` advertisement discovered by the real desktop `zeroconf` browser (no manual IP entry)
- [ ] Discovery alone never substitutes for TLS/SPKI trust on the actual connection

## Secure send

- [ ] `pinned-tls-signature-v1` selected; no `plain-http-bearer-v1` fallback on the real path
- [ ] TLS/SPKI pin validated; desktop Ed25519 authentication succeeds

## Passthrough / Remux / Transcode

- [ ] Compatible MP4/H.264/AAC source: PASSTHROUGH, no FFmpeg invoked
- [ ] MKV/H.264/AAC source: REMUX, codec-copy verified, original untouched
- [ ] Incompatible (e.g. VP9/Opus) source: TRANSCODE, re-probed/validated output, original untouched

## Sharesheet

- [ ] Real `content://` `FileProvider` URI produced (never `file://`)
- [ ] Real Android Sharesheet (`ACTION_SEND` / `Intent.createChooser`) opens with the received item

## Cancel

- [ ] Cancel mid-transfer: `CANCELLED`, receiver partial deleted, desktop source unchanged

## Cleanup

- [ ] Desktop A16 derived temporary file cleaned after success/failure/cancel
- [ ] Phone temporary payload respects its own TTL; Discard removes it and returns to ready state

## Restart

- [ ] FriendSend restart: same identity, same trust, mDNS resumes
- [ ] Desktop restart: trusted phone loads, discovery resumes, send works without re-pairing

## Forget / re-pair

- [ ] "Forget device" (desktop) removes trust; send forbidden until re-paired
- [ ] "Forget desktop" (phone) rejects future sends from that desktop identity
- [ ] Fresh pairing after forget fully restores operation; no stale credential resurrects old trust

## Security

- [ ] No reachable release-path TLS bypass (`verify=False`-without-pin-check-after, disabled hostname/cert checks without the SPKI pin substituting) on desktop
- [ ] No `badCertificateCallback`/insecure trust-all on the Android/Dart side
- [ ] Release manifest requests only justified permissions (no storage/contacts/phone/camera/mic beyond what the feature set actually needs)
- [ ] `FileProvider`: `exported=false`, `grantUriPermissions=true`, narrowest possible exposed subtree
- [ ] No unintended `android:usesCleartextTraffic="true"` / permissive network security config on the release build
- [ ] Identity/trust private material excluded from generic Android backup

## Logs

- [ ] Desktop logs during a real secure transfer contain no pairing secret / private key / bearer token
- [ ] `adb logcat` during pairing/discovery/send/Sharesheet contains no pairing secret / private key / full payload bytes

## APK

- [ ] `flutter build apk --debug` succeeds
- [ ] `flutter build apk --release` succeeds
- [ ] `flutter analyze` clean
- [ ] Full Flutter test suite green, run >=3x for stability

## Known limitations

Record anything intentionally out of scope here for the release in
question (e.g. no background receive when FriendSend is fully closed, no
cloud relay, no automatic recipient/app selection, no final visual
redesign) so the checklist stays truthful about what was and was not
actually verified.
