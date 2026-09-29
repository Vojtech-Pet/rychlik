import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart' as dcrypto;

import 'self_signed_cert.dart';

/// FriendSend's persistent local TLS identity (Prompt A15 §7/§10-12).
///
/// A self-signed ECDSA P-256 certificate + private key, generated once
/// and persisted in app-private storage. Never placed in Downloads,
/// external storage, or SharedPreferences plaintext (§10); the Android
/// manifest/backup configuration additionally excludes this directory
/// from generic Android backup (§11 -- see
/// docs/FRIENDSEND_ANDROID_SECURITY.md for the exact mechanism).
class TlsIdentity {
  const TlsIdentity({required this.certificatePem, required this.privateKeyPem, required this.spkiSha256});

  final String certificatePem;
  final String privateKeyPem;

  /// 32 raw bytes -- the trust anchor pairing binds to (§8/§9), never a
  /// hostname or IP.
  final List<int> spkiSha256;

  String get spkiSha256Hex => spkiSha256.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

  SecurityContext buildSecurityContext() {
    final context = SecurityContext();
    context.useCertificateChainBytes(certificatePem.codeUnits);
    context.usePrivateKeyBytes(privateKeyPem.codeUnits);
    return context;
  }
}

class TlsIdentityStore {
  TlsIdentityStore(this._directory, {this.commonName = 'friendsend-device'});

  final Directory _directory;
  final String commonName;

  File get _certFile => File('${_directory.path}/friendsend_tls_identity.json');

  /// Idempotent: loads the existing identity if present (identity must
  /// survive restart, matching device_id persistence), otherwise
  /// generates and persists a fresh one, atomically (§89: a half-written
  /// identity file must never be silently regenerated over).
  Future<TlsIdentity> loadOrCreate() async {
    final file = _certFile;
    if (await file.exists()) {
      final raw = await file.readAsString();
      final json = jsonDecode(raw) as Map<String, dynamic>;
      final certPem = json['certificate_pem'] as String;
      final keyPem = json['private_key_pem'] as String;
      final pubPem = json['public_key_pem'] as String;
      return TlsIdentity(certificatePem: certPem, privateKeyPem: keyPem, spkiSha256: _spkiFromPublicKeyPem(pubPem));
    }

    final generated = SelfSignedCertificate.generate(commonName: commonName);
    await _directory.create(recursive: true);
    final tmp = File('${file.path}.tmp');
    await tmp.writeAsString(
      jsonEncode({
        'certificate_pem': generated.certificatePem,
        'private_key_pem': generated.privateKeyPem,
        'public_key_pem': generated.publicKeyPem,
      }),
    );
    await tmp.rename(file.path);

    return TlsIdentity(
      certificatePem: generated.certificatePem,
      privateKeyPem: generated.privateKeyPem,
      spkiSha256: _spkiFromPublicKeyPem(generated.publicKeyPem),
    );
  }

  static List<int> _spkiFromPublicKeyPem(String pem) {
    final b64 = pem
        .replaceAll('-----BEGIN PUBLIC KEY-----', '')
        .replaceAll('-----END PUBLIC KEY-----', '')
        .replaceAll('\n', '')
        .trim();
    final der = base64.decode(b64);
    return dcrypto.sha256.convert(der).bytes;
  }
}
