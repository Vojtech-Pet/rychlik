import 'dart:math';

import 'package:cryptography/cryptography.dart';

import 'canonical.dart';
import 'desktop_trust_store.dart';

/// A bounded, one-time, expiring authentication challenge (Prompt A15
/// §42-45) FriendSend issues so a connecting desktop can prove it holds
/// the private key matching a previously-paired [TrustedDesktop].
class AuthChallenge {
  AuthChallenge({required this.challengeId, required this.nonce, required this.expiresAt, required DateTime Function() clock})
    : _clock = clock;

  final String challengeId;
  final List<int> nonce;
  final DateTime expiresAt;
  final DateTime Function() _clock;

  bool get isExpired => _clock().isAfter(expiresAt);
}

enum AuthResult { ok, untrustedDesktop, authenticationFailed, challengeExpired, challengeReplayed }

/// Issues challenges and verifies desktop Ed25519 signatures against a
/// specific handoff/artifact (Prompt A15 §46-49) -- binds authentication
/// to the intended transfer so a captured signature cannot authorize a
/// different one.
class AuthChallengeManager {
  AuthChallengeManager({
    required this.trustStore,
    this.securityProfile = 'pinned-tls-signature-v1',
    this.protocolVersion = 1,
    Duration challengeTtl = const Duration(seconds: 45),
    Random? random,
    DateTime Function()? clock,
  }) : _challengeTtl = challengeTtl,
       _random = random ?? Random.secure(),
       _clock = clock ?? (() => DateTime.now().toUtc());

  final DesktopTrustStore trustStore;
  final String securityProfile;
  final int protocolVersion;
  final Duration _challengeTtl;
  final Random _random;
  final DateTime Function() _clock;
  final Map<String, AuthChallenge> _pending = {};
  final Set<String> _consumed = {};

  AuthChallenge issueChallenge() {
    final challengeId = _randomHex(16);
    final nonce = List<int>.generate(32, (_) => _random.nextInt(256));
    final challenge = AuthChallenge(
      challengeId: challengeId,
      nonce: nonce,
      expiresAt: _clock().add(_challengeTtl),
      clock: _clock,
    );
    _pending[challengeId] = challenge;
    return challenge;
  }

  /// Verifies a desktop's signature over the canonical signature input
  /// for one specific handoff (§46). The challenge is consumed
  /// (one-time, §45) regardless of outcome once looked up, so a captured
  /// valid signature cannot be replayed for a second handoff (§49).
  Future<AuthResult> verify({
    required String desktopInstanceId,
    required String deviceId,
    required String challengeId,
    required String handoffId,
    required List<int> artifactSha256,
    required int artifactSizeBytes,
    required List<int> signature,
  }) async {
    final challenge = _pending.remove(challengeId);
    if (challenge == null || _consumed.contains(challengeId)) {
      return AuthResult.challengeReplayed;
    }
    _consumed.add(challengeId);
    if (challenge.isExpired) {
      return AuthResult.challengeExpired;
    }

    final desktop = await trustStore.get(desktopInstanceId);
    if (desktop == null) {
      return AuthResult.untrustedDesktop;
    }

    final signingInput = authSignatureInput(
      securityProfile: securityProfile,
      protocolVersion: protocolVersion,
      desktopInstanceId: desktopInstanceId,
      deviceId: deviceId,
      challengeId: challengeId,
      challengeNonce: challenge.nonce,
      handoffId: handoffId,
      artifactSha256: artifactSha256,
      artifactSizeBytes: artifactSizeBytes,
    );

    final algorithm = Ed25519();
    final publicKey = SimplePublicKey(desktop.desktopPublicSigningKey, type: KeyPairType.ed25519);
    final ok = await algorithm.verify(signingInput, signature: Signature(signature, publicKey: publicKey));
    return ok ? AuthResult.ok : AuthResult.authenticationFailed;
  }

  String _randomHex(int bytes) {
    final list = List<int>.generate(bytes, (_) => _random.nextInt(256));
    return list.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }
}
