import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart' as dcrypto;
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/platform/lan_address.dart';
import 'package:friendsend/receiver/receiver_server.dart' show ReceiverConfig;
import 'package:friendsend/receiver/temp_cache.dart';
import 'package:friendsend/security/auth_challenge.dart';
import 'package:friendsend/security/canonical.dart';
import 'package:friendsend/security/desktop_trust_store.dart';
import 'package:friendsend/security/secure_receiver_server.dart';
import 'package:friendsend/security/self_signed_cert.dart';
import 'package:friendsend/security/tls_identity.dart';

String _toHex(List<int> bytes) => bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

class _SecureClient {
  _SecureClient(this.port) {
    _client.badCertificateCallback = (cert, host, p) => true; // pin verification is a separate, explicit test
  }

  final int port;
  final HttpClient _client = HttpClient();

  Future<Map<String, dynamic>> challenge() async {
    final req = await _client.getUrl(Uri.parse('https://127.0.0.1:$port/auth/challenge'));
    final resp = await req.close();
    return jsonDecode(await utf8.decoder.bind(resp).join()) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> hello() async {
    final req = await _client.getUrl(Uri.parse('https://127.0.0.1:$port/hello'));
    final resp = await req.close();
    return jsonDecode(await utf8.decoder.bind(resp).join()) as Map<String, dynamic>;
  }

  Future<(int, Map<String, dynamic>)> offer(
    Map<String, dynamic> body, {
    required String desktopId,
    required String challengeId,
    required List<int> signature,
  }) async {
    final req = await _client.postUrl(Uri.parse('https://127.0.0.1:$port/handoff/offer'));
    req.headers.set('X-FriendSend-Desktop-Id', desktopId);
    req.headers.set('X-FriendSend-Challenge-Id', challengeId);
    req.headers.set('X-FriendSend-Signature', base64.encode(signature));
    req.headers.contentType = ContentType.json;
    req.write(jsonEncode(body));
    final resp = await req.close();
    final raw = await utf8.decoder.bind(resp).join();
    return (resp.statusCode, raw.isEmpty ? <String, dynamic>{} : jsonDecode(raw) as Map<String, dynamic>);
  }

  Future<(int, Map<String, dynamic>)> stream(String handoffId, List<int> bytes) async {
    final req = await _client.postUrl(Uri.parse('https://127.0.0.1:$port/handoff/stream'));
    req.headers.set('X-FriendSend-Handoff-Id', handoffId);
    req.contentLength = bytes.length;
    req.add(bytes);
    final resp = await req.close();
    final raw = await utf8.decoder.bind(resp).join();
    return (resp.statusCode, raw.isEmpty ? <String, dynamic>{} : jsonDecode(raw) as Map<String, dynamic>);
  }

  void close() => _client.close(force: true);
}

void main() {
  late Directory root;
  late DesktopTrustStore trustStore;
  late AuthChallengeManager challengeManager;
  late SecureFriendSendReceiverServer server;
  late _SecureClient client;
  late KeyPair desktopKeyPair;
  late String desktopInstanceId;
  final algorithm = Ed25519();

  setUp(() async {
    root = Directory.systemTemp.createTempSync('friendsend_secure_receiver_test_');
    final generated = SelfSignedCertificate.generate(commonName: 'test-device');
    final identity = TlsIdentity(
      certificatePem: generated.certificatePem,
      privateKeyPem: generated.privateKeyPem,
      spkiSha256: dcrypto.sha256
          .convert(
            base64.decode(
              generated.publicKeyPem
                  .replaceAll('-----BEGIN PUBLIC KEY-----', '')
                  .replaceAll('-----END PUBLIC KEY-----', '')
                  .replaceAll('\n', '')
                  .trim(),
            ),
          )
          .bytes,
    );

    trustStore = DesktopTrustStore(root);
    desktopKeyPair = await algorithm.newKeyPair();
    final desktopPublicKey = await desktopKeyPair.extractPublicKey() as SimplePublicKey;
    desktopInstanceId = 'desktop-under-test';
    await trustStore.upsert(
      TrustedDesktop(
        desktopInstanceId: desktopInstanceId,
        desktopPublicSigningKey: desktopPublicKey.bytes,
        pairedAtUtc: DateTime.now().toUtc().toIso8601String(),
      ),
    );

    challengeManager = AuthChallengeManager(trustStore: trustStore, challengeTtl: const Duration(seconds: 30));
    server = SecureFriendSendReceiverServer(
      config: ReceiverConfig(deviceId: 'd1', displayName: 'Secure Test Device'),
      tempCache: TempCache(root),
      tlsIdentity: identity,
      authChallengeManager: challengeManager,
    );
    await server.start();
    client = _SecureClient(server.port);
  });

  tearDown(() async {
    client.close();
    await server.stop();
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  Future<List<int>> signRequest({
    required String challengeId,
    required List<int> challengeNonce,
    required String handoffId,
    required List<int> artifactSha256,
    required int artifactSizeBytes,
    KeyPair? keyPair,
  }) async {
    final input = authSignatureInput(
      securityProfile: 'pinned-tls-signature-v1',
      protocolVersion: 1,
      desktopInstanceId: desktopInstanceId,
      deviceId: 'd1',
      challengeId: challengeId,
      challengeNonce: challengeNonce,
      handoffId: handoffId,
      artifactSha256: artifactSha256,
      artifactSizeBytes: artifactSizeBytes,
    );
    final signature = await algorithm.sign(input, keyPair: keyPair ?? desktopKeyPair);
    return signature.bytes;
  }

  test('valid signature + fresh challenge lets a real HTTPS transfer complete', () async {
    final bytes = List<int>.generate(50000, (i) => i % 256);
    final sha256Bytes = dcrypto.sha256.convert(bytes).bytes;
    final sha256Hex = _toHex(sha256Bytes);
    const handoffId = 'h1';

    final challenge = await client.challenge();
    final signature = await signRequest(
      challengeId: challenge['challenge_id'] as String,
      challengeNonce: base64.decode(challenge['nonce'] as String),
      handoffId: handoffId,
      artifactSha256: sha256Bytes,
      artifactSizeBytes: bytes.length,
    );

    final (offerStatus, offerBody) = await client.offer(
      {
        'handoff_id': handoffId,
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': sha256Hex,
      },
      desktopId: desktopInstanceId,
      challengeId: challenge['challenge_id'] as String,
      signature: signature,
    );
    expect(offerStatus, 200);
    expect(offerBody['accepted'], isTrue);

    final (streamStatus, streamBody) = await client.stream(handoffId, bytes);
    expect(streamStatus, 200);
    expect(streamBody['state'], 'RECEIVED');
    expect(streamBody['sha256'], sha256Hex);
  });

  test('untrusted desktop is rejected before any payload byte', () async {
    final bytes = [1, 2, 3];
    final sha256Bytes = dcrypto.sha256.convert(bytes).bytes;
    final challenge = await client.challenge();
    final otherKeyPair = await algorithm.newKeyPair();
    final signature = await signRequest(
      challengeId: challenge['challenge_id'] as String,
      challengeNonce: base64.decode(challenge['nonce'] as String),
      handoffId: 'h1',
      artifactSha256: sha256Bytes,
      artifactSizeBytes: bytes.length,
      keyPair: otherKeyPair,
    );
    final (status, body) = await client.offer(
      {
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': _toHex(sha256Bytes),
      },
      desktopId: 'never-paired-desktop',
      challengeId: challenge['challenge_id'] as String,
      signature: signature,
    );
    expect(status, 401);
    expect(body['error_code'], 'UNTRUSTED_DESKTOP');
  });

  test('wrong signing key for a known desktop_instance_id fails authentication, zero bytes', () async {
    final bytes = [1, 2, 3];
    final sha256Bytes = dcrypto.sha256.convert(bytes).bytes;
    final challenge = await client.challenge();
    final wrongKeyPair = await algorithm.newKeyPair();
    final signature = await signRequest(
      challengeId: challenge['challenge_id'] as String,
      challengeNonce: base64.decode(challenge['nonce'] as String),
      handoffId: 'h1',
      artifactSha256: sha256Bytes,
      artifactSizeBytes: bytes.length,
      keyPair: wrongKeyPair, // signed by the WRONG key for this desktop_instance_id
    );
    final (status, body) = await client.offer(
      {
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': _toHex(sha256Bytes),
      },
      desktopId: desktopInstanceId,
      challengeId: challenge['challenge_id'] as String,
      signature: signature,
    );
    expect(status, 401);
    expect(body['error_code'], 'AUTHENTICATION_FAILED');
  });

  test('replayed challenge is rejected (one-time use)', () async {
    final bytes = [1, 2, 3];
    final sha256Bytes = dcrypto.sha256.convert(bytes).bytes;
    final challenge = await client.challenge();
    final signature = await signRequest(
      challengeId: challenge['challenge_id'] as String,
      challengeNonce: base64.decode(challenge['nonce'] as String),
      handoffId: 'h1',
      artifactSha256: sha256Bytes,
      artifactSizeBytes: bytes.length,
    );
    final offerBody = {
      'handoff_id': 'h1',
      'display_name': 'f.bin',
      'mime_type': 'application/octet-stream',
      'size_bytes': bytes.length,
      'sha256': _toHex(sha256Bytes),
    };
    final (status1, _) = await client.offer(
      offerBody,
      desktopId: desktopInstanceId,
      challengeId: challenge['challenge_id'] as String,
      signature: signature,
    );
    expect(status1, 200);

    // Replay the exact same challenge_id + signature for a second offer.
    final (status2, body2) = await client.offer(
      {...offerBody, 'handoff_id': 'h2'},
      desktopId: desktopInstanceId,
      challengeId: challenge['challenge_id'] as String,
      signature: signature,
    );
    expect(status2, 400);
    expect(body2['error_code'], 'AUTH_REPLAY');
  });

  test('a signature valid for one handoff_id does not authorize a different one', () async {
    final bytes = [1, 2, 3];
    final sha256Bytes = dcrypto.sha256.convert(bytes).bytes;
    final challenge = await client.challenge();
    final signature = await signRequest(
      challengeId: challenge['challenge_id'] as String,
      challengeNonce: base64.decode(challenge['nonce'] as String),
      handoffId: 'handoff-A',
      artifactSha256: sha256Bytes,
      artifactSizeBytes: bytes.length,
    );
    final (status, body) = await client.offer(
      {
        'handoff_id': 'handoff-B', // different handoff than what was signed
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': _toHex(sha256Bytes),
      },
      desktopId: desktopInstanceId,
      challengeId: challenge['challenge_id'] as String,
      signature: signature,
    );
    expect(status, 401);
    expect(body['error_code'], 'AUTHENTICATION_FAILED');
  });

  test('expired challenge is rejected using an injected clock, no real waiting', () async {
    var now = DateTime.now().toUtc();
    final shortLivedManager = AuthChallengeManager(
      trustStore: trustStore,
      challengeTtl: const Duration(seconds: 5),
      clock: () => now,
    );
    final generated = SelfSignedCertificate.generate(commonName: 'expiry-test');
    final identity = TlsIdentity(
      certificatePem: generated.certificatePem,
      privateKeyPem: generated.privateKeyPem,
      spkiSha256: const [],
    );
    final expiryServer = SecureFriendSendReceiverServer(
      config: ReceiverConfig(deviceId: 'd2', displayName: 'x'),
      tempCache: TempCache(root),
      tlsIdentity: identity,
      authChallengeManager: shortLivedManager,
    );
    await expiryServer.start();
    final expiryClient = _SecureClient(expiryServer.port);
    try {
      final challenge = await expiryClient.challenge();
      now = now.add(const Duration(seconds: 10)); // past the 5s TTL, no real sleep
      final bytes = [1, 2, 3];
      final sha256Bytes = dcrypto.sha256.convert(bytes).bytes;
      final signature = await signRequest(
        challengeId: challenge['challenge_id'] as String,
        challengeNonce: base64.decode(challenge['nonce'] as String),
        handoffId: 'h1',
        artifactSha256: sha256Bytes,
        artifactSizeBytes: bytes.length,
      );
      final (status, body) = await expiryClient.offer(
        {
          'handoff_id': 'h1',
          'display_name': 'f.bin',
          'mime_type': 'application/octet-stream',
          'size_bytes': bytes.length,
          'sha256': _toHex(sha256Bytes),
        },
        desktopId: desktopInstanceId,
        challengeId: challenge['challenge_id'] as String,
        signature: signature,
      );
      expect(status, 400);
      expect(body['error_code'], 'CHALLENGE_EXPIRED');
    } finally {
      expiryClient.close();
      await expiryServer.stop();
    }
  });

  test('hello advertises the additive media capability profiles (Prompt A16 §98)', () async {
    final hello = await client.hello();
    final profiles = (hello['media_profiles'] as List).cast<String>();
    expect(profiles, contains('friendsend-generic-video-v1'));
    expect(profiles, contains('friendsend-generic-audio-v1'));
    // §99-101: never a per-social-app capability.
    for (final forbidden in ['whatsapp', 'messenger', 'telegram']) {
      expect(profiles.any((p) => p.toLowerCase().contains(forbidden)), isFalse);
    }
  });

  test('a receiver started with the production bind address accepts TCP on a non-loopback interface (physical-phone bug)', () async {
    final interfaces = await NetworkInterface.list(type: InternetAddressType.IPv4, includeLoopback: false, includeLinkLocal: false);
    final lan = [for (final i in interfaces) ...i.addresses].where((a) => !a.isLoopback).toList();
    if (lan.isEmpty) {
      markTestSkipped('no non-loopback IPv4 interface on this machine');
      return;
    }
    final generated = SelfSignedCertificate.generate(commonName: 'bind-test');
    final identity = TlsIdentity(certificatePem: generated.certificatePem, privateKeyPem: generated.privateKeyPem, spkiSha256: const []);
    final lanServer = SecureFriendSendReceiverServer(
      config: ReceiverConfig(deviceId: 'd3', displayName: 'x'),
      tempCache: TempCache(root),
      tlsIdentity: identity,
      authChallengeManager: challengeManager,
    );
    await lanServer.start(address: receiverBindAddress);
    try {
      final socket = await Socket.connect(lan.first, lanServer.port, timeout: const Duration(seconds: 3));
      await socket.close();
    } finally {
      await lanServer.stop();
    }
    // the default (loopback-only) server is NOT reachable that way -- this is what shipped before the fix
    await server.stop();
    await server.start();
    await expectLater(Socket.connect(lan.first, server.port, timeout: const Duration(seconds: 2)), throwsA(isA<SocketException>()));
  });
}
