/// Deterministic canonical byte encoding for the Prompt A15 pairing
/// transcript and auth-challenge signature input.
///
/// Mirrors src/rychlik/device/security/canonical.py byte-for-byte --
/// every field is length-prefixed (4-byte big-endian length + raw
/// bytes), concatenated in a fixed order. Never signs/HMACs arbitrary
/// JSON from two different serializers (A15 prompt §30). Cross-language
/// agreement is verified with shared vectors in
/// protocol/fixtures/security_v1/.
library;

import 'dart:convert';
import 'dart:typed_data';

/// A field value: `String` (UTF-8 encoded), `int` (encoded as its
/// decimal ASCII string), or `List<int>`/`Uint8List` (raw bytes).
Uint8List encodeField(Object value) {
  final Uint8List raw;
  if (value is int) {
    raw = Uint8List.fromList(utf8.encode(value.toString()));
  } else if (value is String) {
    raw = Uint8List.fromList(utf8.encode(value));
  } else if (value is List<int>) {
    raw = Uint8List.fromList(value);
  } else {
    throw ArgumentError('unsupported field type: ${value.runtimeType}');
  }
  final lengthPrefix = ByteData(4)..setUint32(0, raw.length, Endian.big);
  final out = BytesBuilder();
  out.add(lengthPrefix.buffer.asUint8List());
  out.add(raw);
  return out.toBytes();
}

Uint8List canonicalBytes(List<Object> fields) {
  final out = BytesBuilder();
  for (final field in fields) {
    out.add(encodeField(field));
  }
  return out.toBytes();
}

final Uint8List pairingProofADomain = Uint8List.fromList(utf8.encode('FRIENDSEND-PAIRING-A15-PROOF-A'));
final Uint8List pairingProofBDomain = Uint8List.fromList(utf8.encode('FRIENDSEND-PAIRING-A15-PROOF-B'));

Uint8List pairingTranscript({
  required String securityProfile,
  required int protocolVersion,
  required String pairingSessionId,
  required String desktopInstanceId,
  required List<int> desktopPublicSigningKey,
  required List<int> desktopNonce,
  required String deviceId,
  required String friendSendDisplayName,
  required List<int> friendSendTlsSpkiSha256,
  required String friendSendEndpointHost,
  required int friendSendEndpointPort,
  required List<int> deviceNonce,
}) {
  return canonicalBytes([
    securityProfile,
    protocolVersion,
    pairingSessionId,
    desktopInstanceId,
    desktopPublicSigningKey,
    desktopNonce,
    deviceId,
    friendSendDisplayName,
    friendSendTlsSpkiSha256,
    friendSendEndpointHost,
    friendSendEndpointPort,
    deviceNonce,
  ]);
}

Uint8List authSignatureInput({
  required String securityProfile,
  required int protocolVersion,
  required String desktopInstanceId,
  required String deviceId,
  required String challengeId,
  required List<int> challengeNonce,
  required String handoffId,
  required List<int> artifactSha256,
  required int artifactSizeBytes,
}) {
  return canonicalBytes([
    securityProfile,
    protocolVersion,
    desktopInstanceId,
    deviceId,
    challengeId,
    challengeNonce,
    handoffId,
    artifactSha256,
    artifactSizeBytes,
  ]);
}
