import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/handoff/handoff_controller.dart';
import 'package:friendsend/identity/device_identity.dart';
import 'package:friendsend/monetization/billing_adapter.dart';
import 'package:friendsend/monetization/entitlement_service.dart';
import 'package:friendsend/platform/incoming_share.dart';
import 'package:friendsend/platform/share_bridge.dart';
import 'package:friendsend/platform/share_targets.dart';
import 'package:friendsend/receiver/receiver_interface.dart';
import 'package:friendsend/receiver/receiver_server.dart' show ReceiverEvent, ReceiverEventKind;
import 'package:friendsend/receiver/temp_cache.dart';
import 'package:friendsend/security/desktop_trust_store.dart';
import 'package:friendsend/security/secure_pairing_manager.dart';
import 'package:friendsend/ui/home_screen.dart';
import 'package:friendsend/ui/theme/fs_theme.dart';

/// An in-memory EntitlementSource a test can drive directly, without touching the filesystem.
class _FakeEntitlement implements EntitlementSource {
  _FakeEntitlement({int remaining = 5, bool unlocked = false}) : _status = EntitlementStatus(unlocked: unlocked, remainingTrialSends: remaining);
  EntitlementStatus _status;
  final _controller = StreamController<EntitlementStatus>.broadcast();
  int recordCalls = 0;
  int unlockCalls = 0;

  @override
  Future<EntitlementStatus> status() async => _status;

  @override
  Stream<EntitlementStatus> get changes => _controller.stream;

  @override
  Future<EntitlementStatus> recordSuccessfulTargetedSend() async {
    recordCalls++;
    if (!_status.unlocked && _status.remainingTrialSends > 0) {
      _status = EntitlementStatus(unlocked: false, remainingTrialSends: _status.remainingTrialSends - 1);
      _controller.add(_status);
    }
    return _status;
  }

  @override
  Future<EntitlementStatus> unlock() async {
    unlockCalls++;
    _status = const EntitlementStatus(unlocked: true, remainingTrialSends: 0);
    _controller.add(_status);
    return _status;
  }
}

class _FakeBilling implements BillingAdapter {
  PurchaseOutcome outcome = PurchaseOutcome.purchased;
  String? price = '1,99 €';
  int purchaseCalls = 0;

  @override
  Future<String?> lifetimeUnlockPrice() async => price;

  @override
  Future<PurchaseOutcome> purchaseLifetimeUnlock() async {
    purchaseCalls++;
    return outcome;
  }
}

class _Receiver implements FriendSendReceiverLike {
  final _c = StreamController<ReceiverEvent>.broadcast();
  @override
  Stream<ReceiverEvent> get events => _c.stream;
  @override
  void requestCancel(String handoffId) {}
  void emit(ReceiverEvent e) => _c.add(e);
}

class _Bridge extends ShareBridge {
  TargetShareResult targetResult = TargetShareResult.opened;
  final targetCalls = <String>[];
  @override
  Future<TargetShareResult> shareToTarget({required String path, required String displayName, required String mimeType, required String targetId}) async {
    targetCalls.add(targetId);
    return targetResult;
  }
}

class _Targets implements ShareTargetProvider {
  @override
  Future<List<ShareTarget>> targetsFor(String mimeType) async => const [ShareTarget(id: 'a', label: 'Messenger')];
}

class _Source implements IncomingShareSource {
  _Source([this.initial]);
  String? initial;
  @override
  Future<String?> takeInitial() async {
    final v = initial;
    initial = null;
    return v;
  }

  @override
  Stream<String> get updates => const Stream.empty();
}

class _Sharer implements TextSharer {
  TargetShareResult targetResult = TargetShareResult.opened;
  ShareResult sheetResult = ShareResult.opened;
  final targetCalls = <String>[];
  final sheetCalls = <String>[];
  @override
  Future<TargetShareResult> shareTextToTarget({required String text, required String targetId}) async {
    targetCalls.add(targetId);
    return targetResult;
  }

  @override
  Future<ShareResult> shareText(String text) async {
    sheetCalls.add(text);
    return sheetResult;
  }

  @override
  Future<TargetShareResult> shareFileToTarget({required String path, required String displayName, required String mimeType, required String targetId}) async {
    targetCalls.add(targetId);
    return targetResult;
  }

  @override
  Future<ShareResult> shareFileViaSheet({required String path, required String displayName, required String mimeType}) async {
    sheetCalls.add(path);
    return sheetResult;
  }
}

void main() {
  group('device-received flow', () {
    late Directory root;
    late _Receiver receiver;
    late _Bridge bridge;
    late HandoffController c;
    late File file;
    late SecurePairingManager pairing;

    setUp(() {
      root = Directory.systemTemp.createTempSync('fs_gate_');
      receiver = _Receiver();
      bridge = _Bridge();
      file = File('${root.path}/h1.bin')..writeAsBytesSync(List.filled(10, 1));
      c = HandoffController(receiver: receiver, tempCache: TempCache(root), shareBridge: bridge);
      pairing = SecurePairingManager(
        deviceId: 'd1', displayName: 'Phone', friendSendTlsSpkiSha256: List<int>.filled(32, 1), friendSendEndpointHost: '127.0.0.1', friendSendEndpointPort: 1,
        trustStore: DesktopTrustStore(root),
      );
    });

    tearDown(() async {
      await c.dispose();
      if (root.existsSync()) root.deleteSync(recursive: true);
    });

    Future<void> pump(WidgetTester t, {required EntitlementSource entitlement, BillingAdapter billing = const UnavailableBillingAdapter()}) async {
      t.view.physicalSize = const Size(780, 1688);
      t.view.devicePixelRatio = 2;
      addTearDown(t.view.reset);
      await t.pumpWidget(MaterialApp(
        theme: FsTheme.build(Brightness.dark),
        home: HomeScreen(
          identity: const DeviceIdentity(deviceId: 'd1', displayName: 'Phone'), pairingManager: pairing, controller: c,
          targetProvider: _Targets(), entitlement: entitlement, billing: billing,
        ),
      ));
      await t.pump();
    }

    Future<void> flush(WidgetTester t) async {
      for (var i = 0; i < 4; i++) {
        await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 15)));
        await t.pump();
      }
    }

    ReceiverEvent received() => ReceiverEvent(handoffId: 'h1', kind: ReceiverEventKind.received, bytesReceived: 10, totalBytes: 10, file: file, mimeType: 'video/mp4', preferredFilename: 'holiday.mp4');

    testWidgets('with sends remaining, Choose app opens the picker as before and shows the remaining count', (t) async {
      final entitlement = _FakeEntitlement(remaining: 3);
      c.markPaired();
      await pump(t, entitlement: entitlement);
      await flush(t);
      c.receiver;
      receiver.emit(received());
      await flush(t);
      expect(find.byKey(const Key('trial_remaining_badge')), findsOneWidget);
      expect(find.text('3 free sends remaining'), findsOneWidget);
      await t.tap(find.byKey(const Key('choose_app_button')));
      await flush(t);
      await t.pumpAndSettle();
      expect(find.byKey(const Key('target_a')), findsOneWidget); // the picker really opened
      expect(find.byKey(const Key('unlock_title')), findsNothing);
    });

    testWidgets('a successful targeted send counts against the trial exactly once', (t) async {
      final entitlement = _FakeEntitlement(remaining: 3);
      c.markPaired();
      await pump(t, entitlement: entitlement);
      receiver.emit(received());
      await flush(t);
      await t.tap(find.byKey(const Key('choose_app_button')));
      await flush(t);
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      await flush(t);
      expect(bridge.targetCalls, ['a']);
      expect(entitlement.recordCalls, 1); // exactly one, not for the earlier attempt or any retry
    });

    testWidgets('a failed targeted send does not count against the trial', (t) async {
      bridge.targetResult = TargetShareResult.targetUnavailable;
      final entitlement = _FakeEntitlement(remaining: 3);
      c.markPaired();
      await pump(t, entitlement: entitlement);
      receiver.emit(received());
      await flush(t);
      await t.tap(find.byKey(const Key('choose_app_button')));
      await flush(t);
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      await flush(t);
      expect(entitlement.recordCalls, 0);
    });

    testWidgets('cancelling the picker without choosing does not count against the trial', (t) async {
      final entitlement = _FakeEntitlement(remaining: 3);
      c.markPaired();
      await pump(t, entitlement: entitlement);
      receiver.emit(received());
      await flush(t);
      await t.tap(find.byKey(const Key('choose_app_button')));
      await flush(t);
      await t.pumpAndSettle();
      await t.tapAt(const Offset(10, 10)); // dismiss the sheet
      await t.pumpAndSettle();
      await flush(t);
      expect(entitlement.recordCalls, 0);
    });

    testWidgets('an exhausted trial shows the unlock screen instead of the picker, with no handoff', (t) async {
      final entitlement = _FakeEntitlement(remaining: 0);
      c.markPaired();
      await pump(t, entitlement: entitlement);
      receiver.emit(received());
      await flush(t);
      await t.tap(find.byKey(const Key('choose_app_button')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('unlock_title')), findsOneWidget);
      expect(find.text('You’ve used your 5 free sends'), findsOneWidget);
      expect(bridge.targetCalls, isEmpty);
      expect(c.current.state, AppState.received); // chooseApp() was never called on the controller
    });

    testWidgets('Not now dismisses the unlock screen back to the received screen, still blocked', (t) async {
      final entitlement = _FakeEntitlement(remaining: 0);
      c.markPaired();
      await pump(t, entitlement: entitlement);
      receiver.emit(received());
      await flush(t);
      await t.tap(find.byKey(const Key('choose_app_button')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('unlock_not_now')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('choose_app_button')), findsOneWidget);
      expect(find.byKey(const Key('unlock_title')), findsNothing);
    });

    testWidgets('a successful purchase unlocks immediately and the send then goes through', (t) async {
      final entitlement = _FakeEntitlement(remaining: 0);
      final billing = _FakeBilling()..outcome = PurchaseOutcome.purchased;
      c.markPaired();
      await pump(t, entitlement: entitlement, billing: billing);
      receiver.emit(received());
      await flush(t);
      await t.tap(find.byKey(const Key('choose_app_button')));
      await t.pumpAndSettle();
      expect(find.text('Unlock for 1,99 €'), findsOneWidget);
      await t.tap(find.byKey(const Key('unlock_buy_button')));
      await t.pumpAndSettle();
      expect(billing.purchaseCalls, 1);
      expect(entitlement.unlockCalls, 1);
      expect(find.byKey(const Key('unlock_title')), findsNothing);
      expect(find.byKey(const Key('choose_app_button')), findsOneWidget); // back to normal, unblocked
      await t.tap(find.byKey(const Key('choose_app_button')));
      await flush(t);
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      expect(bridge.targetCalls, ['a']);
    });

    testWidgets('a cancelled or failed purchase leaves the trial exhausted and shows a reason on failure', (t) async {
      final entitlement = _FakeEntitlement(remaining: 0);
      final billing = _FakeBilling()..outcome = PurchaseOutcome.cancelled;
      c.markPaired();
      await pump(t, entitlement: entitlement, billing: billing);
      receiver.emit(received());
      await flush(t);
      await t.tap(find.byKey(const Key('choose_app_button')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('unlock_buy_button')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('unlock_title')), findsOneWidget); // still there: not unlocked
      expect(entitlement.unlockCalls, 0);

      billing.outcome = PurchaseOutcome.failed;
      await t.tap(find.byKey(const Key('unlock_buy_button')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('unlock_error')), findsOneWidget);
      expect(entitlement.unlockCalls, 0);
    });

    testWidgets('an unlocked user has unlimited sends and no trial badge', (t) async {
      final entitlement = _FakeEntitlement(remaining: 0, unlocked: true);
      c.markPaired();
      await pump(t, entitlement: entitlement);
      receiver.emit(received());
      await flush(t);
      expect(find.byKey(const Key('trial_remaining_badge')), findsNothing);
      await t.tap(find.byKey(const Key('choose_app_button')));
      await flush(t);
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      expect(bridge.targetCalls, ['a']);
      expect(find.byKey(const Key('unlock_title')), findsNothing);
    });
  });

  group('incoming-share flow', () {
    late Directory root;
    late HandoffController c;
    late SecurePairingManager pairing;
    late _Sharer sharer;

    setUp(() {
      root = Directory.systemTemp.createTempSync('fs_gate_incoming_');
      c = HandoffController(receiver: _NoopReceiver(), tempCache: TempCache(root), shareBridge: ShareBridge());
      pairing = SecurePairingManager(
        deviceId: 'd1', displayName: 'Phone', friendSendTlsSpkiSha256: List<int>.filled(32, 1), friendSendEndpointHost: '127.0.0.1', friendSendEndpointPort: 1,
        trustStore: DesktopTrustStore(root),
      );
      sharer = _Sharer();
    });

    tearDown(() async {
      await c.dispose();
      if (root.existsSync()) root.deleteSync(recursive: true);
    });

    Future<void> pump(WidgetTester t, {required EntitlementSource entitlement, BillingAdapter billing = const UnavailableBillingAdapter()}) async {
      t.view.physicalSize = const Size(780, 1688);
      t.view.devicePixelRatio = 2;
      addTearDown(t.view.reset);
      await t.pumpWidget(MaterialApp(
        theme: FsTheme.build(Brightness.dark),
        home: HomeScreen(
          identity: const DeviceIdentity(deviceId: 'd1', displayName: 'Phone'), pairingManager: pairing, controller: c,
          targetProvider: _Targets(), incomingShares: _Source('https://example.com/a'), textSharer: sharer,
          entitlement: entitlement, billing: billing,
        ),
      ));
      await t.pump();
      await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
      await t.pump();
    }

    testWidgets('a targeted send from an incoming share counts against the trial', (t) async {
      final entitlement = _FakeEntitlement(remaining: 2);
      await pump(t, entitlement: entitlement);
      expect(find.byKey(const Key('target_a')), findsOneWidget);
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      expect(sharer.targetCalls, ['a']);
      expect(entitlement.recordCalls, 1);
    });

    testWidgets('the system Sharesheet (More apps) never counts against the trial', (t) async {
      final entitlement = _FakeEntitlement(remaining: 2);
      await pump(t, entitlement: entitlement);
      await t.tap(find.byKey(const Key('incoming_more_apps')));
      await t.pumpAndSettle();
      expect(sharer.sheetCalls, ['https://example.com/a']);
      expect(entitlement.recordCalls, 0);
    });

    testWidgets('an exhausted trial blocks both the target tiles and More apps, showing the unlock screen', (t) async {
      final entitlement = _FakeEntitlement(remaining: 0);
      await pump(t, entitlement: entitlement);
      expect(find.byKey(const Key('incoming_title')), findsOneWidget); // the shared text is still visible
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      expect(sharer.targetCalls, isEmpty);
      expect(find.byKey(const Key('unlock_title')), findsOneWidget);
    });

    testWidgets('More apps is blocked too once the trial is exhausted', (t) async {
      final entitlement = _FakeEntitlement(remaining: 0);
      await pump(t, entitlement: entitlement);
      await t.tap(find.byKey(const Key('incoming_more_apps')));
      await t.pumpAndSettle();
      expect(sharer.sheetCalls, isEmpty);
      expect(find.byKey(const Key('unlock_title')), findsOneWidget);
    });

    testWidgets('Not now on an incoming-triggered unlock returns to the same shared text', (t) async {
      final entitlement = _FakeEntitlement(remaining: 0);
      await pump(t, entitlement: entitlement);
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('unlock_not_now')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('incoming_title')), findsOneWidget);
      expect(t.widget<Text>(find.byKey(const Key('incoming_text'))).data, 'https://example.com/a');
    });

    testWidgets('unlocking from the incoming flow lets the same share go through afterwards', (t) async {
      final entitlement = _FakeEntitlement(remaining: 0);
      final billing = _FakeBilling()..outcome = PurchaseOutcome.purchased;
      await pump(t, entitlement: entitlement, billing: billing);
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('unlock_buy_button')));
      await t.pumpAndSettle();
      expect(entitlement.unlockCalls, 1);
      expect(find.byKey(const Key('incoming_title')), findsOneWidget);
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      expect(sharer.targetCalls, ['a']);
    });

    testWidgets('the Incoming Share Router cannot bypass a trial already used up by the device-received flow', (t) async {
      // The same EntitlementSource instance is shared by both entry points in the real app (main.dart);
      // this proves a single shared instance blocks both, regardless of which one exhausted it.
      final entitlement = _FakeEntitlement(remaining: 0);
      await pump(t, entitlement: entitlement);
      expect(find.byKey(const Key('unlock_title')), findsNothing); // not shown until a send is attempted
      await t.tap(find.byKey(const Key('target_a')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('unlock_title')), findsOneWidget);
    });
  });
}

class _NoopReceiver implements FriendSendReceiverLike {
  @override
  Stream<ReceiverEvent> get events => const Stream.empty();
  @override
  void requestCancel(String handoffId) {}
}
