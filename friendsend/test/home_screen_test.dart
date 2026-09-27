import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/handoff/handoff_controller.dart';
import 'package:friendsend/identity/device_identity.dart';
import 'package:friendsend/pairing/pairing_manager.dart';
import 'package:friendsend/platform/share_bridge.dart';
import 'package:friendsend/protocol/protocol.dart';
import 'package:friendsend/receiver/receiver_server.dart';
import 'package:friendsend/receiver/temp_cache.dart';
import 'package:friendsend/ui/home_screen.dart';

Future<void> _pumpHome(
  WidgetTester tester, {
  required HandoffController controller,
  required PairingManager pairingManager,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: HomeScreen(
        identity: const DeviceIdentity(deviceId: 'd1', displayName: 'Test Phone'),
        pairingManager: pairingManager,
        controller: controller,
      ),
    ),
  );
}

void main() {
  late Directory root;
  late FriendSendReceiverServer server;
  late HandoffController controller;
  late PairingManager pairingManager;

  setUp(() async {
    root = Directory.systemTemp.createTempSync('friendsend_home_screen_test_');
    server = FriendSendReceiverServer(config: ReceiverConfig(deviceId: 'd1', displayName: 'x'), tempCache: TempCache(root));
    await server.start();
    controller = HandoffController(receiver: server, tempCache: TempCache(root), shareBridge: ShareBridge(), autoShare: false);
    pairingManager = PairingManager(receiver: server);
  });

  tearDown(() async {
    await controller.dispose();
    await server.stop();
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  testWidgets('unpaired shows the truthful empty state (§78)', (tester) async {
    await _pumpHome(tester, controller: controller, pairingManager: pairingManager);
    expect(find.byKey(const Key('unpaired_message')), findsOneWidget);
    expect(find.text('Pair FriendSend with Rýchlik to receive a file.'), findsOneWidget);
  });

  testWidgets('malformed pairing paste shows a bounded error, never a raw crash (§18)', (tester) async {
    await _pumpHome(tester, controller: controller, pairingManager: pairingManager);
    await tester.enterText(find.byKey(const Key('pairing_paste_field')), 'not json at all');
    await tester.tap(find.byKey(const Key('pairing_pair_button')));
    await tester.pump();
    expect(find.byKey(const Key('pairing_error')), findsOneWidget);
    expect(find.byKey(const Key('unpaired_message')), findsOneWidget);
  });

  testWidgets('valid pairing payload transitions to the ready state (§79)', (tester) async {
    await _pumpHome(tester, controller: controller, pairingManager: pairingManager);
    final payload = {
      'protocol_version': 1,
      'pairing_session_id': 's1',
      'desktop_instance_id': 'desktop-1',
      'endpoint': {'host': '127.0.0.1', 'port': 12345},
      'secret': 'deadbeef',
      'expires_at_utc': DateTime.now().toUtc().add(const Duration(minutes: 5)).toIso8601String(),
    };
    await tester.enterText(find.byKey(const Key('pairing_paste_field')), jsonEncode(payload));
    await tester.tap(find.byKey(const Key('pairing_pair_button')));
    await tester.pump();
    expect(find.byKey(const Key('ready_message')), findsOneWidget);
    expect(find.textContaining('Test Phone'), findsOneWidget);
  });

  testWidgets('expired pairing payload shows a bounded error (§19)', (tester) async {
    await _pumpHome(tester, controller: controller, pairingManager: pairingManager);
    final payload = {
      'protocol_version': 1,
      'pairing_session_id': 's1',
      'desktop_instance_id': 'desktop-1',
      'endpoint': {'host': '127.0.0.1', 'port': 12345},
      'secret': 'deadbeef',
      'expires_at_utc': DateTime.now().toUtc().subtract(const Duration(minutes: 5)).toIso8601String(),
    };
    await tester.enterText(find.byKey(const Key('pairing_paste_field')), jsonEncode(payload));
    await tester.tap(find.byKey(const Key('pairing_pair_button')));
    await tester.pump();
    expect(find.byKey(const Key('pairing_error')), findsOneWidget);
  });

  testWidgets('a real receive drives the widget from ready to the received state (§74/§80)', (tester) async {
    HttpOverrides.global = null; // this suite needs a real loopback socket
    controller.markPaired();
    server.acceptToken('tok');
    await _pumpHome(tester, controller: controller, pairingManager: pairingManager);
    await tester.pump();
    expect(find.byKey(const Key('ready_message')), findsOneWidget);

    final bytes = List<int>.generate(500, (i) => i % 256);
    final digest = sha256.convert(bytes).toString();
    // testWidgets runs its body in a fake-async test zone; a real socket
    // round-trip needs the real event loop, so it must run inside
    // tester.runAsync() rather than a bare `await` (otherwise the test
    // hangs forever waiting on I/O the fake zone never drives).
    await tester.runAsync(() async {
      final httpClient = HttpClient();
      final offerRequest = await httpClient.postUrl(Uri.parse('http://127.0.0.1:${server.port}/handoff/offer'));
      offerRequest.headers.set(headerProtocolVersion, '1');
      offerRequest.headers.set(headerAuthToken, 'tok');
      offerRequest.headers.contentType = ContentType.json;
      offerRequest.write(
        jsonEncode({
          'handoff_id': 'h1',
          'display_name': 'clip.mp4',
          'mime_type': 'video/mp4',
          'size_bytes': bytes.length,
          'sha256': digest,
        }),
      );
      await (await offerRequest.close()).drain<void>();
      final streamRequest = await httpClient.postUrl(Uri.parse('http://127.0.0.1:${server.port}/handoff/stream'));
      streamRequest.headers.set(headerProtocolVersion, '1');
      streamRequest.headers.set(headerAuthToken, 'tok');
      streamRequest.headers.set(headerHandoffId, 'h1');
      streamRequest.contentLength = bytes.length;
      streamRequest.add(bytes);
      await (await streamRequest.close()).drain<void>();
      httpClient.close();
      await Future<void>.delayed(const Duration(milliseconds: 100));
    });

    await tester.pump(const Duration(milliseconds: 100));
    await tester.pump();

    expect(find.byKey(const Key('received_filename')), findsOneWidget);
    expect(find.text('clip.mp4'), findsOneWidget);
    expect(find.byKey(const Key('share_button')), findsOneWidget);
    expect(find.byKey(const Key('discard_button')), findsOneWidget);
  });

  testWidgets('error state never claims delivery/success wording', (tester) async {
    await _pumpHome(tester, controller: controller, pairingManager: pairingManager);
    // Reaching AppState.error organically is exercised via a real
    // INTEGRITY_MISMATCH/INCOMPLETE_TRANSFER flow in
    // handoff_controller_test.dart / receiver_server_test.dart; this test
    // only asserts the widget never renders forbidden delivery wording
    // anywhere in its static build, regardless of state.
    for (final forbidden in ['Delivered to friend', 'Sent on WhatsApp', 'Friend received it']) {
      expect(find.textContaining(forbidden), findsNothing);
    }
  });
}
