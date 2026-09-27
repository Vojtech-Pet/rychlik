import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/security/canonical.dart';

Map<String, dynamic> _fixture(String name) {
  final file = File('../protocol/fixtures/security_v1/$name');
  return jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
}

Uint8List _hex(String s) {
  final out = Uint8List(s.length ~/ 2);
  for (var i = 0; i < out.length; i++) {
    out[i] = int.parse(s.substring(i * 2, i * 2 + 2), radix: 16);
  }
  return out;
}

String _toHex(List<int> bytes) => bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

void main() {
  test('pairing transcript matches the shared cross-language vector', () async {
    final vector = _fixture('pairing_transcript.json');
    final inputs = vector['inputs'] as Map<String, dynamic>;

    final transcript = pairingTranscript(
      securityProfile: inputs['security_profile'] as String,
      protocolVersion: inputs['protocol_version'] as int,
      pairingSessionId: inputs['pairing_session_id'] as String,
      desktopInstanceId: inputs['desktop_instance_id'] as String,
      desktopPublicSigningKey: _hex(inputs['desktop_public_signing_key_hex'] as String),
      desktopNonce: _hex(inputs['desktop_nonce_hex'] as String),
      deviceId: inputs['device_id'] as String,
      friendSendDisplayName: inputs['friendsend_display_name'] as String,
      friendSendTlsSpkiSha256: _hex(inputs['friendsend_tls_spki_sha256_hex'] as String),
      friendSendEndpointHost: inputs['friendsend_endpoint_host'] as String,
      friendSendEndpointPort: inputs['friendsend_endpoint_port'] as int,
      deviceNonce: _hex(inputs['device_nonce_hex'] as String),
    );

    expect(_toHex(transcript), vector['expected_transcript_hex']);
    expect(transcript.length, vector['expected_transcript_length']);

    final secretBytes = Uint8List.fromList(utf8.encode(vector['pairing_secret'] as String));
    final hmacAlgo = Hmac.sha256();
    final secretKey = SecretKey(secretBytes);

    final proofAInput = Uint8List.fromList([...pairingProofADomain, ...transcript]);
    final proofA = await hmacAlgo.calculateMac(proofAInput, secretKey: secretKey);
    expect(_toHex(proofA.bytes), vector['expected_proof_a_hmac_sha256_hex']);

    final proofBInput = Uint8List.fromList([...pairingProofBDomain, ...transcript]);
    final proofB = await hmacAlgo.calculateMac(proofBInput, secretKey: secretKey);
    expect(_toHex(proofB.bytes), vector['expected_proof_b_hmac_sha256_hex']);

    expect(_toHex(proofA.bytes), isNot(_toHex(proofB.bytes)));
  });

  test('auth signature input matches the shared vector and Ed25519 round-trips', () async {
    final vector = _fixture('signed_handoff.json');
    final inputs = vector['inputs'] as Map<String, dynamic>;

    final sigInput = authSignatureInput(
      securityProfile: inputs['security_profile'] as String,
      protocolVersion: inputs['protocol_version'] as int,
      desktopInstanceId: inputs['desktop_instance_id'] as String,
      deviceId: inputs['device_id'] as String,
      challengeId: inputs['challenge_id'] as String,
      challengeNonce: _hex(inputs['challenge_nonce_hex'] as String),
      handoffId: inputs['handoff_id'] as String,
      artifactSha256: _hex(inputs['artifact_sha256_hex'] as String),
      artifactSizeBytes: inputs['artifact_size_bytes'] as int,
    );

    expect(_toHex(sigInput), vector['expected_signature_input_hex']);
    expect(sigInput.length, vector['expected_signature_input_length']);

    final algorithm = Ed25519();
    final seed = _hex(vector['ed25519_private_key_seed_hex'] as String);
    final keyPair = await algorithm.newKeyPairFromSeed(seed);
    final publicKey = await keyPair.extractPublicKey();
    expect(_toHex(publicKey.bytes), vector['ed25519_public_key_hex']);

    final signature = await algorithm.sign(sigInput, keyPair: keyPair);
    expect(_toHex(signature.bytes), vector['expected_signature_hex']);

    final expectedSignature = Signature(_hex(vector['expected_signature_hex'] as String), publicKey: publicKey);
    final verified = await algorithm.verify(sigInput, signature: expectedSignature);
    expect(verified, isTrue);
  });
}
