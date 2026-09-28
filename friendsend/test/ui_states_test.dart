import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/handoff/handoff_controller.dart';
import 'package:friendsend/identity/device_identity.dart';
import 'package:friendsend/platform/share_bridge.dart';
import 'package:friendsend/platform/share_targets.dart';
import 'package:friendsend/protocol/protocol.dart';
import 'package:friendsend/receiver/receiver_interface.dart';
import 'package:friendsend/receiver/receiver_server.dart' show ReceiverEvent, ReceiverEventKind;
import 'package:friendsend/receiver/temp_cache.dart';
import 'package:friendsend/security/desktop_trust_store.dart';
import 'package:friendsend/security/secure_pairing_manager.dart';
import 'package:friendsend/ui/home_screen.dart';
import 'package:friendsend/ui/theme/fs_theme.dart';

class _Receiver implements FriendSendReceiverLike {
  final _c = StreamController<ReceiverEvent>.broadcast();
  final cancelled = <String>[];
  @override
  Stream<ReceiverEvent> get events => _c.stream;
  @override
  void requestCancel(String handoffId) => cancelled.add(handoffId);
  void emit(ReceiverEvent e) => _c.add(e);
}

class _Bridge extends ShareBridge {
  ShareResult result = ShareResult.opened;
  int calls = 0;
  @override
  Future<ShareResult> shareFile({required String path, required String displayName, required String mimeType}) async {
    calls++;
    return result;
  }
}

class _Targets implements ShareTargetProvider {
  @override
  Future<List<ShareTarget>> targetsFor(String mimeType) async => const [
    ShareTarget(id: 'a', label: 'Messenger'),
    ShareTarget(id: 'b', label: 'WhatsApp'),
    ShareTarget(id: 'c', label: 'A very long application label that must ellipsize'),
  ];
}

ReceiverEvent _progress(int got, int total, {String name = 'holiday.mp4'}) =>
    ReceiverEvent(handoffId: 'h1', kind: ReceiverEventKind.progress, bytesReceived: got, totalBytes: total, mimeType: 'video/mp4', preferredFilename: name);

void main() {
  late Directory root;
  late _Receiver receiver;
  late _Bridge bridge;
  late HandoffController c;
  late File file;
  late SecurePairingManager pairing;

  setUp(() {
    root = Directory.systemTemp.createTempSync('fs_ui_states_');
    receiver = _Receiver();
    bridge = _Bridge();
    file = File('${root.path}/h1.bin')..writeAsBytesSync(List.filled(10, 1));
    c = HandoffController(receiver: receiver, tempCache: TempCache(root), shareBridge: bridge);
    pairing = SecurePairingManager(
      deviceId: 'd1',
      displayName: 'Phone',
      friendSendTlsSpkiSha256: List<int>.filled(32, 1),
      friendSendEndpointHost: '127.0.0.1',
      friendSendEndpointPort: 1,
      trustStore: DesktopTrustStore(root),
    );
  });

  tearDown(() async {
    await c.dispose();
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  Future<void> pump(WidgetTester t, {Brightness b = Brightness.dark, Size size = const Size(390, 844), DesktopTrustStore? store, ShareTargetProvider? targets}) async {
    t.view.physicalSize = size * 2;
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    await t.pumpWidget(MaterialApp(
      theme: FsTheme.build(b),
      home: HomeScreen(identity: const DeviceIdentity(deviceId: 'd1', displayName: 'Phone'), pairingManager: pairing, controller: c, trustStore: store, targetProvider: targets ?? const NoShareTargets()),
    ));
    await t.pump();
  }

  // The controller is built in setUp (outside the widget-test fake-async zone), so its stream events need the
  // real event loop to turn once before a frame is pumped.
  Future<void> flush(WidgetTester t) async {
    for (var i = 0; i < 4; i++) {
      await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 15)));
      await t.pump();
    }
  }

  Future<void> emit(WidgetTester t, ReceiverEvent e) async {
    receiver.emit(e);
    await flush(t);
  }

  ReceiverEvent received({String name = 'holiday.mp4'}) => ReceiverEvent(handoffId: 'h1', kind: ReceiverEventKind.received, bytesReceived: 198000000, totalBytes: 198000000, file: file, mimeType: 'video/mp4', preferredFilename: name);

  for (final b in Brightness.values) {
    for (final size in const [Size(360, 640), Size(360, 800), Size(412, 915)]) {
      testWidgets('every state renders without overflow (${b.name}, ${size.width.toInt()}x${size.height.toInt()}, long filename)', (t) async {
        final long = '${'very_long_holiday_video_name_' * 5}.mp4';
        await pump(t, b: b, size: size);
        expect(find.text('Connect to Rýchlik'), findsOneWidget); // unpaired
        c.beginPairing();
        await flush(t);
        expect(find.byKey(const Key('pairing_progress')), findsOneWidget);
        c.markPaired();
        await flush(t);
        expect(find.text('Ready to receive'), findsOneWidget);
        await emit(t, _progress(100, 198000000, name: long));
        expect(find.byKey(const Key('receiving_message')), findsOneWidget);
        await emit(t, _progress(198000000, 198000000, name: long));
        expect(find.byKey(const Key('verifying_message')), findsOneWidget);
        await emit(t, received(name: long));
        expect(find.text('Video ready'), findsOneWidget);
        c.chooseApp();
        await flush(t);
        await t.pumpAndSettle();
        expect(find.byKey(const Key('picker_title')), findsOneWidget);
        await t.tap(find.byKey(const Key('picker_discard')));
        await t.pumpAndSettle();
        await flush(t);
        expect(find.text('Ready to receive'), findsOneWidget);
        await emit(t, _progress(100, 1000));
        await emit(t, ReceiverEvent(handoffId: 'h1', kind: ReceiverEventKind.cancelled, bytesReceived: 100, totalBytes: 1000));
        expect(find.byKey(const Key('cancelled_message')), findsOneWidget);
        await t.tap(find.byKey(const Key('done_button')));
        await flush(t);
        await emit(t, _progress(1000, 1000));
        await emit(t, ReceiverEvent(handoffId: 'h1', kind: ReceiverEventKind.failed, bytesReceived: 1000, totalBytes: 1000, errorCode: HandoffErrorCode.integrityMismatch));
        expect(find.text('File verification failed'), findsOneWidget);
        expect(t.takeException(), isNull);
      });
    }
  }

  testWidgets('receiving shows real size/progress and a measured speed only after two events; Cancel asks the receiver', (t) async {
    c.markPaired();
    await pump(t);
    await emit(t, _progress(50, 200));
    expect(find.text('25 %'), findsOneWidget);
    expect(find.byKey(const Key('receiving_speed')), findsNothing);
    expect(find.text('holiday.mp4'), findsOneWidget);
    await t.tap(find.byKey(const Key('cancel_button')));
    expect(receiver.cancelled, ['h1']);
  });

  testWidgets('verifying shows the step list and never offers Choose app', (t) async {
    c.markPaired();
    await pump(t);
    await emit(t, _progress(1000, 1000));
    expect(find.text('Verifying file'), findsOneWidget);
    expect(find.byKey(const Key('choose_app_button')), findsNothing);
    expect(find.text('You can’t share the file until it has been verified.'), findsOneWidget);
  });

  testWidgets('Choose app opens the picker; More apps opens the system Sharesheet and only then shows handoff accepted', (t) async {
    c.markPaired();
    await pump(t, targets: _Targets());
    await emit(t, _progress(1000, 1000));
    await emit(t, received());
    await t.tap(find.byKey(const Key('choose_app_button')));
    await flush(t);
    await t.pumpAndSettle();
    expect(find.text('Where do you want to send it?'), findsOneWidget);
    expect(find.text('Messenger'), findsOneWidget);
    expect(find.text('WhatsApp'), findsOneWidget);
    expect(find.text('More apps…'), findsOneWidget);
    expect(find.text('Open Android Sharesheet'), findsOneWidget);
    expect(bridge.calls, 0); // nothing handed off yet
    expect(c.current.state, AppState.choosingTarget);
    await t.tap(find.byKey(const Key('picker_more_apps')));
    await t.pumpAndSettle();
    await flush(t);
    expect(bridge.calls, 1);
    expect(c.current.state, AppState.handoffAccepted);
    expect(find.text('Android Sharesheet opened'), findsOneWidget);
    expect(find.textContaining('can’t see whether it was delivered'), findsOneWidget);
    expect(find.textContaining('Delivered'), findsNothing);
    await t.tap(find.byKey(const Key('done_button')));
    await flush(t);
    expect(find.text('Ready to receive'), findsOneWidget);
  });

  testWidgets('a failed Sharesheet open keeps the file and says so', (t) async {
    bridge.result = ShareResult.platformError;
    c.markPaired();
    await pump(t);
    await emit(t, received());
    await t.tap(find.byKey(const Key('choose_app_button')));
    await flush(t);
    await t.pumpAndSettle();
    expect(find.byKey(const Key('picker_no_targets')), findsOneWidget);
    await t.tap(find.byKey(const Key('picker_more_apps')));
    await t.pumpAndSettle();
    await flush(t);
    expect(c.current.state, AppState.received);
    expect(find.textContaining('Couldn’t open the Android Sharesheet'), findsOneWidget);
    expect(find.byKey(const Key('received_filename')), findsOneWidget);
  });

  testWidgets('dismissing the picker returns to received without touching the file', (t) async {
    c.markPaired();
    await pump(t);
    await emit(t, received());
    await t.tap(find.byKey(const Key('choose_app_button')));
    await flush(t);
    await t.pumpAndSettle();
    await t.tapAt(const Offset(195, 60)); // scrim
    await t.pumpAndSettle();
    expect(c.current.state, AppState.received);
    expect(file.existsSync(), isTrue);
  });

  testWidgets('Discard on the received screen deletes the temp file (TempCache) and returns to ready', (t) async {
    c.markPaired();
    await pump(t);
    await emit(t, received());
    await t.tap(find.byKey(const Key('discard_button')));
    await flush(t);
    expect(file.existsSync(), isFalse);
    expect(find.text('Ready to receive'), findsOneWidget);
  });

  testWidgets('trusted computer: real trust store record shown, Forget needs confirmation and returns to pairing', (t) async {
    final store = DesktopTrustStore(Directory('${root.path}/trust')..createSync());
    await t.runAsync(() => store.upsert(TrustedDesktop(desktopInstanceId: 'desk-1', desktopPublicSigningKey: List<int>.generate(32, (i) => i + 0xb0), pairedAtUtc: '2026-09-28T06:20:00Z')));
    c.markPaired();
    await pump(t, store: store);
    await flush(t);
    await t.tap(find.byKey(const Key('trusted_computer_card')));
    await t.pumpAndSettle();
    expect(find.text('Trusted computer'), findsOneWidget);
    expect(find.byKey(const Key('trusted_identity')), findsOneWidget);
    await t.tap(find.byKey(const Key('forget_button')));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const Key('forget_cancel')));
    await t.pumpAndSettle();
    expect((await t.runAsync(() => store.allDesktops()))!.length, 1); // cancel forgets nothing
    await t.tap(find.byKey(const Key('forget_button')));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const Key('forget_confirm')));
    await flush(t);
    await t.pumpAndSettle();
    await flush(t);
    expect((await t.runAsync(() => store.allDesktops()))!, isEmpty);
    expect(find.text('Connect to Rýchlik'), findsOneWidget);
  });
}
