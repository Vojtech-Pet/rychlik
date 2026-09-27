import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/receiver/temp_cache.dart';

void main() {
  late Directory root;

  setUp(() {
    root = Directory.systemTemp.createTempSync('friendsend_tempcache_test_');
  });

  tearDown(() {
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  test('allocate() never uses the declared filename to build a path', () async {
    final cache = TempCache(root);
    final maliciousId = '../../evil';
    final file = await cache.allocate(maliciousId);
    expect(file.path.startsWith(root.path), isTrue);
    expect(file.parent.path, root.path);
  });

  test('filename traversal cannot escape the cache root', () async {
    final cache = TempCache(root);
    for (final malicious in ['../../evil.mp4', '/sdcard/foo', '..\\..\\foo', '../../../etc/passwd']) {
      final file = await cache.allocate(malicious);
      final resolved = File(file.path).absolute.path;
      expect(resolved.startsWith(root.absolute.path), isTrue, reason: 'escaped for $malicious');
    }
  });

  test('discard deletes the file', () async {
    final cache = TempCache(root);
    final file = await cache.allocate('h1');
    await file.writeAsString('data');
    expect(await file.exists(), isTrue);
    await cache.discard('h1', file);
    expect(await file.exists(), isFalse);
  });

  test('cleanupExpired removes entries older than TTL using an injected clock', () async {
    var now = DateTime.utc(2026, 1, 1, 0, 0, 0);
    final cache = TempCache(root, ttl: const Duration(hours: 1), clock: () => now);
    final file = await cache.allocate('h1');
    await file.writeAsString('data');
    cache.markReceived('h1');

    // Still within TTL.
    now = now.add(const Duration(minutes: 30));
    var expired = await cache.cleanupExpired();
    expect(expired, isEmpty);
    expect(await file.exists(), isTrue);

    // Past TTL -- no real waiting required (§67).
    now = now.add(const Duration(hours: 1));
    expired = await cache.cleanupExpired();
    expect(expired, ['h1']);
    expect(await file.exists(), isFalse);
  });

  test('sweepUntrackedOnStartup removes leftover files from a killed prior process', () async {
    await root.create(recursive: true);
    final leftover = File('${root.path}/incoming-leftover.bin');
    await leftover.writeAsString('stale');

    final cache = TempCache(root);
    await cache.sweepUntrackedOnStartup();

    expect(await leftover.exists(), isFalse);
  });
}
