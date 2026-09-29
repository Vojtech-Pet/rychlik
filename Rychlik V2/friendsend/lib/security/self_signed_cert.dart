import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:basic_utils/basic_utils.dart';
import 'package:pointycastle/asn1.dart' as pc;

/// Generates a minimal, strictly RFC-5480-conformant self-signed ECDSA
/// P-256 X.509 certificate for FriendSend's local TLS identity (Prompt
/// A15 §7).
///
/// `package:basic_utils`'s own `X509Utils.generateSelfSignedCertificate`
/// was tried first and is otherwise exactly the right tool for this (a
/// standard library rather than ad-hoc ASN.1 code, per §7's own
/// instruction) -- but it unconditionally encodes the signature
/// algorithm's parameters field as `ASN1Null()`, which is correct for
/// RSA but invalid for ECDSA (RFC 5480 §2.1.1 requires this field to be
/// ABSENT for ecdsa-with-SHAxxx). Real-world confirmation: Python's
/// `cryptography` library (used by the desktop) refuses to parse the
/// resulting certificate at all ("ExtraData" in
/// `TbsCertificate::signature_alg`), even though Dart's own `dart:io`
/// TLS stack is lenient enough to serve it. Since cross-language
/// interoperability is exactly what this phase must prove, this file
/// reuses the same standard ASN.1 primitives
/// (`package:pointycastle/export.dart`, the same library
/// `X509Utils` itself is built on) with that one field corrected, rather
/// than either hand-rolling raw certificate bytes or silently trusting a
/// certificate a real peer cannot even parse.
class SelfSignedCertificate {
  const SelfSignedCertificate({required this.certificatePem, required this.privateKeyPem, required this.publicKeyPem});

  final String certificatePem;
  final String privateKeyPem;

  /// The raw SubjectPublicKeyInfo PEM block -- the exact bytes an SPKI
  /// pin is computed from (Prompt A15 §8/§9).
  final String publicKeyPem;

  static const _ecdsaWithSha256Oid = '1.2.840.10045.4.3.2';

  static SelfSignedCertificate generate({required String commonName, int validityDays = 3650}) {
    final keyPair = CryptoUtils.generateEcKeyPair(curve: 'prime256v1');
    final privateKey = keyPair.privateKey as ECPrivateKey;
    final publicKey = keyPair.publicKey as ECPublicKey;

    final privateKeyPem = CryptoUtils.encodeEcPrivateKeyToPem(privateKey);
    final publicKeyPem = CryptoUtils.encodeEcPublicKeyToPem(publicKey);
    final spkiDer = _pemToDer(publicKeyPem);
    final spkiSequence = pc.ASN1Parser(spkiDer).nextObject();

    final tbs = pc.ASN1Sequence();

    // version (v3)
    final version = pc.ASN1Object(tag: 0xA0);
    version.valueBytes = pc.ASN1Integer(BigInt.from(2)).encode();
    tbs.add(version);

    // serialNumber -- a fresh random positive integer, never a fixed "1"
    // (avoids any chance of two certs from this generator colliding).
    final serialBytes = Uint8List.fromList(List<int>.generate(16, (_) => Random.secure().nextInt(256)));
    serialBytes[0] &= 0x7f; // ensure a positive INTEGER encoding
    tbs.add(pc.ASN1Integer(_bytesToBigInt(serialBytes)));

    // signature AlgorithmIdentifier -- ecdsa-with-SHA256, NO parameters
    // field at all (the RFC 5480 fix this class exists for).
    final algorithmId = pc.ASN1Sequence();
    algorithmId.add(pc.ASN1ObjectIdentifier.fromIdentifierString(_ecdsaWithSha256Oid));
    tbs.add(algorithmId);

    // issuer == subject (self-signed)
    final nameSeq = _nameSequence(commonName);
    tbs.add(nameSeq);

    // validity
    final validitySeq = pc.ASN1Sequence();
    final notBefore = DateTime.now().toUtc().subtract(const Duration(minutes: 5));
    final notAfter = notBefore.add(Duration(days: validityDays));
    validitySeq.add(pc.ASN1UtcTime(notBefore));
    validitySeq.add(pc.ASN1UtcTime(notAfter));
    tbs.add(validitySeq);

    // subject
    tbs.add(_nameSequence(commonName));

    // subjectPublicKeyInfo -- reuse the exact SPKI bytes a pin is
    // computed from, so the certificate and the pin always agree.
    tbs.add(spkiSequence);

    final tbsDer = tbs.encode();
    final signature = X509Utils.eccSign(Uint8List.fromList(tbsDer), privateKey, 'SHA-256');

    final signatureValueSeq = pc.ASN1Sequence();
    signatureValueSeq.add(pc.ASN1Integer(signature.r));
    signatureValueSeq.add(pc.ASN1Integer(signature.s));
    final signatureBitString = pc.ASN1BitString(stringValues: signatureValueSeq.encode());

    final certificate = pc.ASN1Sequence();
    certificate.add(tbs);
    certificate.add(algorithmId);
    certificate.add(signatureBitString);

    final certDer = certificate.encode();
    final certPem = _derToPem(Uint8List.fromList(certDer), 'CERTIFICATE');

    return SelfSignedCertificate(certificatePem: certPem, privateKeyPem: privateKeyPem, publicKeyPem: publicKeyPem);
  }

  static pc.ASN1Sequence _nameSequence(String commonName) {
    final rdn = pc.ASN1Sequence();
    rdn.add(pc.ASN1ObjectIdentifier.fromIdentifierString('2.5.4.3')); // CN
    rdn.add(pc.ASN1UTF8String(utf8StringValue: commonName));
    final set = pc.ASN1Set();
    set.add(rdn);
    final name = pc.ASN1Sequence();
    name.add(set);
    return name;
  }

  static Uint8List _pemToDer(String pem) {
    final b64 = pem.split('\n').where((line) => !line.startsWith('-----')).join();
    return base64.decode(b64);
  }

  static String _derToPem(Uint8List der, String label) {
    final b64 = base64.encode(der);
    final chunks = <String>[];
    for (var i = 0; i < b64.length; i += 64) {
      chunks.add(b64.substring(i, i + 64 > b64.length ? b64.length : i + 64));
    }
    return '-----BEGIN $label-----\n${chunks.join('\n')}\n-----END $label-----';
  }

  static BigInt _bytesToBigInt(Uint8List bytes) {
    var result = BigInt.zero;
    for (final byte in bytes) {
      result = (result << 8) | BigInt.from(byte);
    }
    return result;
  }
}
