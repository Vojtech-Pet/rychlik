import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// FriendSend's whole business model: 5 free successful sends, then a one-time lifetime unlock. No ads, no
/// subscription. `remainingTrialSends` only ever changes through [recordSuccessfulTargetedSend] (never a raw
/// setter), so nothing outside this service can silently reset or inflate it.
class EntitlementStatus {
  const EntitlementStatus({required this.unlocked, required this.remainingTrialSends});

  final bool unlocked;
  final int remainingTrialSends; // meaningless once unlocked; always 0 there

  /// Whether FriendSend may send anything at all right now (by any route: a picked app or the system Sharesheet).
  bool get canSend => unlocked || remainingTrialSends > 0;

  static const EntitlementStatus initial = EntitlementStatus(unlocked: false, remainingTrialSends: EntitlementService.defaultTrialSends);
}

abstract class EntitlementSource {
  Future<EntitlementStatus> status();

  Stream<EntitlementStatus> get changes;

  /// Called only after a targeted ACTION_SEND was really accepted by Android (`TargetShareResult.opened`).
  /// Never for the system Sharesheet (receipt there can't be confirmed) and never for a failed/cancelled send.
  Future<EntitlementStatus> recordSuccessfulTargetedSend();

  Future<EntitlementStatus> unlock();
}

/// Always unlocked; used where FriendSend's monetisation is not wired in (most tests).
class AlwaysUnlockedEntitlement implements EntitlementSource {
  const AlwaysUnlockedEntitlement();

  @override
  Future<EntitlementStatus> status() async => const EntitlementStatus(unlocked: true, remainingTrialSends: 0);

  @override
  Stream<EntitlementStatus> get changes => const Stream.empty();

  @override
  Future<EntitlementStatus> recordSuccessfulTargetedSend() async => status();

  @override
  Future<EntitlementStatus> unlock() async => status();
}

/// Persistent (survives app and phone restarts): a small JSON file in the app's own support directory. Stores
/// the number of sends *used*, not remaining, so a future change to the trial size never corrupts old data.
class EntitlementService implements EntitlementSource {
  EntitlementService(Directory supportDir, {this.trialSends = defaultTrialSends}) : _file = File('${supportDir.path}/entitlement.json');

  static const int defaultTrialSends = 5;

  final int trialSends;
  final File _file;
  final StreamController<EntitlementStatus> _controller = StreamController<EntitlementStatus>.broadcast();
  EntitlementStatus? _cached;

  @override
  Stream<EntitlementStatus> get changes => _controller.stream;

  @override
  Future<EntitlementStatus> status() async => _cached ??= await _load();

  Future<EntitlementStatus> _load() async {
    try {
      final data = jsonDecode(await _file.readAsString());
      if (data is! Map) throw const FormatException('not an object');
      final unlocked = data['unlocked'] == true;
      final used = ((data['successfulSends'] as num?)?.toInt() ?? 0).clamp(0, trialSends);
      return EntitlementStatus(unlocked: unlocked, remainingTrialSends: unlocked ? 0 : trialSends - used);
    } catch (_) {
      return EntitlementStatus(unlocked: false, remainingTrialSends: trialSends); // no file yet, or unreadable: a fresh trial
    }
  }

  Future<void> _persist(bool unlocked, int used) async {
    final tmp = File('${_file.path}.tmp');
    await tmp.writeAsString(jsonEncode({'unlocked': unlocked, 'successfulSends': used}));
    await tmp.rename(_file.path);
  }

  @override
  Future<EntitlementStatus> recordSuccessfulTargetedSend() async {
    final current = await status();
    if (current.unlocked) return current; // already unlimited: nothing to count
    if (current.remainingTrialSends <= 0) return current; // must not go negative; canSend should have blocked this already
    final usedBefore = trialSends - current.remainingTrialSends;
    final next = EntitlementStatus(unlocked: false, remainingTrialSends: trialSends - (usedBefore + 1));
    await _persist(false, usedBefore + 1);
    _cached = next;
    _controller.add(next);
    return next;
  }

  @override
  Future<EntitlementStatus> unlock() async {
    const next = EntitlementStatus(unlocked: true, remainingTrialSends: 0);
    await _persist(true, trialSends);
    _cached = next;
    _controller.add(next);
    return next;
  }
}
