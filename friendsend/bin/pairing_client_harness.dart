// Host-side test harness driving the REAL FriendSend pairing client
// (Prompt A15) -- lib/security/secure_pairing_manager.dart, the exact
// class the Flutter app's pairing screen uses. One-shot: reads a single
// line of pairing-bootstrap-payload JSON from stdin, completes pairing
// against the desktop's real bootstrap listener, and prints the result.
//
// This is what proves "real Python <-> Dart pairing E2E" (Prompt A15
// §153) rather than a fixture standing in for either side.
import 'dart:convert';
import 'dart:io';

import 'package:args/args.dart';
import 'package:friendsend/security/desktop_trust_store.dart';
import 'package:friendsend/security/secure_pairing_manager.dart';
import 'package:friendsend/security/tls_identity.dart';

Future<void> main(List<String> arguments) async {
  final parser = ArgParser()
    ..addOption('device-id', mandatory: true)
    ..addOption('display-name', defaultsTo: 'Pairing Harness Device')
    ..addOption('identity-dir', mandatory: true)
    ..addOption('friendsend-host', mandatory: true)
    ..addOption('friendsend-port', mandatory: true);

  final args = parser.parse(arguments);
  final identityDir = Directory(args['identity-dir'] as String);
  final tlsIdentity = await TlsIdentityStore(identityDir).loadOrCreate();
  final trustStore = DesktopTrustStore(identityDir);

  final manager = SecurePairingManager(
    deviceId: args['device-id'] as String,
    displayName: args['display-name'] as String,
    friendSendTlsSpkiSha256: tlsIdentity.spkiSha256,
    friendSendEndpointHost: args['friendsend-host'] as String,
    friendSendEndpointPort: int.parse(args['friendsend-port'] as String),
    trustStore: trustStore,
  );

  final line = stdin.readLineSync();
  if (line == null || line.trim().isEmpty) {
    stdout.writeln(jsonEncode({'pairing_result': 'error', 'message': 'no payload on stdin'}));
    return;
  }

  try {
    final payload = manager.parse(line);
    await manager.completePairing(payload);
    stdout.writeln(jsonEncode({'pairing_result': 'ok'}));
  } catch (e) {
    stdout.writeln(jsonEncode({'pairing_result': 'error', 'message': e.toString()}));
  }
}
