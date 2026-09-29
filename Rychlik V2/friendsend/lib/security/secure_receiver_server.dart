import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:convert/convert.dart' show AccumulatorSink;
import 'package:crypto/crypto.dart';

import '../protocol/models.dart';
import '../protocol/protocol.dart';
import '../receiver/receiver_interface.dart';
import '../receiver/receiver_server.dart' show ReceiverConfig, ReceiverEvent, ReceiverEventKind;
import '../receiver/temp_cache.dart';
import 'auth_challenge.dart';
import 'tls_identity.dart';

/// The production FriendSend receiver after Prompt A15: real HTTPS
/// (`HttpServer.bindSecure`, the self-signed [TlsIdentity]), with every
/// `/handoff/offer` additionally requiring a valid, fresh, one-time
/// Ed25519 signature from a previously-paired desktop
/// ([AuthChallengeManager]) before any preflight/capability check even
/// runs -- never mind before any payload byte. `/handoff/stream` reuses
/// the exact bounded, incrementally-hashed streaming/integrity model
/// from the A14 [FriendSendReceiverServer] (§52 of the A15 prompt: this
/// phase does not replace that guarantee, only adds a transport/auth
/// layer around it).
class SecureFriendSendReceiverServer implements FriendSendReceiverLike {
  SecureFriendSendReceiverServer({
    required this.config,
    required this.tempCache,
    required this.tlsIdentity,
    required this.authChallengeManager,
  });

  final ReceiverConfig config;
  final TempCache tempCache;
  final TlsIdentity tlsIdentity;
  final AuthChallengeManager authChallengeManager;

  HttpServer? _server;
  final Map<String, HandoffOffer> _pendingOffers = {};
  final Set<String> _authenticatedHandoffs = {};
  final Set<String> _cancelRequested = {};
  final StreamController<ReceiverEvent> _events = StreamController.broadcast();

  @override
  Stream<ReceiverEvent> get events => _events.stream;

  int get port => _server!.port;
  String get host => _server!.address.address;

  Future<void> start({InternetAddress? address}) async {
    final context = tlsIdentity.buildSecurityContext();
    _server = await HttpServer.bindSecure(address ?? InternetAddress.loopbackIPv4, 0, context);
    unawaited(_serve());
  }

  Future<void> stop() async {
    await _server?.close(force: true);
    _server = null;
  }

  @override
  void requestCancel(String handoffId) => _cancelRequested.add(handoffId);

  Future<void> _serve() async {
    final server = _server;
    if (server == null) return;
    await for (final request in server) {
      unawaited(_dispatch(request));
    }
  }

  Future<void> _dispatch(HttpRequest request) async {
    try {
      final path = request.uri.path;
      if (request.method == 'GET' && path == '/hello') {
        await _handleHello(request);
      } else if (request.method == 'GET' && path == '/auth/challenge') {
        await _handleChallenge(request);
      } else if (request.method == 'POST' && path == '/handoff/offer') {
        await _handleOffer(request);
      } else if (request.method == 'POST' && path == '/handoff/stream') {
        await _handleStream(request);
      } else {
        request.response.statusCode = HttpStatus.notFound;
        await request.response.close();
      }
    } catch (_) {
      try {
        request.response.statusCode = HttpStatus.internalServerError;
        await request.response.close();
      } catch (_) {}
    }
  }

  Future<void> _handleHello(HttpRequest request) async {
    final body = jsonEncode({
      'protocol_version': config.protocolVersion,
      'capabilities': config.capabilities.map((c) => c.wireName).toList(),
      'platform': config.platform,
      'device_id': config.deviceId,
      'display_name': config.displayName,
      'security_profile': 'pinned-tls-signature-v1',
      'tls_spki_sha256': tlsIdentity.spkiSha256Hex,
      'media_profiles': config.mediaProfiles.toList(),
    });
    await _sendJson(request, HttpStatus.ok, body);
  }

  Future<void> _handleChallenge(HttpRequest request) async {
    final challenge = authChallengeManager.issueChallenge();
    final body = jsonEncode({
      'challenge_id': challenge.challengeId,
      'nonce': base64.encode(challenge.nonce),
      'expires_at_utc': challenge.expiresAt.toIso8601String(),
    });
    await _sendJson(request, HttpStatus.ok, body);
  }

  Future<void> _handleOffer(HttpRequest request) async {
    final raw = await utf8.decoder.bind(request).join();
    Map<String, dynamic> json;
    try {
      json = raw.isEmpty ? {} : jsonDecode(raw) as Map<String, dynamic>;
    } on FormatException {
      await _reject(request, HttpStatus.badRequest, HandoffErrorCode.receiverRejected);
      return;
    }

    HandoffOffer offer;
    try {
      offer = HandoffOffer.fromJson(json);
    } on ProtocolFormatException {
      await _reject(request, HttpStatus.badRequest, HandoffErrorCode.receiverRejected);
      return;
    }

    final desktopId = request.headers.value('X-FriendSend-Desktop-Id');
    final challengeId = request.headers.value('X-FriendSend-Challenge-Id');
    final signatureB64 = request.headers.value('X-FriendSend-Signature');
    if (desktopId == null || challengeId == null || signatureB64 == null) {
      await _reject(request, HttpStatus.unauthorized, HandoffErrorCode.authenticationFailed);
      return;
    }

    final result = await authChallengeManager.verify(
      desktopInstanceId: desktopId,
      deviceId: config.deviceId,
      challengeId: challengeId,
      handoffId: offer.handoffId,
      artifactSha256: _hexToBytes(offer.sha256),
      artifactSizeBytes: offer.sizeBytes,
      signature: base64.decode(signatureB64),
    );

    switch (result) {
      case AuthResult.untrustedDesktop:
        await _reject(request, HttpStatus.unauthorized, HandoffErrorCode.untrustedDesktop);
        return;
      case AuthResult.authenticationFailed:
        await _reject(request, HttpStatus.unauthorized, HandoffErrorCode.authenticationFailed);
        return;
      case AuthResult.challengeExpired:
        await _reject(request, HttpStatus.badRequest, HandoffErrorCode.challengeExpired);
        return;
      case AuthResult.challengeReplayed:
        await _reject(request, HttpStatus.badRequest, HandoffErrorCode.authReplay);
        return;
      case AuthResult.ok:
        break;
    }

    final rejection = _preflightReject(offer);
    if (rejection != null) {
      await _reject(request, rejection.$1, rejection.$2);
      return;
    }

    _pendingOffers[offer.handoffId] = offer;
    _authenticatedHandoffs.add(offer.handoffId);
    await _sendJson(request, HttpStatus.ok, jsonEncode({'accepted': true}));
  }

  (int, HandoffErrorCode)? _preflightReject(HandoffOffer offer) {
    final maxBytes = config.maxPayloadBytes;
    if (maxBytes != null && offer.sizeBytes > maxBytes) {
      return (HttpStatus.requestEntityTooLarge, HandoffErrorCode.payloadTooLarge);
    }
    final supportedMime = config.supportedMimeTypes;
    if (supportedMime != null && !supportedMime.contains(offer.mimeType)) {
      return (HttpStatus.unsupportedMediaType, HandoffErrorCode.unsupportedMedia);
    }
    return null;
  }

  Future<void> _handleStream(HttpRequest request) async {
    final handoffId = request.headers.value('X-FriendSend-Handoff-Id');
    final declaredLength = request.contentLength >= 0 ? request.contentLength : 0;

    final offer = handoffId == null ? null : _pendingOffers.remove(handoffId);
    final authenticated = handoffId != null && _authenticatedHandoffs.remove(handoffId);

    if (offer == null || !authenticated) {
      await _drain(request);
      await _sendJson(
        request,
        HttpStatus.unauthorized,
        jsonEncode({'state': 'FAILED', 'error_code': HandoffErrorCode.authenticationFailed.wireName}),
      );
      return;
    }
    if (offer.sizeBytes != declaredLength) {
      await _drain(request);
      await _sendJson(
        request,
        HttpStatus.badRequest,
        jsonEncode({'state': 'FAILED', 'error_code': HandoffErrorCode.receiverRejected.wireName}),
      );
      return;
    }

    final file = await tempCache.allocate(handoffId);
    final sink = file.openWrite();
    final hashOutput = AccumulatorSink<Digest>();
    final hashInput = sha256.startChunkedConversion(hashOutput);
    var received = 0;
    var cancelled = false;
    var connectionBroke = false;

    try {
      await for (final chunk in request) {
        if (_cancelRequested.remove(handoffId)) {
          cancelled = true;
          break;
        }
        sink.add(chunk);
        hashInput.add(chunk);
        received += chunk.length;
        _events.add(
          ReceiverEvent(
            handoffId: handoffId,
            kind: ReceiverEventKind.progress,
            bytesReceived: received,
            totalBytes: offer.sizeBytes,
            mimeType: offer.mimeType,
            preferredFilename: offer.preferredFilename ?? offer.displayName,
          ),
        );
      }
    } on Object {
      connectionBroke = true;
    } finally {
      await sink.close();
    }
    hashInput.close();

    if (connectionBroke && !cancelled) {
      await tempCache.discard(handoffId, file);
      _events.add(
        ReceiverEvent(
          handoffId: handoffId,
          kind: ReceiverEventKind.failed,
          bytesReceived: received,
          totalBytes: offer.sizeBytes,
          errorCode: HandoffErrorCode.incompleteTransfer,
        ),
      );
      return;
    }

    if (cancelled) {
      await tempCache.discard(handoffId, file);
      _events.add(
        ReceiverEvent(handoffId: handoffId, kind: ReceiverEventKind.cancelled, bytesReceived: received, totalBytes: offer.sizeBytes),
      );
      try {
        final socket = await request.response.detachSocket(writeHeaders: false);
        socket.destroy();
      } catch (_) {}
      return;
    }

    if (received != offer.sizeBytes) {
      await tempCache.discard(handoffId, file);
      _events.add(
        ReceiverEvent(
          handoffId: handoffId,
          kind: ReceiverEventKind.failed,
          bytesReceived: received,
          totalBytes: offer.sizeBytes,
          errorCode: HandoffErrorCode.incompleteTransfer,
        ),
      );
      await _sendJson(
        request,
        HttpStatus.ok,
        jsonEncode({'state': 'FAILED', 'error_code': HandoffErrorCode.incompleteTransfer.wireName}),
      );
      return;
    }

    final actualSha256 = hashOutput.events.single.toString();
    if (actualSha256 != offer.sha256) {
      await tempCache.discard(handoffId, file);
      _events.add(
        ReceiverEvent(
          handoffId: handoffId,
          kind: ReceiverEventKind.failed,
          bytesReceived: received,
          totalBytes: offer.sizeBytes,
          errorCode: HandoffErrorCode.integrityMismatch,
        ),
      );
      await _sendJson(
        request,
        HttpStatus.ok,
        jsonEncode({'state': 'FAILED', 'error_code': HandoffErrorCode.integrityMismatch.wireName}),
      );
      return;
    }

    tempCache.markReceived(handoffId);
    _events.add(
      ReceiverEvent(
        handoffId: handoffId,
        kind: ReceiverEventKind.received,
        bytesReceived: received,
        totalBytes: offer.sizeBytes,
        file: file,
        sha256: actualSha256,
        mimeType: offer.mimeType,
        preferredFilename: offer.preferredFilename ?? offer.displayName,
      ),
    );
    await _sendJson(
      request,
      HttpStatus.ok,
      jsonEncode({'state': 'RECEIVED', 'bytes_received': received, 'sha256': actualSha256}),
    );
  }

  Future<void> _drain(HttpRequest request) async {
    try {
      await request.drain<void>();
    } catch (_) {}
  }

  Future<void> _reject(HttpRequest request, int status, HandoffErrorCode code) async {
    await _sendJson(request, status, jsonEncode({'state': 'FAILED', 'error_code': code.wireName}));
  }

  Future<void> _sendJson(HttpRequest request, int status, String body) async {
    try {
      final bytes = utf8.encode(body);
      request.response.statusCode = status;
      request.response.headers.contentType = ContentType.json;
      request.response.contentLength = bytes.length;
      request.response.add(bytes);
      await request.response.close();
    } catch (_) {}
  }

  static List<int> _hexToBytes(String hex) {
    final out = List<int>.filled(hex.length ~/ 2, 0);
    for (var i = 0; i < out.length; i++) {
      out[i] = int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16);
    }
    return out;
  }
}
