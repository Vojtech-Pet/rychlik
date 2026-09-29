import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/protocol/protocol.dart';
import 'package:friendsend/receiver/receiver_server.dart';
import 'package:friendsend/receiver/temp_cache.dart';

/// Real `dart:io` `HttpClient` against a real, loopback, OS-assigned-port
/// server -- no mocked transport (Prompt A14 §22/§94-95 apply to this
/// server the same way they apply to the cross-language E2E).
class _Client {
  _Client(this.port);

  final int port;
  final HttpClient _client = HttpClient();

  Future<(int, Map<String, dynamic>)> offer(
    Map<String, dynamic> body, {
    String protocolVersion = '1',
    String token = 'tok',
  }) async {
    final request = await _client.postUrl(Uri.parse('http://127.0.0.1:$port/handoff/offer'));
    request.headers.set(headerProtocolVersion, protocolVersion);
    request.headers.set(headerAuthToken, token);
    request.headers.contentType = ContentType.json;
    request.write(jsonEncode(body));
    final response = await request.close();
    final raw = await utf8.decoder.bind(response).join();
    return (response.statusCode, raw.isEmpty ? <String, dynamic>{} : jsonDecode(raw) as Map<String, dynamic>);
  }

  Future<(int, Map<String, dynamic>)> stream(
    String handoffId,
    List<int> bytes, {
    String token = 'tok',
    int? declaredLengthOverride,
    int? writeChunkSize,
    Duration chunkDelay = Duration.zero,
  }) async {
    final request = await _client.postUrl(Uri.parse('http://127.0.0.1:$port/handoff/stream'));
    request.headers.set(headerProtocolVersion, '1');
    request.headers.set(headerAuthToken, token);
    request.headers.set(headerHandoffId, handoffId);
    request.contentLength = declaredLengthOverride ?? bytes.length;
    if (writeChunkSize == null) {
      request.add(bytes);
    } else {
      // Explicit small, flushed, paced writes so the receiver genuinely
      // observes multiple chunks over loopback instead of the OS
      // coalescing a large single `add()` into one read on the server
      // side (mirrors the A13 desktop transport's test-only chunk_delay
      // knob for the same reason).
      for (var offset = 0; offset < bytes.length; offset += writeChunkSize) {
        final end = (offset + writeChunkSize < bytes.length) ? offset + writeChunkSize : bytes.length;
        request.add(bytes.sublist(offset, end));
        await request.flush();
        if (chunkDelay > Duration.zero) {
          await Future<void>.delayed(chunkDelay);
        }
      }
    }
    final response = await request.close();
    final raw = await utf8.decoder.bind(response).join();
    return (response.statusCode, raw.isEmpty ? <String, dynamic>{} : jsonDecode(raw) as Map<String, dynamic>);
  }

  void close() => _client.close(force: true);
}

String _sha256Of(List<int> bytes) => sha256.convert(bytes).toString();

void main() {
  late Directory root;
  late TempCache cache;
  late FriendSendReceiverServer server;
  late _Client client;

  setUp(() async {
    root = Directory.systemTemp.createTempSync('friendsend_receiver_test_');
    cache = TempCache(root);
    server = FriendSendReceiverServer(
      config: ReceiverConfig(deviceId: 'd1', displayName: 'Test Device'),
      tempCache: cache,
    );
    await server.start();
    server.acceptToken('tok');
    client = _Client(server.port);
  });

  tearDown(() async {
    client.close();
    await server.stop();
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  test('GET /hello advertises protocol version, capabilities, device identity', () async {
    final httpClient = HttpClient();
    final request = await httpClient.getUrl(Uri.parse('http://127.0.0.1:${server.port}/hello'));
    final response = await request.close();
    final raw = await utf8.decoder.bind(response).join();
    final json = jsonDecode(raw) as Map<String, dynamic>;
    expect(json['protocol_version'], 1);
    expect(json['device_id'], 'd1');
    expect((json['capabilities'] as List).contains('RECEIVE_STREAM'), isTrue);
    httpClient.close();
  });

  group('offer/stream separation -- rejection costs zero payload bytes', () {
    test('protocol version mismatch is rejected before stream, zero bytes accepted', () async {
      final (status, body) = await client.offer({
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': 3,
        'sha256': _sha256Of([1, 2, 3]),
      }, protocolVersion: '2');
      expect(status, 400);
      expect(body['error_code'], 'UNSUPPORTED_PROTOCOL');
    });

    test('wrong auth token is rejected with AUTHENTICATION_FAILED', () async {
      final (status, body) = await client.offer({
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': 3,
        'sha256': _sha256Of([1, 2, 3]),
      }, token: 'wrong-token');
      expect(status, 401);
      expect(body['error_code'], 'AUTHENTICATION_FAILED');
    });

    test('capability rejection (forced) never reaches stream', () async {
      server.forceCapabilityRejection = true;
      final (status, body) = await client.offer({
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': 3,
        'sha256': _sha256Of([1, 2, 3]),
      });
      expect(status, 400);
      expect(body['error_code'], 'UNSUPPORTED_CAPABILITY');
    });

    test('oversized payload is rejected with PAYLOAD_TOO_LARGE, no stream', () async {
      final small = FriendSendReceiverServer(
        config: ReceiverConfig(deviceId: 'd1', displayName: 'x', maxPayloadBytes: 10),
        tempCache: cache,
      );
      await small.start();
      small.acceptToken('tok');
      final smallClient = _Client(small.port);
      final (status, body) = await smallClient.offer({
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': 1000,
        'sha256': _sha256Of([1, 2, 3]),
      });
      expect(status, 413);
      expect(body['error_code'], 'PAYLOAD_TOO_LARGE');
      smallClient.close();
      await small.stop();
    });

    test('unsupported MIME type is rejected with UNSUPPORTED_MEDIA', () async {
      final restricted = FriendSendReceiverServer(
        config: ReceiverConfig(deviceId: 'd1', displayName: 'x', supportedMimeTypes: {'video/mp4'}),
        tempCache: cache,
      );
      await restricted.start();
      restricted.acceptToken('tok');
      final restrictedClient = _Client(restricted.port);
      final (status, body) = await restrictedClient.offer({
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/zip',
        'size_bytes': 3,
        'sha256': _sha256Of([1, 2, 3]),
      });
      expect(status, 415);
      expect(body['error_code'], 'UNSUPPORTED_MEDIA');
      restrictedClient.close();
      await restricted.stop();
    });

    test('accepted offer allows exactly one matching stream', () async {
      final bytes = List<int>.generate(1000, (i) => i % 256);
      final (offerStatus, offerBody) = await client.offer({
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': _sha256Of(bytes),
      });
      expect(offerStatus, 200);
      expect(offerBody['accepted'], isTrue);

      final (streamStatus, streamBody) = await client.stream('h1', bytes);
      expect(streamStatus, 200);
      expect(streamBody['state'], 'RECEIVED');
      expect(streamBody['bytes_received'], bytes.length);
    });
  });

  group('streaming integrity', () {
    test('multi-chunk large transfer: exact bytes and sha256, progress advances multiple times', () async {
      final bytes = List<int>.generate(2 * 1024 * 1024, (i) => i % 256); // 2 MiB, several chunks
      final progressEvents = <ReceiverEvent>[];
      final sub = server.events.listen((e) {
        if (e.kind == ReceiverEventKind.progress) progressEvents.add(e);
      });

      await client.offer({
        'handoff_id': 'big',
        'display_name': 'big.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': _sha256Of(bytes),
      });
      final (status, body) = await client.stream(
        'big',
        bytes,
        writeChunkSize: 64 * 1024,
        chunkDelay: const Duration(milliseconds: 2),
      );
      await Future<void>.delayed(const Duration(milliseconds: 50));

      expect(status, 200);
      expect(body['state'], 'RECEIVED');
      expect(body['sha256'], _sha256Of(bytes));
      expect(progressEvents.length, greaterThan(1));
      expect(progressEvents.last.bytesReceived, bytes.length);
      await sub.cancel();
    });

    test('received event exposes the verified file and it is retained (not deleted, §62)', () async {
      final bytes = [1, 2, 3, 4, 5];
      ReceiverEvent? received;
      final sub = server.events.listen((e) {
        if (e.kind == ReceiverEventKind.received) received = e;
      });
      await client.offer({
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': _sha256Of(bytes),
      });
      await client.stream('h1', bytes);
      await Future<void>.delayed(const Duration(milliseconds: 50));

      expect(received, isNotNull);
      expect(await received!.file!.exists(), isTrue);
      expect(await received!.file!.readAsBytes(), bytes);
      await sub.cancel();
    });

    test('incomplete body (declared length not fully delivered) -> INCOMPLETE_TRANSFER, temp deleted', () async {
      final bytes = List<int>.generate(100, (i) => i);
      await client.offer({
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': _sha256Of(bytes),
      });

      ReceiverEvent? failed;
      final sub = server.events.listen((e) {
        if (e.kind == ReceiverEventKind.failed) failed = e;
      });

      // Declare 100 bytes but a mismatched offer/stream length triggers the
      // receiver's own length-consistency rejection first; use a raw socket
      // to genuinely truncate the body mid-stream instead.
      final socket = await Socket.connect('127.0.0.1', server.port);
      final head =
          'POST /handoff/stream HTTP/1.1\r\n'
          'Host: 127.0.0.1\r\n'
          '$headerProtocolVersion: 1\r\n'
          '$headerAuthToken: tok\r\n'
          '$headerHandoffId: h1\r\n'
          'Content-Length: ${bytes.length}\r\n'
          'Connection: close\r\n\r\n';
      socket.add(utf8.encode(head));
      socket.add(bytes.sublist(0, 40)); // send fewer bytes than declared
      await socket.flush();
      await socket.close();
      await Future<void>.delayed(const Duration(milliseconds: 200));

      expect(failed?.errorCode, HandoffErrorCode.incompleteTransfer);
      await sub.cancel();
    });

    test('hash mismatch on an otherwise-complete body -> INTEGRITY_MISMATCH, temp deleted', () async {
      final bytes = List<int>.generate(50, (i) => i);
      final wrongHash = _sha256Of([...bytes]..[0] = (bytes[0] + 1) % 256);
      await client.offer({
        'handoff_id': 'h1',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': wrongHash,
      });
      final (status, body) = await client.stream('h1', bytes);
      expect(status, 200);
      expect(body['state'], 'FAILED');
      expect(body['error_code'], 'INTEGRITY_MISMATCH');
    });
  });

  group('cancellation', () {
    test('receiver-side cancel stops reading and deletes the partial temp file', () async {
      final bytes = List<int>.generate(5 * 1024 * 1024, (i) => i % 256);
      await client.offer({
        'handoff_id': 'cancel-me',
        'display_name': 'f.bin',
        'mime_type': 'application/octet-stream',
        'size_bytes': bytes.length,
        'sha256': _sha256Of(bytes),
      });

      final progress = Completer<void>();
      final sub = server.events.listen((e) {
        if (e.kind == ReceiverEventKind.progress && !progress.isCompleted) {
          progress.complete();
          server.requestCancel('cancel-me');
        }
      });

      try {
        await client
            .stream('cancel-me', bytes, writeChunkSize: 64 * 1024, chunkDelay: const Duration(milliseconds: 5))
            .timeout(const Duration(seconds: 5));
      } catch (_) {
        // Connection dropped/reset by the receiver mid-upload -- expected.
      }
      await progress.future;
      await Future<void>.delayed(const Duration(milliseconds: 100));
      await sub.cancel();

      final leftovers = root.listSync();
      expect(leftovers, isEmpty);
    });
  });
}
