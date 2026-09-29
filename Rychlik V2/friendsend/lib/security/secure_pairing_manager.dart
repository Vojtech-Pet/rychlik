import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:cryptography/cryptography.dart';

import 'canonical.dart';
import 'desktop_trust_store.dart';

/// FriendSend's side of the real pairing wire callback (Prompt A15
/// §17/§28-35) -- see src/rychlik/device/security/pairing_bootstrap.py
/// for the desktop side and the full protocol description. Two real
/// HTTP round trips against the desktop's short-lived bootstrap
/// listener:
///
///     POST /pairing/offer   {transcript fields + proof_a}
///     <- {accepted, proof_b}
///     POST /pairing/confirm {pairing_session_id}   (only once proof_b
///                                                    is verified)
///
/// Trust is persisted here ONLY after proof_b verifies -- never before
/// (§34/§35): a connection failure before that point leaves this side
/// with no half-trust either.
class PairingFormatException implements Exception {
  PairingFormatException(this.message);
  final String message;
  @override
  String toString() => 'PairingFormatException: $message';
}

class PairingExpiredException implements Exception {}

class PairingProofMismatchException implements Exception {}

class PairingUnsupportedProtocolException implements Exception {
  PairingUnsupportedProtocolException(this.version);
  final int version;
}

class PairingBootstrapPayload {
  PairingBootstrapPayload({
    required this.protocolVersion,
    required this.securityProfile,
    required this.pairingSessionId,
    required this.desktopInstanceId,
    required this.desktopPublicSigningKey,
    required this.desktopEndpointHost,
    required this.desktopEndpointPort,
    required this.desktopNonce,
    required this.secret,
    required this.expiresAtUtc,
  });

  final int protocolVersion;
  final String securityProfile;
  final String pairingSessionId;
  final String desktopInstanceId;
  final List<int> desktopPublicSigningKey;
  final String desktopEndpointHost;
  final int desktopEndpointPort;
  final List<int> desktopNonce;
  final String secret;
  final DateTime expiresAtUtc;

  bool get isExpired => DateTime.now().toUtc().isAfter(expiresAtUtc);

  factory PairingBootstrapPayload.fromJson(Map<String, dynamic> json) {
    try {
      final endpoint = json['desktop_endpoint'] as Map<String, dynamic>;
      final expires = DateTime.parse(json['expires_at_utc'] as String).toUtc();
      return PairingBootstrapPayload(
        protocolVersion: json['protocol_version'] as int,
        securityProfile: json['security_profile'] as String,
        pairingSessionId: json['pairing_session_id'] as String,
        desktopInstanceId: json['desktop_instance_id'] as String,
        desktopPublicSigningKey: base64.decode(json['desktop_public_signing_key'] as String),
        desktopEndpointHost: endpoint['host'] as String,
        desktopEndpointPort: endpoint['port'] as int,
        desktopNonce: base64.decode(json['desktop_nonce'] as String),
        secret: json['secret'] as String,
        expiresAtUtc: expires,
      );
    } on TypeError catch (e) {
      throw PairingFormatException('malformed pairing payload: $e');
    } on FormatException catch (e) {
      throw PairingFormatException('malformed pairing payload: $e');
    }
  }

  /// Never includes [secret] -- callers display/log this, not
  /// `toJson()`/the raw payload (§20/§169).
  String get redactedDescription =>
      'PairingBootstrapPayload(session=$pairingSessionId, desktop=$desktopInstanceId, secret=<redacted>)';
}

class SecurePairingManager {
  SecurePairingManager({
    required this.deviceId,
    required this.displayName,
    required this.friendSendTlsSpkiSha256,
    required this.friendSendEndpointHost,
    required this.friendSendEndpointPort,
    required this.trustStore,
    this.protocolVersion = 1,
    this.securityProfile = 'pinned-tls-signature-v1',
    Random? random,
  }) : _random = random ?? Random.secure();

  final String deviceId;
  final String displayName;
  final List<int> friendSendTlsSpkiSha256;
  final String friendSendEndpointHost;
  final int friendSendEndpointPort;
  final DesktopTrustStore trustStore;
  final int protocolVersion;
  final String securityProfile;
  final Random _random;

  PairingBootstrapPayload parse(String rawJson) {
    final Map<String, dynamic> json;
    try {
      json = jsonDecode(rawJson) as Map<String, dynamic>;
    } on FormatException {
      throw PairingFormatException('pairing payload is not valid JSON');
    }
    return PairingBootstrapPayload.fromJson(json);
  }

  Future<void> completePairing(PairingBootstrapPayload payload) async {
    if (payload.protocolVersion != protocolVersion) {
      throw PairingUnsupportedProtocolException(payload.protocolVersion);
    }
    if (payload.isExpired) {
      throw PairingExpiredException();
    }

    final deviceNonce = List<int>.generate(32, (_) => _random.nextInt(256));
    final transcript = pairingTranscript(
      securityProfile: payload.securityProfile,
      protocolVersion: payload.protocolVersion,
      pairingSessionId: payload.pairingSessionId,
      desktopInstanceId: payload.desktopInstanceId,
      desktopPublicSigningKey: payload.desktopPublicSigningKey,
      desktopNonce: payload.desktopNonce,
      deviceId: deviceId,
      friendSendDisplayName: displayName,
      friendSendTlsSpkiSha256: friendSendTlsSpkiSha256,
      friendSendEndpointHost: friendSendEndpointHost,
      friendSendEndpointPort: friendSendEndpointPort,
      deviceNonce: deviceNonce,
    );

    final secretBytes = utf8.encode(payload.secret);
    final hmacAlgo = Hmac.sha256();
    final secretKey = SecretKey(secretBytes);
    final proofA = await hmacAlgo.calculateMac(
      [...pairingProofADomain, ...transcript],
      secretKey: secretKey,
    );

    final httpClient = HttpClient();
    httpClient.connectionTimeout = const Duration(seconds: 10);
    try {
      final offerRequest = await httpClient
          .postUrl(Uri.parse('http://${payload.desktopEndpointHost}:${payload.desktopEndpointPort}/pairing/offer'))
          .timeout(const Duration(seconds: 10));
      offerRequest.headers.contentType = ContentType.json;
      // Explicit Content-Length -- without it dart:io's HttpClient may
      // send Transfer-Encoding: chunked, which Python's plain
      // http.server-based bootstrap listener (like the A13/A14 test
      // fixture) never decodes, since it only ever reads by
      // Content-Length (see git history: the exact same class of bug
      // found in Prompt A14's cross-language transport).
      final offerBodyBytes = utf8.encode(
        jsonEncode({
          'pairing_session_id': payload.pairingSessionId,
          'device_id': deviceId,
          'friendsend_display_name': displayName,
          'friendsend_tls_spki_sha256': _toHex(friendSendTlsSpkiSha256),
          'friendsend_endpoint': {'host': friendSendEndpointHost, 'port': friendSendEndpointPort},
          'device_nonce': base64.encode(deviceNonce),
          'protocol_version': payload.protocolVersion,
          'security_profile': payload.securityProfile,
          'proof_a': _toHex(proofA.bytes),
        }),
      );
      offerRequest.contentLength = offerBodyBytes.length;
      offerRequest.add(offerBodyBytes);
      final offerResponse = await offerRequest.close().timeout(const Duration(seconds: 10));
      final offerBody = jsonDecode(await utf8.decoder.bind(offerResponse).join()) as Map<String, dynamic>;
      if (offerResponse.statusCode != 200 || offerBody['accepted'] != true) {
        throw PairingProofMismatchException();
      }

      final proofBExpected = await hmacAlgo.calculateMac(
        [...pairingProofBDomain, ...transcript],
        secretKey: secretKey,
      );
      final proofBReceived = _fromHex(offerBody['proof_b'] as String);
      if (_toHex(proofBExpected.bytes) != _toHex(proofBReceived)) {
        throw PairingProofMismatchException();
      }

      // Only now, having verified the desktop's reciprocal proof, is it
      // safe to persist trust (§34/§35).
      await trustStore.upsert(
        TrustedDesktop(
          desktopInstanceId: payload.desktopInstanceId,
          desktopPublicSigningKey: payload.desktopPublicSigningKey,
          displayName: null,
          pairedAtUtc: DateTime.now().toUtc().toIso8601String(),
        ),
      );

      // A fresh HttpClient for the confirm call, rather than reusing the
      // offer's keep-alive connection, keeps this bootstrap exchange's
      // two requests independent and simple to reason about.
      final confirmClient = HttpClient();
      confirmClient.connectionTimeout = const Duration(seconds: 10);
      try {
        final confirmRequest = await confirmClient
            .postUrl(Uri.parse('http://${payload.desktopEndpointHost}:${payload.desktopEndpointPort}/pairing/confirm'))
            .timeout(const Duration(seconds: 10));
        confirmRequest.headers.contentType = ContentType.json;
        final confirmBodyBytes = utf8.encode(jsonEncode({'pairing_session_id': payload.pairingSessionId}));
        confirmRequest.contentLength = confirmBodyBytes.length;
        confirmRequest.add(confirmBodyBytes);
        await confirmRequest.close().timeout(const Duration(seconds: 10));
      } finally {
        // force: true -- a plain close() leaves an idle keep-alive
        // connection open long enough to keep the whole process (and,
        // for the one-shot pairing_client_harness, the host test
        // runner waiting on it) alive well past a normal exit.
        confirmClient.close(force: true);
      }
    } finally {
      httpClient.close(force: true);
    }
  }

  static String _toHex(List<int> bytes) => bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

  static List<int> _fromHex(String s) {
    final out = List<int>.filled(s.length ~/ 2, 0);
    for (var i = 0; i < out.length; i++) {
      out[i] = int.parse(s.substring(i * 2, i * 2 + 2), radix: 16);
    }
    return out;
  }
}
