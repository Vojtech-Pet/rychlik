/// FriendSend Protocol v1 wire message models.
///
/// Parsed strictly from JSON per docs/FRIENDSEND_PROTOCOL_V1.md "Required
/// fields" -- an unrecognized *required* field, a negative size, or a
/// malformed sha256 is rejected before any payload byte is read (Prompt
/// A14 §34/§45). Unknown *optional* fields are ignored for forward
/// compatibility (§8/§145).
library;

final RegExp _sha256Pattern = RegExp(r'^[0-9a-f]{64}$');

class ProtocolFormatException implements Exception {
  ProtocolFormatException(this.message);

  final String message;

  @override
  String toString() => 'ProtocolFormatException: $message';
}

/// `POST /handoff/offer` request body.
class HandoffOffer {
  HandoffOffer({
    required this.handoffId,
    required this.displayName,
    required this.mimeType,
    required this.sizeBytes,
    required this.sha256,
    this.preferredFilename,
  }) {
    if (handoffId.isEmpty) {
      throw ProtocolFormatException('handoff_id must not be empty');
    }
    if (sizeBytes < 0) {
      throw ProtocolFormatException('size_bytes must not be negative');
    }
    if (!_sha256Pattern.hasMatch(sha256)) {
      throw ProtocolFormatException(
        'sha256 must be 64 lowercase hex characters, got "$sha256"',
      );
    }
  }

  final String handoffId;
  final String displayName;
  final String mimeType;
  final int sizeBytes;
  final String sha256;
  final String? preferredFilename;

  /// Throws [ProtocolFormatException] for a missing required field rather
  /// than a raw [TypeError] -- callers must never see an unbounded parser
  /// stack trace (§18).
  factory HandoffOffer.fromJson(Map<String, dynamic> json) {
    final handoffId = _requireString(json, 'handoff_id');
    final displayName = _requireString(json, 'display_name');
    final mimeType = _requireString(json, 'mime_type');
    final sizeBytes = _requireInt(json, 'size_bytes');
    final sha256 = _requireString(json, 'sha256');
    final preferredFilename = json['preferred_filename'] as String?;
    return HandoffOffer(
      handoffId: handoffId,
      displayName: displayName,
      mimeType: mimeType,
      sizeBytes: sizeBytes,
      sha256: sha256,
      preferredFilename: preferredFilename,
    );
  }

  Map<String, dynamic> toJson() => {
    'handoff_id': handoffId,
    'display_name': displayName,
    'mime_type': mimeType,
    'size_bytes': sizeBytes,
    'sha256': sha256,
    'preferred_filename': preferredFilename,
  };
}

/// A pairing session offer, matching Python's `PairingPayload.to_wire_dict()`.
/// A14 does not mandate a specific wire endpoint for completing pairing
/// (protocol doc §"Pairing semantics" / A14 prompt §17) -- this model
/// parses the payload a user pastes from the desktop, out of band.
class PairingPayload {
  PairingPayload({
    required this.protocolVersion,
    required this.pairingSessionId,
    required this.desktopInstanceId,
    required this.endpointHost,
    required this.endpointPort,
    required this.secret,
    required this.expiresAtUtc,
  }) {
    if (pairingSessionId.isEmpty) {
      throw ProtocolFormatException('pairing_session_id must not be empty');
    }
    if (secret.isEmpty) {
      throw ProtocolFormatException('secret must not be empty');
    }
    if (endpointHost.isEmpty) {
      throw ProtocolFormatException('endpoint.host must not be empty');
    }
    if (endpointPort <= 0 || endpointPort >= 65536) {
      throw ProtocolFormatException('endpoint.port out of range: $endpointPort');
    }
  }

  final int protocolVersion;
  final String pairingSessionId;
  final String desktopInstanceId;
  final String endpointHost;
  final int endpointPort;
  final String secret;
  final DateTime expiresAtUtc;

  bool get isExpired => DateTime.now().toUtc().isAfter(expiresAtUtc);

  factory PairingPayload.fromJson(Map<String, dynamic> json) {
    final endpoint = json['endpoint'];
    if (endpoint is! Map) {
      throw ProtocolFormatException('endpoint must be an object');
    }
    final expiresRaw = _requireString(json, 'expires_at_utc');
    final expires = DateTime.tryParse(expiresRaw);
    if (expires == null) {
      throw ProtocolFormatException('expires_at_utc is not a valid ISO-8601 timestamp');
    }
    return PairingPayload(
      protocolVersion: _requireInt(json, 'protocol_version'),
      pairingSessionId: _requireString(json, 'pairing_session_id'),
      desktopInstanceId: _requireString(json, 'desktop_instance_id'),
      endpointHost: _requireString(endpoint.cast<String, dynamic>(), 'host'),
      endpointPort: _requireInt(endpoint.cast<String, dynamic>(), 'port'),
      secret: _requireString(json, 'secret'),
      expiresAtUtc: expires.toUtc(),
    );
  }

  /// Never used for logs/errors -- callers display this, not [secret]
  /// (Prompt A14 §20).
  String get redactedDescription =>
      'PairingPayload(session=$pairingSessionId, desktop=$desktopInstanceId, '
      'endpoint=$endpointHost:$endpointPort, secret=<redacted>)';
}

String _requireString(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! String || value.isEmpty) {
    throw ProtocolFormatException('missing or invalid required field "$key"');
  }
  return value;
}

int _requireInt(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! int) {
    throw ProtocolFormatException('missing or invalid required field "$key"');
  }
  return value;
}
