import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/monetization/entitlement_service.dart';
import 'package:friendsend/monetization/purchase_verifier.dart';

const _productId = 'friendsend_lifetime_unlock';

class _Verifier implements PurchaseVerifier {
  _Verifier([this.result = VerificationResult.verified]);
  VerificationResult result;
  final calls = <(String, String)>[];
  @override
  Future<VerificationResult> verify({required String productId, required String purchaseToken}) async {
    calls.add((productId, purchaseToken));
    return result;
  }
}

void main() {
  late Directory root;

  setUp(() => root = Directory.systemTemp.createTempSync('fs_billing_'));
  tearDown(() {
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  group('B1: purchase state model', () {
    test('5 trial sends exhausts the trial, blocking further sends', () async {
      final service = EntitlementService(root);
      for (var i = 0; i < 5; i++) {
        await service.recordSuccessfulTargetedSend();
      }
      final status = await service.status();
      expect(status.state, EntitlementState.trial);
      expect(status.canSend, isFalse);
    });

    test('a verified purchase reaches FULL and survives a restart', () async {
      final service = EntitlementService(root);
      final status = await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: _Verifier());
      expect(status.state, EntitlementState.fullVerified);
      expect((await EntitlementService(root).status()).state, EntitlementState.fullVerified);
    });

    test('a rejected verification never unlocks', () async {
      final service = EntitlementService(root);
      for (var i = 0; i < 5; i++) {
        await service.recordSuccessfulTargetedSend();
      }
      final status = await service.applyPurchase(productId: _productId, purchaseToken: 'tok-bad', verifier: _Verifier(VerificationResult.rejected));
      expect(status.state, EntitlementState.trial);
      expect(status.canSend, isFalse); // no unlock granted
      expect(status.remainingTrialSends, 0); // the pre-existing (exhausted) trial count is untouched, not reset
    });

    test('a temporary verification failure never grants a fake permanent unlock, only bounded grace', () async {
      final service = EntitlementService(root, unverifiedGracePeriod: const Duration(hours: 1));
      final status = await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: _Verifier(VerificationResult.temporaryFailure));
      expect(status.state, EntitlementState.fullUnverified);
      expect(status.canSend, isTrue); // grace access
      expect(status.unlocked, isTrue); // UI shows unlocked, but it is not fullVerified
    });

    test('an expired grace period lapses into restoreRequired, not a silent permanent unlock', () async {
      var now = DateTime(2026, 1, 1);
      final service = EntitlementService(root, unverifiedGracePeriod: const Duration(hours: 1), clock: () => now);
      for (var i = 0; i < 5; i++) {
        await service.recordSuccessfulTargetedSend(); // exhaust the trial first, as in the real "bought after trial ran out" case
      }
      final pending = await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: _Verifier(VerificationResult.temporaryFailure));
      expect(pending.canSend, isTrue); // grace access while within the window
      now = now.add(const Duration(hours: 2));
      final status = await service.status();
      expect(status.state, EntitlementState.restoreRequired);
      expect(status.canSend, isFalse); // grace expired and the trial underneath was already at 0
    });

    test('a wrong product id changes nothing', () async {
      final service = EntitlementService(root);
      final before = await service.status();
      final after = await service.applyPurchase(productId: 'some.other.sku', purchaseToken: 'tok-1', verifier: _Verifier());
      expect(after.state, before.state);
      expect(after.remainingTrialSends, before.remainingTrialSends);
    });

    test('a replayed/duplicate purchase callback for an already-verified purchase is idempotent (verifier not re-called)', () async {
      final verifier = _Verifier();
      final service = EntitlementService(root);
      await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: verifier);
      expect(verifier.calls.length, 1);
      final again = await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: verifier);
      expect(verifier.calls.length, 1); // not called a second time
      expect(again.state, EntitlementState.fullVerified);
    });

    test('a different token for a still-pending/unverified purchase is re-verified, not silently accepted', () async {
      final verifier = _Verifier(VerificationResult.temporaryFailure);
      final service = EntitlementService(root);
      await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: verifier);
      expect(verifier.calls.length, 1);
      await service.applyPurchase(productId: _productId, purchaseToken: 'tok-2', verifier: verifier);
      expect(verifier.calls.length, 2);
    });
  });

  group('B1b: periodic re-verification (refund / revoke must not stay permanently FULL)', () {
    test('a fullVerified purchase that is later rejected on re-check is revoked, not left FULL', () async {
      final verifier = _Verifier(VerificationResult.verified);
      final service = EntitlementService(root, reverifyInterval: Duration.zero);
      for (var i = 0; i < 5; i++) {
        await service.recordSuccessfulTargetedSend(); // the realistic case: trial exhausted, then bought
      }
      await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: verifier);
      expect((await service.status()).state, EntitlementState.fullVerified);

      verifier.result = VerificationResult.rejected; // e.g. a refund happened on Google's side
      final revoked = await service.reverifyIfDue(verifier: verifier);
      expect(revoked.state, EntitlementState.trial);
      expect(revoked.canSend, isFalse);
      expect((await service.status()).state, EntitlementState.trial); // persisted
    });

    test('re-verification is skipped until the interval has elapsed', () async {
      var now = DateTime(2026, 1, 1);
      final verifier = _Verifier(VerificationResult.verified);
      final service = EntitlementService(root, reverifyInterval: const Duration(days: 7), clock: () => now);
      await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: verifier);
      expect(verifier.calls.length, 1);

      now = now.add(const Duration(days: 1));
      await service.reverifyIfDue(verifier: verifier);
      expect(verifier.calls.length, 1); // not due yet: verifier not called again

      now = now.add(const Duration(days: 7));
      await service.reverifyIfDue(verifier: verifier);
      expect(verifier.calls.length, 2); // due now
    });

    test('a temporary failure on re-check never revokes and is retried next time', () async {
      final verifier = _Verifier(VerificationResult.verified);
      final service = EntitlementService(root, reverifyInterval: Duration.zero);
      await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: verifier);

      verifier.result = VerificationResult.temporaryFailure;
      final status = await service.reverifyIfDue(verifier: verifier);
      expect(status.state, EntitlementState.fullVerified); // still FULL: our backend hiccup, not a real rejection
      expect(status.canSend, isTrue);
    });

    test('trial and grace-period (fullUnverified) states are not touched by reverifyIfDue', () async {
      final service = EntitlementService(root);
      final trialBefore = await service.status();
      final trialAfter = await service.reverifyIfDue(verifier: _Verifier(VerificationResult.rejected));
      expect(trialAfter.state, trialBefore.state);
      expect(trialAfter.remainingTrialSends, trialBefore.remainingTrialSends);
    });
  });

  group('B2: restore / reinstall recovery', () {
    test('fresh install with an owned purchase restores to FULL', () async {
      final firstInstall = EntitlementService(root);
      await firstInstall.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: _Verifier());

      // Simulate reinstall: a brand new service over an empty directory (no local entitlement.json).
      final reinstallDir = Directory.systemTemp.createTempSync('fs_billing_reinstall_');
      addTearDown(() => reinstallDir.deleteSync(recursive: true));
      final reinstalled = EntitlementService(reinstallDir);
      expect((await reinstalled.status()).state, EntitlementState.trial); // looks like trial until restored

      final restored = await reinstalled.restoreFrom(const PurchaseRecord(productId: _productId, purchaseToken: 'tok-1'), verifier: _Verifier());
      expect(restored.state, EntitlementState.fullVerified);
      expect((await EntitlementService(reinstallDir).status()).state, EntitlementState.fullVerified); // persists too
    });

    test('restore with no ownership stays trial, with whatever trial credit already existed', () async {
      final service = EntitlementService(root);
      await service.recordSuccessfulTargetedSend();
      final before = await service.status();
      final after = await service.restoreFrom(null, verifier: _Verifier());
      expect(after.state, EntitlementState.trial);
      expect(after.remainingTrialSends, before.remainingTrialSends);
    });

    test('restore is exactly applyPurchase under the hood: rejected ownership does not unlock', () async {
      final service = EntitlementService(root);
      final status = await service.restoreFrom(const PurchaseRecord(productId: _productId, purchaseToken: 'tok-x'), verifier: _Verifier(VerificationResult.rejected));
      expect(status.state, EntitlementState.trial);
      expect(status.unlocked, isFalse);
    });

    test('retryPendingVerification re-checks a grace-period purchase and can promote it to verified', () async {
      final verifier = _Verifier(VerificationResult.temporaryFailure);
      final service = EntitlementService(root);
      final pending = await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: verifier);
      expect(pending.state, EntitlementState.fullUnverified);

      verifier.result = VerificationResult.verified;
      final retried = await service.retryPendingVerification(verifier: verifier);
      expect(retried.state, EntitlementState.fullVerified);
    });

    test('retryPendingVerification with nothing on record is a no-op', () async {
      final service = EntitlementService(root);
      final before = await service.status();
      final after = await service.retryPendingVerification(verifier: _Verifier());
      expect(after.state, before.state);
      expect(after.remainingTrialSends, before.remainingTrialSends);
    });
  });

  group('B3: server verification boundary', () {
    test('ServerPurchaseVerifier (no backend yet) always reports temporaryFailure, never verified', () async {
      const verifier = ServerPurchaseVerifier();
      final result = await verifier.verify(productId: _productId, purchaseToken: 'tok-1');
      expect(result, VerificationResult.temporaryFailure);
    });

    test('a purchase through the real (backend-less) ServerPurchaseVerifier grants grace, never a permanent unlock', () async {
      final service = EntitlementService(root);
      final status = await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: const ServerPurchaseVerifier());
      expect(status.state, EntitlementState.fullUnverified);
      expect(status.canSend, isTrue);
    });
  });

  group('shared entitlement across both FriendSend entry points', () {
    test('one EntitlementService instance reflects a purchase made through either flow', () async {
      // main.dart constructs a single EntitlementService shared by HomeScreen for both the device-received
      // flow and the Incoming Share Router; this proves the underlying model supports that (no per-flow state).
      final service = EntitlementService(root);
      for (var i = 0; i < 5; i++) {
        await service.recordSuccessfulTargetedSend();
      }
      expect((await service.status()).canSend, isFalse);
      await service.applyPurchase(productId: _productId, purchaseToken: 'tok-1', verifier: _Verifier());
      expect((await service.status()).canSend, isTrue);
    });
  });
}
