// Host-side test harness for the real FriendSend Dart receiver core
// (Prompt A14 §96-97).
//
// This is NOT a separate test-only server implementation -- it constructs
// and drives the exact same `FriendSendReceiverServer` / `TempCache` /
// `PairingManager` classes the Flutter app itself uses (see lib/receiver,
// lib/pairing). It exists purely so the real cross-language E2E test
// (desktop Python -> real socket -> this Dart receiver core) can run
// deterministically in CI without an Android emulator. It proves the
// protocol/streaming/integrity logic; it does NOT prove FileProvider,
// ACTION_SEND, or the Android Sharesheet, which need a real Android
// environment (see docs/FRIENDSEND_ANDROID_MVP_RESULT.md).
//
// Wire protocol (stdout/stdin, line-delimited JSON):
//   stdout, once at startup:
//     {"host": "127.0.0.1", "port": 54321, "device_id": "..."}
//   stdout, one line per receiver event:
//     {"event": "progress"|"received"|"failed"|"cancelled", "handoff_id": ...,
//      "bytes_received": ..., "total_bytes": ..., "error_code": ...|null,
//      "sha256": ...|null, "file_path": ...|null}
//   stdin commands (one per line):
//     ACCEPT_TOKEN <token>
//     CANCEL <handoff_id>
//     STOP
import 'dart:convert';
import 'dart:io';

import 'package:args/args.dart';
import 'package:friendsend/receiver/receiver_server.dart';
import 'package:friendsend/receiver/temp_cache.dart';

Future<void> main(List<String> arguments) async {
  final parser = ArgParser()
    ..addOption('device-id', defaultsTo: 'harness-device')
    ..addOption('display-name', defaultsTo: 'Harness FriendSend Device')
    ..addOption('cache-dir', mandatory: true)
    ..addOption('max-payload-bytes')
    ..addOption('mime-types')
    ..addMultiOption('seed-token')
    ..addFlag('force-capability-rejection', defaultsTo: false)
    ..addFlag('force-receiver-rejection', defaultsTo: false);

  final args = parser.parse(arguments);

  final maxPayload = args['max-payload-bytes'] as String?;
  final mimeTypesRaw = args['mime-types'] as String?;

  final tempCache = TempCache(Directory(args['cache-dir'] as String));
  final receiver = FriendSendReceiverServer(
    config: ReceiverConfig(
      deviceId: args['device-id'] as String,
      displayName: args['display-name'] as String,
      maxPayloadBytes: maxPayload == null ? null : int.parse(maxPayload),
      supportedMimeTypes: mimeTypesRaw?.split(',').toSet(),
    ),
    tempCache: tempCache,
  );
  receiver.forceCapabilityRejection = args['force-capability-rejection'] as bool;
  receiver.forceReceiverRejection = args['force-receiver-rejection'] as bool;

  for (final token in args['seed-token'] as List<String>) {
    receiver.acceptToken(token);
  }

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
    jsonEncode({'host': receiver.host, 'port': receiver.port, 'device_id': receiver.config.deviceId}),
  );

  await for (final line in stdin.transform(utf8.decoder).transform(const LineSplitter())) {
    final trimmed = line.trim();
    if (trimmed.isEmpty) continue;
    if (trimmed == 'STOP') {
      break;
    } else if (trimmed.startsWith('ACCEPT_TOKEN ')) {
      receiver.acceptToken(trimmed.substring('ACCEPT_TOKEN '.length).trim());
    } else if (trimmed.startsWith('CANCEL ')) {
      receiver.requestCancel(trimmed.substring('CANCEL '.length).trim());
    }
  }

  await receiver.stop();
}
