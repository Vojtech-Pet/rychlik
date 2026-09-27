// Host-side test harness for the real, secure (pinned-tls-signature-v1)
// FriendSend Dart receiver core (Prompt A15) -- the secure-profile
// analogue of receiver_harness.dart (Prompt A14). Constructs and drives
// the exact same SecureFriendSendReceiverServer / TlsIdentity /
// DesktopTrustStore / AuthChallengeManager classes the Flutter app
// itself uses, so the real cross-language secure E2E can run
// deterministically without an Android emulator.
//
// stdout, once at startup:
//   {"host": "...", "port": ..., "device_id": "...", "tls_spki_sha256": "..."}
// stdout, one line per receiver event (same shape as receiver_harness.dart)
// stdin commands:
//   TRUST_DESKTOP <desktop_instance_id> <base64_ed25519_public_key>
//   CANCEL <handoff_id>
//   STOP
import 'dart:convert';
import 'dart:io';

import 'package:args/args.dart';
import 'package:friendsend/receiver/receiver_server.dart' show ReceiverConfig;
import 'package:friendsend/receiver/temp_cache.dart';
import 'package:friendsend/security/auth_challenge.dart';
import 'package:friendsend/security/desktop_trust_store.dart';
import 'package:friendsend/security/secure_receiver_server.dart';
import 'package:friendsend/security/tls_identity.dart';

Future<void> main(List<String> arguments) async {
  final parser = ArgParser()
    ..addOption('device-id', defaultsTo: 'secure-harness-device')
    ..addOption('display-name', defaultsTo: 'Secure Harness FriendSend Device')
    ..addOption('cache-dir', mandatory: true)
    ..addOption('identity-dir', mandatory: true)
    ..addMultiOption('trust-desktop', help: '<desktop_instance_id>:<base64_pubkey>');

  final args = parser.parse(arguments);

  final identityDir = Directory(args['identity-dir'] as String);
  final tlsIdentity = await TlsIdentityStore(identityDir).loadOrCreate();
  final trustStore = DesktopTrustStore(identityDir);

  for (final entry in args['trust-desktop'] as List<String>) {
    final parts = entry.split(':');
    final desktopInstanceId = parts[0];
    final pubKey = base64.decode(parts[1]);
    await trustStore.upsert(
      TrustedDesktop(
        desktopInstanceId: desktopInstanceId,
        desktopPublicSigningKey: pubKey,
        pairedAtUtc: DateTime.now().toUtc().toIso8601String(),
      ),
    );
  }

  final challengeManager = AuthChallengeManager(trustStore: trustStore);
  final receiver = SecureFriendSendReceiverServer(
    config: ReceiverConfig(deviceId: args['device-id'] as String, displayName: args['display-name'] as String),
    tempCache: TempCache(Directory(args['cache-dir'] as String)),
    tlsIdentity: tlsIdentity,
    authChallengeManager: challengeManager,
  );

  await receiver.start();

  receiver.events.listen((event) {
    final line = jsonEncode({
      'event': event.kind.name,
      'handoff_id': event.handoffId,
      'bytes_received': event.bytesReceived,
      'total_bytes': event.totalBytes,
      'error_code': event.errorCode?.wireName,
      'sha256': event.sha256,
      'file_path': event.file?.path,
    });
    stdout.writeln(line);
  });

  stdout.writeln(
    jsonEncode({
      'host': receiver.host,
      'port': receiver.port,
      'device_id': receiver.config.deviceId,
      'tls_spki_sha256': tlsIdentity.spkiSha256Hex,
    }),
  );

  await for (final line in stdin.transform(utf8.decoder).transform(const LineSplitter())) {
    final trimmed = line.trim();
    if (trimmed.isEmpty) continue;
    if (trimmed == 'STOP') {
      break;
    } else if (trimmed.startsWith('TRUST_DESKTOP ')) {
      final rest = trimmed.substring('TRUST_DESKTOP '.length).trim();
      final spaceIdx = rest.indexOf(' ');
      final desktopInstanceId = rest.substring(0, spaceIdx);
      final pubKey = base64.decode(rest.substring(spaceIdx + 1));
      await trustStore.upsert(
        TrustedDesktop(
          desktopInstanceId: desktopInstanceId,
          desktopPublicSigningKey: pubKey,
          pairedAtUtc: DateTime.now().toUtc().toIso8601String(),
        ),
      );
    } else if (trimmed.startsWith('CANCEL ')) {
      receiver.requestCancel(trimmed.substring('CANCEL '.length).trim());
    }
  }

  await receiver.stop();
}
