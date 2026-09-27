import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/pairing/pairing_manager.dart';
import 'package:friendsend/protocol/models.dart';
import 'package:friendsend/receiver/receiver_server.dart';
import 'package:friendsend/receiver/temp_cache.dart';

Map<String, dynamic> _fixture() {
  final file = File('../protocol/fixtures/v1/pairing_payload.json');
  return jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
}

void main() {
  late Directory root;
  late FriendSendReceiverServer receiver;
  late PairingManager manager;

  setUp(() {
    root = Directory.systemTemp.createTempSync('friendsend_pairing_test_');
    receiver = FriendSendReceiverServer(
      config: ReceiverConfig(deviceId: 'd1', displayName: 'Test Device'),
      tempCache: TempCache(root),
    );
    manager = PairingManager(receiver: receiver);
  });

  tearDown(() {
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  test('parses a valid pairing payload', () {
    final payload = manager.parse(jsonEncode(_fixture()));
    expect(payload.pairingSessionId, isNotEmpty);
  });

  test('malformed JSON raises ProtocolFormatException, never a raw crash', () {
    expect(() => manager.parse('not json'), throwsA(isA<ProtocolFormatException>()));
  });

  test('malformed payload (missing endpoint) raises ProtocolFormatException', () {
    final json = Map<String, dynamic>.from(_fixture())..remove('endpoint');
    expect(() => manager.parse(jsonEncode(json)), throwsA(isA<ProtocolFormatException>()));
  });

  test('completing pairing with a valid, unexpired payload mints a fresh accepted token', () {
    final payload = manager.parse(jsonEncode(_fixture()));
    final token = manager.completePairing(payload);
    expect(token, isNotEmpty);
    expect(token, isNot(payload.secret));
  });

  test('expired payload raises PairingExpiredException, no retry with stale secret', () {
    final json = Map<String, dynamic>.from(_fixture());
    json['expires_at_utc'] = DateTime.now().toUtc().subtract(const Duration(minutes: 1)).toIso8601String();
    final payload = manager.parse(jsonEncode(json));
    expect(() => manager.completePairing(payload), throwsA(isA<PairingExpiredException>()));
  });

  test('protocol version mismatch raises PairingUnsupportedProtocolException', () {
    final json = Map<String, dynamic>.from(_fixture());
    json['protocol_version'] = 99;
    final payload = manager.parse(jsonEncode(json));
    expect(() => manager.completePairing(payload), throwsA(isA<PairingUnsupportedProtocolException>()));
  });

  test('PairingPayload never persists/logs the raw secret via redactedDescription', () {
    final payload = manager.parse(jsonEncode(_fixture()));
    expect(payload.redactedDescription.contains(payload.secret), isFalse);
  });
}
