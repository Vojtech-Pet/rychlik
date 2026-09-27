import 'dart:convert';
import 'dart:math';

import '../protocol/models.dart';
import '../receiver/receiver_server.dart';

/// Mobile-side pairing (Prompt A14 §16-21).
///
/// Protocol v1 does not mandate a specific wire endpoint for completing
/// pairing (docs/FRIENDSEND_PROTOCOL_V1.md, "Pairing semantics"): A14
/// implements the desktop->phone half of that exchange as an explicit,
/// documented simplification -- the user pastes the [PairingPayload] JSON
/// the desktop produced (`PairingManager.create_session()` /
/// `.to_wire_dict()` in `rychlik.device.pairing`) out of band (QR/manual
/// entry), and this class locally validates it and mints a fresh auth
/// token that the receiver will require on subsequent `/handoff/*`
/// requests. There is deliberately no callback endpoint here that would
/// let the phone report acceptance back to the desktop over the wire --
/// that full mutual-trust wire flow is explicitly deferred (see A14
/// prompt §14-15, A15 scope).
class PairingException implements Exception {
  PairingException(this.message);

  final String message;

  @override
  String toString() => 'PairingException: $message';
}

class PairingExpiredException extends PairingException {
  PairingExpiredException() : super('pairing session has expired');
}

class PairingUnsupportedProtocolException extends PairingException {
  PairingUnsupportedProtocolException(int version)
    : super('unsupported pairing protocol version: $version');
}

class PairingManager {
  PairingManager({required this.receiver, Random? random}) : _random = random ?? Random.secure();

  final FriendSendReceiverServer receiver;
  final Random _random;

  /// Throws [ProtocolFormatException] for malformed JSON/fields -- never a
  /// raw parser stack trace reaches the UI (§18).
  PairingPayload parse(String rawJson) {
    final Map<String, dynamic> json;
    try {
      json = jsonDecode(rawJson) as Map<String, dynamic>;
    } on FormatException {
      throw ProtocolFormatException('pairing payload is not valid JSON');
    }
    return PairingPayload.fromJson(json);
  }

  /// Validates expiry/version and mints a fresh, freshly-random auth
  /// token this receiver will now accept (never persists the pairing
  /// [PairingPayload.secret] itself, §13/§20/§137).
  String completePairing(PairingPayload payload) {
    if (payload.protocolVersion != receiver.config.protocolVersion) {
      throw PairingUnsupportedProtocolException(payload.protocolVersion);
    }
    if (payload.isExpired) {
      throw PairingExpiredException();
    }
    final token = _generateToken();
    receiver.acceptToken(token);
    return token;
  }

  String _generateToken() {
    final bytes = List<int>.generate(16, (_) => _random.nextInt(256));
    return bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }
}
