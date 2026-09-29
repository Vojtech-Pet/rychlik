import 'dart:io';

/// App-private temporary spool for incoming FriendSend payloads (Prompt
/// A14 §30-33, §63-67).
///
/// FriendSend is a temporary bridge, never a permanent Inbox/gallery/media
/// library (§31/§68/§140): every entry carries a bounded TTL and is
/// removed on cleanup, on failure, or on startup if it outlived the app.
/// The incoming/declared filename is NEVER used to build a path -- entries
/// are named from `handoff_id` alone (§32/§126), so a malicious
/// `../../evil.mp4` cannot escape this directory.
class TempCacheEntry {
  TempCacheEntry({required this.handoffId, required this.file, required this.createdAt});

  final String handoffId;
  final File file;
  final DateTime createdAt;
}

/// Injectable clock so TTL tests never need to sleep a real hour (§67).
typedef Clock = DateTime Function();

DateTime _systemNow() => DateTime.now().toUtc();

class TempCache {
  TempCache(this.root, {Duration ttl = const Duration(hours: 1), Clock clock = _systemNow})
    : _ttl = ttl,
      _clock = clock;

  final Directory root;
  final Duration _ttl;
  final Clock _clock;
  final Map<String, DateTime> _createdAt = {};

  Duration get ttl => _ttl;

  /// A private, per-handoff spool path. Callers must never join user/
  /// network-declared filename segments into this path (§32).
  Future<File> allocate(String handoffId) async {
    await root.create(recursive: true);
    final safeName = _safeSegment(handoffId);
    final file = File('${root.path}/incoming-$safeName.bin');
    _createdAt[handoffId] = _clock();
    return file;
  }

  Future<void> discard(String handoffId, File file) async {
    _createdAt.remove(handoffId);
    if (await file.exists()) {
      await file.delete();
    }
  }

  /// Marks an entry as successfully received (so it becomes eligible for
  /// TTL-based cleanup, not immediate deletion, §62/§130).
  void markReceived(String handoffId) {
    _createdAt[handoffId] = _clock();
  }

  /// Removes entries older than [ttl]. Safe to call from a lightweight
  /// periodic check (§66) or once at startup (§65/§129) -- never spawns a
  /// thread per file.
  Future<List<String>> cleanupExpired() async {
    final now = _clock();
    final expiredIds = <String>[];
    for (final entry in _createdAt.entries.toList()) {
      if (now.difference(entry.value) >= _ttl) {
        expiredIds.add(entry.key);
      }
    }
    for (final handoffId in expiredIds) {
      final safeName = _safeSegment(handoffId);
      final file = File('${root.path}/incoming-$safeName.bin');
      if (await file.exists()) {
        await file.delete();
      }
      _createdAt.remove(handoffId);
    }
    return expiredIds;
  }

  /// Startup sweep: any physical file under [root] not currently tracked
  /// in memory (left behind by a killed process, §138) is removed, since
  /// a restarted app has no safe way to know it was ever verified.
  Future<void> sweepUntrackedOnStartup() async {
    if (!await root.exists()) return;
    // Called once, before anything has been tracked in this process's
    // in-memory map, so every physical file found here is a leftover from
    // a previous process (crash, kill, or normal exit) with no way to
    // know it was ever verified -- delete unconditionally (§138).
    await for (final entity in root.list()) {
      if (entity is File) {
        await entity.delete();
      }
    }
  }

  static String _safeSegment(String handoffId) {
    final buffer = StringBuffer();
    for (final codeUnit in handoffId.codeUnits) {
      final ch = String.fromCharCode(codeUnit);
      if (RegExp(r'[A-Za-z0-9_-]').hasMatch(ch)) {
        buffer.write(ch);
      } else {
        buffer.write('_');
      }
    }
    final sanitized = buffer.toString();
    return sanitized.isEmpty ? 'unknown' : sanitized;
  }
}
