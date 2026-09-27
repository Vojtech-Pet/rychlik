import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:convert/convert.dart' show AccumulatorSink;
import 'package:crypto/crypto.dart';

import '../protocol/models.dart';
import '../protocol/protocol.dart';
import 'temp_cache.dart';

/// Configuration for one running receiver instance. Deliberately generic
/// capability set -- never a per-app whitelist (Prompt A14 §37/§56).
class ReceiverConfig {
  ReceiverConfig({
    required this.deviceId,
    required this.displayName,
    this.platform = 'android',
    this.capabilities = const {DeviceCapability.receiveStream, DeviceCapability.temporaryFileHandoff},
    this.protocolVersion = friendSendProtocolVersion,
    this.maxPayloadBytes,
    this.supportedMimeTypes,
  });

  final String deviceId;
  final String displayName;
  final String platform;
  final Set<DeviceCapability> capabilities;
  final int protocolVersion;

  /// Null means "no product maximum chosen yet" (§36) -- test-configurable.
  final int? maxPayloadBytes;

  /// Null means "accept any declared MIME type" for this MVP.
  final Set<String>? supportedMimeTypes;
}

enum ReceiverEventKind { progress, received, failed, cancelled }

class ReceiverEvent {
  ReceiverEvent({
    required this.handoffId,
    required this.kind,
    required this.bytesReceived,
    required this.totalBytes,
    this.errorCode,
    this.file,
    this.sha256,
    this.mimeType,
    this.preferredFilename,
  });

  final String handoffId;
  final ReceiverEventKind kind;
  final int bytesReceived;
  final int totalBytes;
  final HandoffErrorCode? errorCode;

  /// Only set for [ReceiverEventKind.received] -- the verified, still-
  /// retained-until-TTL temp file (§62/§130).
  final File? file;
  final String? sha256;
  final String? mimeType;
  final String? preferredFilename;
}

/// The real FriendSend receiver core: a genuine `dart:io` `HttpServer`
/// implementing the two-phase offer/stream exchange from
/// docs/FRIENDSEND_PROTOCOL_V1.md.
///
/// This is application code, not a test double (Prompt A14 §95) -- both
/// the Flutter app and the host-side cross-language test harness
/// (bin/receiver_harness.dart) construct and drive this same class.
/// Streaming is bounded: `dart:io` delivers the request body as a chunked
/// `Stream<List<int>>`, so the full payload is never buffered in memory
/// (§40/§41) -- each chunk is hashed incrementally and written straight to
/// the temp-cache file.
class FriendSendReceiverServer {
  FriendSendReceiverServer({required this.config, required this.tempCache});

  final ReceiverConfig config;
  final TempCache tempCache;

  HttpServer? _server;
  final Set<String> _acceptedTokens = {};
  final Map<String, HandoffOffer> _pendingOffers = {};
  final Set<String> _cancelRequested = {};
  final StreamController<ReceiverEvent> _events = StreamController.broadcast();

  bool forceCapabilityRejection = false;
  bool forceReceiverRejection = false;

  Stream<ReceiverEvent> get events => _events.stream;

  int get port => _server!.port;
  String get host => _server!.address.address;

  Future<void> start({InternetAddress? address}) async {
    _server = await HttpServer.bind(address ?? InternetAddress.loopbackIPv4, 0);
    unawaited(_serve());
  }

  Future<void> stop() async {
    await _server?.close(force: true);
    _server = null;
  }

  /// A pairing completion (out of scope of the v1 wire contract itself,
  /// §17) results in a fresh auth token this receiver will accept on
  /// subsequent `/handoff/*` requests from that sender.
  void acceptToken(String token) => _acceptedTokens.add(token);

  /// Receiver-side cancel (§71/§123): stops reading further body bytes for
  /// this handoff and drops the connection early, mirroring "connection
  /// closed early = treat as incomplete/cancelled" in the protocol doc.
  /// Protocol v1 defines no receiver->sender cancel acknowledgement
  /// endpoint, so this never invents one.
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
      } catch (_) {
        // Connection already gone -- nothing more to do.
      }
    }
  }

  Future<void> _handleHello(HttpRequest request) async {
    final body = jsonEncode({
      'protocol_version': config.protocolVersion,
      'capabilities': config.capabilities.map((c) => c.wireName).toList(),
      'platform': config.platform,
      'device_id': config.deviceId,
      'display_name': config.displayName,
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

    final rejection = _preflightReject(request, offer);
    if (rejection != null) {
      await _reject(request, rejection.$1, rejection.$2);
      return;
    }

    _pendingOffers[offer.handoffId] = offer;
    await _sendJson(request, HttpStatus.ok, jsonEncode({'accepted': true}));
  }

  (int, HandoffErrorCode)? _preflightReject(HttpRequest request, HandoffOffer offer) {
    final protocolHeader = request.headers.value(headerProtocolVersion);
    if (protocolHeader != config.protocolVersion.toString()) {
      return (HttpStatus.badRequest, HandoffErrorCode.unsupportedProtocol);
    }
    final token = request.headers.value(headerAuthToken);
    if (token == null || !_acceptedTokens.contains(token)) {
      return (HttpStatus.unauthorized, HandoffErrorCode.authenticationFailed);
    }
    if (forceCapabilityRejection) {
      return (HttpStatus.badRequest, HandoffErrorCode.unsupportedCapability);
    }
    final maxBytes = config.maxPayloadBytes;
    if (maxBytes != null && offer.sizeBytes > maxBytes) {
      return (HttpStatus.requestEntityTooLarge, HandoffErrorCode.payloadTooLarge);
    }
    final supportedMime = config.supportedMimeTypes;
    if (supportedMime != null && !supportedMime.contains(offer.mimeType)) {
      return (HttpStatus.unsupportedMediaType, HandoffErrorCode.unsupportedMedia);
    }
    if (forceReceiverRejection) {
      return (HttpStatus.badRequest, HandoffErrorCode.receiverRejected);
    }
    return null;
  }

  Future<void> _handleStream(HttpRequest request) async {
    final handoffId = request.headers.value(headerHandoffId);
    final token = request.headers.value(headerAuthToken);
    final declaredLength = request.contentLength >= 0 ? request.contentLength : 0;

    final offer = handoffId == null ? null : _pendingOffers.remove(handoffId);
    final tokenOk = token != null && _acceptedTokens.contains(token);

    if (offer == null || !tokenOk) {
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

    final file = await tempCache.allocate(handoffId!);
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
          ),
        );
      }
    } on Object {
      // The protocol doc treats a connection closed early by either side
      // as incomplete/cancelled -- never an unhandled 500 (§46).
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
      // The sender may still be mid-upload of a request body we've
      // decided to stop reading -- there is no clean HTTP response for
      // "I cancelled partway through your POST", so this forcibly drops
      // the connection (detach + destroy) rather than risk a graceful
      // close() hanging while waiting on an unfinished request body. This
      // is exactly the "connection closed early" case the protocol doc
      // already treats as incomplete/cancelled on the sender's side.
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

    // Both checks passed -- only now may RECEIVED be reported (§44/§49).
    // Unlike the Python test fixture, the real receiver keeps the
    // verified file until Sharesheet handoff + TTL cleanup (§62/§130).
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
    } catch (_) {
      // A connection error while draining is fine -- the client will see
      // its own connection failure.
    }
  }

  Future<void> _reject(HttpRequest request, int status, HandoffErrorCode code) async {
    await _sendJson(request, status, jsonEncode({'state': 'FAILED', 'error_code': code.wireName}));
  }

  Future<void> _sendJson(HttpRequest request, int status, String body) async {
    try {
      final bytes = utf8.encode(body);
      request.response.statusCode = status;
      request.response.headers.contentType = ContentType.json;
      // Explicit Content-Length rather than the dart:io default of
      // chunked transfer-encoding for a response with no declared
      // length -- some HTTP clients (observed: Python `requests`) fail
      // to decode dart:io's chunked response framing cleanly.
      request.response.contentLength = bytes.length;
      request.response.add(bytes);
      await request.response.close();
    } catch (_) {
      // Peer disconnected before the response could be written.
    }
  }
}
