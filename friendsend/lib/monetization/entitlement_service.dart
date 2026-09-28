import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'product.dart';
import 'purchase_verifier.dart';

/// FriendSend's whole business model: 5 free successful sends, then a one-time lifetime unlock. No ads, no
/// subscription. A client-reported "purchased" callback is never, by itself, a permanent unlock -- it only
/// starts verification. `remainingTrialSends` only ever changes through [recordSuccessfulTargetedSend].
enum EntitlementState {
  /// Counting down free sends; no purchase on record.
  trial,

  /// A purchase was reported by the billing client and is being verified for the first time.
  purchasePending,

  /// Verified against a real backend: permanent, unconditional.
  fullVerified,

  /// The billing client reported a purchase but verification could not be reached (offline, backend down).
  /// Grants the same access as [fullVerified] for a bounded grace period while verification keeps retrying --
  /// never presented as permanent, and it lapses into [restoreRequired] if the grace period runs out.
  fullUnverified,

  /// A purchase was reported but verification came back negative (or the grace period lapsed): sending needs a
  /// successful "Restore purchases" (or trial sends, if any remain) before it can continue.
  restoreRequired,
}

class EntitlementStatus {
  const EntitlementStatus({required this.state, required this.remainingTrialSends});

  final EntitlementState state;
  final int remainingTrialSends; // meaningful only for trial/purchasePending/restoreRequired

  /// True while [state] grants sending, whether that's real trial credit or a verified/grace-period unlock.
  bool get canSend => switch (state) {
        EntitlementState.fullVerified || EntitlementState.fullUnverified => true,
        EntitlementState.trial || EntitlementState.purchasePending || EntitlementState.restoreRequired => remainingTrialSends > 0,
      };

  /// Kept for the UI: true once a purchase grants access (verified or still in its grace period), whatever the
  /// trial count says. Never true just because trial sends remain.
  bool get unlocked => state == EntitlementState.fullVerified || state == EntitlementState.fullUnverified;

  static const EntitlementStatus initial = EntitlementStatus(state: EntitlementState.trial, remainingTrialSends: EntitlementService.defaultTrialSends);
}

abstract class EntitlementSource {
  Future<EntitlementStatus> status();

  Stream<EntitlementStatus> get changes;

  /// Called only after a targeted ACTION_SEND was really accepted by Android (`TargetShareResult.opened`).
  /// Never for the system Sharesheet (receipt there can't be confirmed) and never for a failed/cancelled send.
  Future<EntitlementStatus> recordSuccessfulTargetedSend();

  /// The billing client reported this purchase (a real token, or a replay of one already known). Idempotent:
  /// re-applying the same already-verified token is a no-op. Starts (or re-runs) verification and settles into
  /// [EntitlementState.fullVerified], [EntitlementState.fullUnverified] or back to [EntitlementState.trial]
  /// depending on the result -- a rejected purchase is never left pending.
  Future<EntitlementStatus> applyPurchase({required String productId, required String purchaseToken, required PurchaseVerifier verifier});

  /// Re-runs verification for whatever purchase is on record (grace-period retry); a no-op if there is none.
  Future<EntitlementStatus> retryPendingVerification({required PurchaseVerifier verifier});

  /// "Restore purchases": `record` is whatever the billing client found owned for this account (or null for
  /// nothing owned, which leaves the current state -- including any trial credit -- untouched).
  Future<EntitlementStatus> restoreFrom(PurchaseRecord? record, {required PurchaseVerifier verifier});
}

/// What the billing client found: enough to verify and to record for later restores.
class PurchaseRecord {
  const PurchaseRecord({required this.productId, required this.purchaseToken});

  final String productId;
  final String purchaseToken;
}

/// Always unlocked; used where FriendSend's monetisation is not wired in (most tests).
class AlwaysUnlockedEntitlement implements EntitlementSource {
  const AlwaysUnlockedEntitlement();

  @override
  Future<EntitlementStatus> status() async => const EntitlementStatus(state: EntitlementState.fullVerified, remainingTrialSends: 0);

  @override
  Stream<EntitlementStatus> get changes => const Stream.empty();

  @override
  Future<EntitlementStatus> recordSuccessfulTargetedSend() async => status();

  @override
  Future<EntitlementStatus> applyPurchase({required String productId, required String purchaseToken, required PurchaseVerifier verifier}) async => status();

  @override
  Future<EntitlementStatus> retryPendingVerification({required PurchaseVerifier verifier}) async => status();

  @override
  Future<EntitlementStatus> restoreFrom(PurchaseRecord? record, {required PurchaseVerifier verifier}) async => status();
}

/// Internal on-disk record: everything [EntitlementStatus] is projected from, plus what a purchase needs to be
/// re-verified or restored later.
class _Record {
  const _Record({required this.state, required this.sendsUsed, this.productId, this.purchaseToken, this.unverifiedSince});

  final EntitlementState state;
  final int sendsUsed;
  final String? productId;
  final String? purchaseToken;
  final DateTime? unverifiedSince;

  bool get isUnlimited => state == EntitlementState.fullVerified || state == EntitlementState.fullUnverified;

  _Record copyWith({
    EntitlementState? state,
    int? sendsUsed,
    Object? productId = _unset,
    Object? purchaseToken = _unset,
    Object? unverifiedSince = _unset,
  }) =>
      _Record(
        state: state ?? this.state,
        sendsUsed: sendsUsed ?? this.sendsUsed,
        productId: identical(productId, _unset) ? this.productId : productId as String?,
        purchaseToken: identical(purchaseToken, _unset) ? this.purchaseToken : purchaseToken as String?,
        unverifiedSince: identical(unverifiedSince, _unset) ? this.unverifiedSince : unverifiedSince as DateTime?,
      );

  static const Object _unset = Object();
}

/// Persistent (survives app and phone restarts): a small JSON file in the app's own support directory.
/// `sendsUsed`, not "remaining", is what's stored, so a future change to the trial size never corrupts old data.
class EntitlementService implements EntitlementSource {
  EntitlementService(Directory supportDir, {this.trialSends = defaultTrialSends, Duration? unverifiedGracePeriod, DateTime Function()? clock})
      : _file = File('${supportDir.path}/entitlement.json'),
        unverifiedGracePeriod = unverifiedGracePeriod ?? const Duration(days: 3),
        _now = clock ?? DateTime.now;

  static const int defaultTrialSends = 5;

  final int trialSends;
  final Duration unverifiedGracePeriod;
  final DateTime Function() _now;
  final File _file;
  final StreamController<EntitlementStatus> _controller = StreamController<EntitlementStatus>.broadcast();
  _Record? _cached;

  @override
  Stream<EntitlementStatus> get changes => _controller.stream;

  EntitlementStatus _project(_Record r) => EntitlementStatus(
        state: r.state,
        remainingTrialSends: r.isUnlimited ? 0 : (trialSends - r.sendsUsed).clamp(0, trialSends),
      );

  @override
  Future<EntitlementStatus> status() async => _project(await _currentWithGraceApplied());

  Future<_Record> _current() async => _cached ??= await _load();

  /// A [fullUnverified] record whose grace period has run out becomes [restoreRequired] -- checked on every
  /// read, so it can never silently stay "unlocked" forever without ever being verified.
  Future<_Record> _currentWithGraceApplied() async {
    final record = await _current();
    if (record.state == EntitlementState.fullUnverified && record.unverifiedSince != null) {
      if (_now().difference(record.unverifiedSince!) > unverifiedGracePeriod) {
        final next = record.copyWith(state: EntitlementState.restoreRequired);
        await _persist(next);
        return next;
      }
    }
    return record;
  }

  Future<_Record> _load() async {
    try {
      final data = jsonDecode(await _file.readAsString());
      if (data is! Map) throw const FormatException('not an object');
      if (data.containsKey('state')) {
        final state = EntitlementState.values.asNameMap()[data['state']] ?? EntitlementState.trial;
        final used = ((data['successfulSends'] as num?)?.toInt() ?? 0).clamp(0, trialSends);
        final unverifiedMs = (data['unverifiedSinceEpochMs'] as num?)?.toInt();
        return _Record(
          state: state,
          sendsUsed: used,
          productId: data['productId'] as String?,
          purchaseToken: data['purchaseToken'] as String?,
          unverifiedSince: unverifiedMs == null ? null : DateTime.fromMillisecondsSinceEpoch(unverifiedMs),
        );
      }
      // Pre-hardening schema (v1: {"unlocked": bool, "successfulSends": int}). A previously unlocked install is
      // grandfathered in as verified (it predates the verifier existing at all); nothing else reads this shape.
      final unlocked = data['unlocked'] == true;
      final used = ((data['successfulSends'] as num?)?.toInt() ?? 0).clamp(0, trialSends);
      return _Record(state: unlocked ? EntitlementState.fullVerified : EntitlementState.trial, sendsUsed: used);
    } catch (_) {
      return _Record(state: EntitlementState.trial, sendsUsed: 0); // no file yet, or unreadable: a fresh trial
    }
  }

  Future<void> _persist(_Record r) async {
    _cached = r;
    final tmp = File('${_file.path}.tmp');
    await tmp.writeAsString(jsonEncode({
      'state': r.state.name,
      'successfulSends': r.sendsUsed,
      if (r.productId != null) 'productId': r.productId,
      if (r.purchaseToken != null) 'purchaseToken': r.purchaseToken,
      if (r.unverifiedSince != null) 'unverifiedSinceEpochMs': r.unverifiedSince!.millisecondsSinceEpoch,
    }));
    await tmp.rename(_file.path);
    _controller.add(_project(r));
  }

  @override
  Future<EntitlementStatus> recordSuccessfulTargetedSend() async {
    final current = await _currentWithGraceApplied();
    if (current.isUnlimited) return _project(current); // unlimited: nothing to count
    if (current.sendsUsed >= trialSends) return _project(current); // must not go negative; canSend should have blocked this already
    final next = current.copyWith(sendsUsed: current.sendsUsed + 1);
    await _persist(next);
    return _project(next);
  }

  @override
  Future<EntitlementStatus> applyPurchase({required String productId, required String purchaseToken, required PurchaseVerifier verifier}) async {
    if (productId != lifetimeUnlockProductId) return status(); // not our product: ignored, no state change
    final current = await _currentWithGraceApplied();
    if (current.state == EntitlementState.fullVerified && current.purchaseToken == purchaseToken) {
      return _project(current); // idempotent: a replayed callback for an already-verified purchase
    }
    final pending = current.copyWith(state: EntitlementState.purchasePending, productId: productId, purchaseToken: purchaseToken);
    await _persist(pending);
    return _verifyAndSettle(pending, verifier);
  }

  @override
  Future<EntitlementStatus> retryPendingVerification({required PurchaseVerifier verifier}) async {
    final current = await _currentWithGraceApplied();
    if (current.purchaseToken == null || current.productId == null) return _project(current);
    if (current.state != EntitlementState.fullUnverified && current.state != EntitlementState.purchasePending && current.state != EntitlementState.restoreRequired) {
      return _project(current);
    }
    return _verifyAndSettle(current, verifier);
  }

  @override
  Future<EntitlementStatus> restoreFrom(PurchaseRecord? record, {required PurchaseVerifier verifier}) async {
    if (record == null) return status(); // nothing owned: current state (including any trial credit) is untouched
    return applyPurchase(productId: record.productId, purchaseToken: record.purchaseToken, verifier: verifier);
  }

  Future<EntitlementStatus> _verifyAndSettle(_Record pending, PurchaseVerifier verifier) async {
    final result = await verifier.verify(productId: pending.productId!, purchaseToken: pending.purchaseToken!);
    final next = switch (result) {
      VerificationResult.verified => pending.copyWith(state: EntitlementState.fullVerified, unverifiedSince: null),
      VerificationResult.rejected => pending.copyWith(state: EntitlementState.trial, productId: null, purchaseToken: null, unverifiedSince: null),
      VerificationResult.temporaryFailure => pending.copyWith(state: EntitlementState.fullUnverified, unverifiedSince: _now()),
    };
    await _persist(next);
    return _project(next);
  }
}
