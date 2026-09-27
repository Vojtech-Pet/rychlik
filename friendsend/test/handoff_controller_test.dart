import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/handoff/handoff_controller.dart';
import 'package:friendsend/platform/share_bridge.dart';
import 'package:friendsend/protocol/protocol.dart';
import 'package:friendsend/receiver/receiver_server.dart';
import 'package:friendsend/receiver/temp_cache.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  // TestWidgetsFlutterBinding installs a fake HttpOverrides that makes
  // every HttpClient request return 400 without touching the network --
  // this suite needs a REAL loopback socket (it drives the actual
  // receiver server), so restore real networking for these tests.
  HttpOverrides.global = null;

  late Directory root;
  late TempCache cache;
  late FriendSendReceiverServer server;
  late HandoffController controller;

  setUp(() async {
    root = Directory.systemTemp.createTempSync('friendsend_handoff_controller_test_');
    cache = TempCache(root);
    server = FriendSendReceiverServer(config: ReceiverConfig(deviceId: 'd1', displayName: 'x'), tempCache: cache);
    await server.start();
    // No real Android platform attached in a widget/unit test -- the
    // MethodChannel call must fail bounded (MissingPluginException),
    // never crash the test (Prompt A14 §144: UI/widget tests must not
    // require real Android platform code).
    controller = HandoffController(receiver: server, tempCache: cache, shareBridge: ShareBridge(), autoShare: false);
  });

  tearDown(() async {
    await controller.dispose();
    await server.stop();
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  test('starts unpaired', () {
    expect(controller.current.state, AppState.unpaired);
  });

  test('markPaired transitions to paired', () {
    controller.markPaired();
    expect(controller.current.state, AppState.paired);
  });

  test('a real receive drives the controller from paired -> receiving -> received', () async {
    controller.markPaired();
    server.acceptToken('tok');
    final states = <HandoffUiSnapshot>[];
    final sub = controller.snapshots.listen(states.add);

    final bytes = List<int>.generate(2000, (i) => i % 256);
    final digest = sha256.convert(bytes).toString();
    final httpClient = HttpClient();

    final offerRequest = await httpClient.postUrl(Uri.parse('http://127.0.0.1:${server.port}/handoff/offer'));
    offerRequest.headers.set(headerProtocolVersion, '1');
    offerRequest.headers.set(headerAuthToken, 'tok');
    offerRequest.headers.contentType = ContentType.json;
    offerRequest.write(
      jsonEncode({
        'handoff_id': 'h1',
        'display_name': 'x.bin',
        'mime_type': 'application/octet-stream',
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
    await sub.cancel();

    expect(states.any((s) => s.state == AppState.receiving), isTrue);
    expect(controller.current.state, AppState.received);
    expect(controller.current.bytesReceived, bytes.length);
  });

  test('share bridge failure without a real platform never crashes shareCurrentFile', () async {
    final result = await controller.shareCurrentFile('${root.path}/x.bin', 'x.bin', 'application/octet-stream');
    expect(result, ShareResult.platformError);
  });

  test('MissingPluginException from the channel is treated as a bounded platform error', () async {
    final bridge = ShareBridge(channel: const MethodChannel('app.friendsend/share'));
    final result = await bridge.shareFile(path: '/tmp/x', displayName: 'x', mimeType: 'application/octet-stream');
    expect(result, ShareResult.platformError);
  });
}
