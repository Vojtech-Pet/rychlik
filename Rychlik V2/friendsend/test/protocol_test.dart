import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/protocol/models.dart';
import 'package:friendsend/protocol/protocol.dart';

Map<String, dynamic> _loadFixture(String name) {
  final file = File('../protocol/fixtures/v1/$name');
  return jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
}

void main() {
  group('protocol constants', () {
    test('version is 1', () {
      expect(friendSendProtocolVersion, 1);
    });

    test('capability wire names are generic, never a per-app whitelist', () {
      final names = DeviceCapability.values.map((c) => c.wireName).toSet();
      expect(names, {'RECEIVE_STREAM', 'TEMPORARY_FILE_HANDOFF', 'SHARE_TO_OS'});
      for (final forbidden in ['WHATSAPP', 'MESSENGER', 'TELEGRAM']) {
        expect(names.contains(forbidden), isFalse);
      }
    });
  });

  group('HandoffOffer.fromJson (shared fixture: handoff_offer.json)', () {
    test('parses the shared platform-neutral fixture', () {
      final json = _loadFixture('handoff_offer.json');
      final offer = HandoffOffer.fromJson(json);
      expect(offer.handoffId, isNotEmpty);
      expect(offer.sha256.length, 64);
    });

    test('rejects a negative size_bytes', () {
      expect(
        () => HandoffOffer(
          handoffId: 'h1',
          displayName: 'x',
          mimeType: 'video/mp4',
          sizeBytes: -1,
          sha256: 'a' * 64,
        ),
        throwsA(isA<ProtocolFormatException>()),
      );
    });

    test('rejects a malformed sha256', () {
      expect(
        () => HandoffOffer(
          handoffId: 'h1',
          displayName: 'x',
          mimeType: 'video/mp4',
          sizeBytes: 10,
          sha256: 'not-hex',
        ),
        throwsA(isA<ProtocolFormatException>()),
      );
    });

    test('missing required field raises ProtocolFormatException, not a raw parser crash', () {
      expect(
        () => HandoffOffer.fromJson({'display_name': 'x'}),
        throwsA(isA<ProtocolFormatException>()),
      );
    });

    test('unknown optional field is ignored', () {
      final json = _loadFixture('handoff_offer.json');
      json['some_future_field'] = 'ignored';
      expect(() => HandoffOffer.fromJson(json), returnsNormally);
    });
  });

  group('PairingPayload.fromJson (shared fixture: pairing_payload.json)', () {
    test('parses the shared fixture', () {
      final json = _loadFixture('pairing_payload.json');
      final payload = PairingPayload.fromJson(json);
      expect(payload.protocolVersion, 1);
      expect(payload.pairingSessionId, isNotEmpty);
      expect(payload.endpointPort, greaterThan(0));
    });

    test('redactedDescription never contains the raw secret', () {
      final json = _loadFixture('pairing_payload.json');
      final payload = PairingPayload.fromJson(json);
      expect(payload.redactedDescription.contains(payload.secret), isFalse);
    });

    test('expired payload reports isExpired true', () {
      final json = Map<String, dynamic>.from(_loadFixture('pairing_payload.json'));
      json['expires_at_utc'] = DateTime.now().toUtc().subtract(const Duration(days: 1)).toIso8601String();
      final payload = PairingPayload.fromJson(json);
      expect(payload.isExpired, isTrue);
    });

    test('malformed endpoint raises ProtocolFormatException', () {
      final json = Map<String, dynamic>.from(_loadFixture('pairing_payload.json'));
      json.remove('endpoint');
      expect(() => PairingPayload.fromJson(json), throwsA(isA<ProtocolFormatException>()));
    });
  });
}
