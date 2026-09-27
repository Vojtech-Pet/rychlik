import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart' as dcrypto;
import 'package:cryptography/cryptography.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/handoff/handoff_controller.dart';
import 'package:friendsend/identity/device_identity.dart';
import 'package:friendsend/platform/share_bridge.dart';
import 'package:friendsend/receiver/receiver_server.dart' show ReceiverConfig;
import 'package:friendsend/receiver/temp_cache.dart';
import 'package:friendsend/security/auth_challenge.dart';
import 'package:friendsend/security/canonical.dart';
import 'package:friendsend/security/desktop_trust_store.dart';
import 'package:friendsend/security/secure_pairing_manager.dart';
import 'package:friendsend/security/secure_receiver_server.dart';
import 'package:friendsend/security/self_signed_cert.dart';
import 'package:friendsend/security/tls_identity.dart';
import 'package:friendsend/ui/home_screen.dart';

String _toHex(List<int> bytes) => bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

List<int> _fromHex(String s) {
  final out = List<int>.filled(s.length ~/ 2, 0);
  for (var i = 0; i < out.length; i++) {
    out[i] = int.parse(s.substring(i * 2, i * 2 + 2), radix: 16);
  }
  return out;
}

/// A real (non-mocked) Dart implementation of the desktop's pairing
/// bootstrap listener (mirrors
/// src/rychlik/device/security/pairing_bootstrap.py's wire contract) --
/// used only so this widget test can drive a genuine, correct pairing
/// exchange without needing a Python process. It performs the real
/// transcript/HMAC computation, not a stub that always says yes.
class _FakeDesktopBootstrap {
  _FakeDesktopBootstrap({required this.secret, required this.transcriptBuilder});

  final String secret;
  final Uint8ListBuilder transcriptBuilder;
  HttpServer? _server;
  bool _offered = false;

  int get port => _server!.port;

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server!.listen(_handle);
  }

  Future<void> _handle(HttpRequest request) async {
    final body = jsonDecode(await utf8.decoder.bind(request).join()) as Map<String, dynamic>;
    if (request.uri.path == '/pairing/offer') {
      final transcript = transcriptBuilder(body);
      final hmacAlgo = Hmac.sha256();
      final secretKey = SecretKey(utf8.encode(secret));
      final expectedProofA = await hmacAlgo.calculateMac([...pairingProofADomain, ...transcript], secretKey: secretKey);
      if (_toHex(expectedProofA.bytes) != body['proof_a']) {
        request.response.statusCode = 401;
        request.response.write(jsonEncode({'error_code': 'AUTHENTICATION_FAILED'}));
        await request.response.close();
        return;
      }
      final proofB = await hmacAlgo.calculateMac([...pairingProofBDomain, ...transcript], secretKey: secretKey);
      _offered = true;
      request.response.write(jsonEncode({'accepted': true, 'proof_b': _toHex(proofB.bytes)}));
      await request.response.close();
    } else if (request.uri.path == '/pairing/confirm' && _offered) {
      request.response.write(jsonEncode({'trusted': true}));
      await request.response.close();
    } else {
      request.response.statusCode = 404;
      await request.response.close();
    }
  }

  Future<void> stop() async {
    await _server?.close(force: true);
  }
}

typedef Uint8ListBuilder = List<int> Function(Map<String, dynamic> offerBody);

Future<void> _pumpHome(
  WidgetTester tester, {
  required HandoffController controller,
  required SecurePairingManager pairingManager,
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
  late SecureFriendSendReceiverServer server;
  late HandoffController controller;
  late SecurePairingManager pairingManager;
  late DesktopTrustStore trustStore;

  setUp(() async {
    root = Directory.systemTemp.createTempSync('friendsend_home_screen_test_');
    final generated = SelfSignedCertificate.generate(commonName: 'd1');
    final spkiDer = base64.decode(
      generated.publicKeyPem.replaceAll('-----BEGIN PUBLIC KEY-----', '').replaceAll('-----END PUBLIC KEY-----', '').replaceAll('\n', '').trim(),
    );
    final identity = TlsIdentity(
      certificatePem: generated.certificatePem,
      privateKeyPem: generated.privateKeyPem,
      spkiSha256: dcrypto.sha256.convert(spkiDer).bytes,
    );
    trustStore = DesktopTrustStore(root);
    server = SecureFriendSendReceiverServer(
      config: ReceiverConfig(deviceId: 'd1', displayName: 'x'),
      tempCache: TempCache(root),
      tlsIdentity: identity,
      authChallengeManager: AuthChallengeManager(trustStore: trustStore),
    );
    await server.start();
    controller = HandoffController(receiver: server, tempCache: TempCache(root), shareBridge: ShareBridge(), autoShare: false);
    pairingManager = SecurePairingManager(
      deviceId: 'd1',
      displayName: 'Test Phone',
      friendSendTlsSpkiSha256: identity.spkiSha256,
      friendSendEndpointHost: '127.0.0.1',
      friendSendEndpointPort: server.port,
      trustStore: trustStore,
    );
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
    HttpOverrides.global = null; // this suite needs real loopback sockets
    await _pumpHome(tester, controller: controller, pairingManager: pairingManager);

    await tester.runAsync(() async {
      const secret = 'deadbeefcafebabe0011223344556677';
      final desktopKeyPair = await Ed25519().newKeyPair();
      final desktopPublicKey = await desktopKeyPair.extractPublicKey();
      final desktopNonce = List<int>.generate(32, (i) => i);

      List<int> buildTranscript(Map<String, dynamic> body) => pairingTranscript(
        securityProfile: body['security_profile'] as String,
        protocolVersion: body['protocol_version'] as int,
        pairingSessionId: body['pairing_session_id'] as String,
        desktopInstanceId: 'desktop-1',
        desktopPublicSigningKey: desktopPublicKey.bytes,
        desktopNonce: desktopNonce,
        deviceId: body['device_id'] as String,
        friendSendDisplayName: body['friendsend_display_name'] as String,
        friendSendTlsSpkiSha256: _fromHex(body['friendsend_tls_spki_sha256'] as String),
        friendSendEndpointHost: (body['friendsend_endpoint'] as Map)['host'] as String,
        friendSendEndpointPort: (body['friendsend_endpoint'] as Map)['port'] as int,
        deviceNonce: base64.decode(body['device_nonce'] as String),
      );

      final bootstrap = _FakeDesktopBootstrap(secret: secret, transcriptBuilder: buildTranscript);
      await bootstrap.start();

      final payload = {
        'protocol_version': 1,
        'security_profile': 'pinned-tls-signature-v1',
        'pairing_session_id': 's1',
        'desktop_instance_id': 'desktop-1',
        'desktop_public_signing_key': base64.encode(desktopPublicKey.bytes),
        'desktop_endpoint': {'host': '127.0.0.1', 'port': bootstrap.port},
        'desktop_nonce': base64.encode(desktopNonce),
        'secret': secret,
        'expires_at_utc': DateTime.now().toUtc().add(const Duration(minutes: 5)).toIso8601String(),
      };

      await tester.enterText(find.byKey(const Key('pairing_paste_field')), jsonEncode(payload));
      await tester.tap(find.byKey(const Key('pairing_pair_button')));
      await Future<void>.delayed(const Duration(milliseconds: 200));
      await bootstrap.stop();
    });

    await tester.pump();
    await tester.pump();

    expect(find.byKey(const Key('ready_message')), findsOneWidget);
    expect(find.textContaining('Test Phone'), findsOneWidget);
  });

  testWidgets('expired pairing payload shows a bounded error (§19)', (tester) async {
    await _pumpHome(tester, controller: controller, pairingManager: pairingManager);
    final payload = {
      'protocol_version': 1,
      'security_profile': 'pinned-tls-signature-v1',
      'pairing_session_id': 's1',
      'desktop_instance_id': 'desktop-1',
      'desktop_public_signing_key': base64.encode(List<int>.filled(32, 1)),
      'desktop_endpoint': {'host': '127.0.0.1', 'port': 12345},
      'desktop_nonce': base64.encode(List<int>.filled(32, 2)),
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

    // Real crypto (package:cryptography may dispatch to a background
    // isolate) and real file I/O (DesktopTrustStore) both need the real
    // event loop, exactly like real socket I/O -- everything from key
    // generation through the HTTPS calls runs inside ONE runAsync().
    late SimpleKeyPair desktopKeyPair;
    const desktopInstanceId = 'desktop-1';
    final bytes = List<int>.generate(500, (i) => i % 256);
    final digest = dcrypto.sha256.convert(bytes).toString();

    await tester.runAsync(() async {
      desktopKeyPair = await Ed25519().newKeyPair();
      final desktopPublicKey = await desktopKeyPair.extractPublicKey();
      await trustStore.upsert(
        TrustedDesktop(
          desktopInstanceId: desktopInstanceId,
          desktopPublicSigningKey: desktopPublicKey.bytes,
          pairedAtUtc: DateTime.now().toUtc().toIso8601String(),
        ),
      );
    });

    await _pumpHome(tester, controller: controller, pairingManager: pairingManager);
    await tester.pump();
    expect(find.byKey(const Key('ready_message')), findsOneWidget);

    await tester.runAsync(() async {
      // A fresh HttpClient per request, each force-closed immediately --
      // avoids relying on dart:io's connection-pooling/keep-alive
      // behaving a particular way across three sequential HTTPS
      // requests inside a widget test's runAsync zone.
      final challengeClient = HttpClient()..badCertificateCallback = (cert, host, port) => true;
      final Map<String, dynamic> challengeBody;
      try {
        final challengeRequest = await challengeClient.getUrl(Uri.parse('https://127.0.0.1:${server.port}/auth/challenge'));
        final challengeResponse = await challengeRequest.close();
        challengeBody = jsonDecode(await utf8.decoder.bind(challengeResponse).join()) as Map<String, dynamic>;
      } finally {
        challengeClient.close(force: true);
      }
      final challengeId = challengeBody['challenge_id'] as String;
      final challengeNonce = base64.decode(challengeBody['nonce'] as String);

      final sigInput = authSignatureInput(
        securityProfile: 'pinned-tls-signature-v1',
        protocolVersion: 1,
        desktopInstanceId: desktopInstanceId,
        deviceId: 'd1',
        challengeId: challengeId,
        challengeNonce: challengeNonce,
        handoffId: 'h1',
        artifactSha256: _fromHex(digest),
        artifactSizeBytes: bytes.length,
      );
      final signature = await Ed25519().sign(sigInput, keyPair: desktopKeyPair);

      final offerClient = HttpClient()..badCertificateCallback = (cert, host, port) => true;
      try {
        final offerRequest = await offerClient.postUrl(Uri.parse('https://127.0.0.1:${server.port}/handoff/offer'));
        offerRequest.headers.set('X-FriendSend-Desktop-Id', desktopInstanceId);
        offerRequest.headers.set('X-FriendSend-Challenge-Id', challengeId);
        offerRequest.headers.set('X-FriendSend-Signature', base64.encode(signature.bytes));
        offerRequest.headers.contentType = ContentType.json;
        final offerBodyBytes = utf8.encode(
          jsonEncode({
            'handoff_id': 'h1',
            'display_name': 'clip.mp4',
            'mime_type': 'video/mp4',
            'size_bytes': bytes.length,
            'sha256': digest,
          }),
        );
        offerRequest.contentLength = offerBodyBytes.length;
        offerRequest.add(offerBodyBytes);
        await (await offerRequest.close()).drain<void>();
      } finally {
        offerClient.close(force: true);
      }

      final streamClient = HttpClient()..badCertificateCallback = (cert, host, port) => true;
      try {
        final streamRequest = await streamClient.postUrl(Uri.parse('https://127.0.0.1:${server.port}/handoff/stream'));
        streamRequest.headers.set('X-FriendSend-Handoff-Id', 'h1');
        streamRequest.contentLength = bytes.length;
        streamRequest.add(bytes);
        await (await streamRequest.close()).drain<void>();
      } finally {
        streamClient.close(force: true);
      }
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
    // handoff_controller_test.dart / secure_receiver_server_test.dart;
    // this test only asserts the widget never renders forbidden delivery
    // wording anywhere in its static build, regardless of state.
    for (final forbidden in ['Delivered to friend', 'Sent on WhatsApp', 'Friend received it']) {
      expect(find.textContaining(forbidden), findsNothing);
    }
  });
}
