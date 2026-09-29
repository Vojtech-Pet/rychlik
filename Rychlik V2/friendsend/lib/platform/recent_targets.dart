import 'dart:convert';
import 'dart:io';

/// The apps the user most recently sent shared text to, newest first. Used only to put them at the front of the
/// picker, so the usual one or two taps are always in the same place.
abstract class RecentTargetsStore {
  Future<List<String>> load();

  Future<void> record(String targetId);
}

class NoRecentTargets implements RecentTargetsStore {
  const NoRecentTargets();

  @override
  Future<List<String>> load() async => const [];

  @override
  Future<void> record(String targetId) async {}
}

class FileRecentTargets implements RecentTargetsStore {
  FileRecentTargets(Directory dir, {this.max = 6}) : _file = File('${dir.path}/recent_share_targets.json');

  final File _file;
  final int max;

  @override
  Future<List<String>> load() async {
    try {
      final data = jsonDecode(await _file.readAsString());
      if (data is List) return [for (final e in data) if (e is String && e.isNotEmpty) e];
    } catch (_) {}
    return const [];
  }

  @override
  Future<void> record(String targetId) async {
    final current = await load();
    final next = [targetId, ...current.where((e) => e != targetId)].take(max).toList();
    try {
      await _file.writeAsString(jsonEncode(next));
    } catch (_) {} // best effort: ordering is a convenience only
  }
}

/// Recent targets first (only those still installed), the rest in the platform's own order.
List<T> orderByRecents<T>(List<T> targets, List<String> recentIds, String Function(T) idOf) {
  final byId = {for (final t in targets) idOf(t): t};
  final front = [for (final id in recentIds) if (byId.containsKey(id)) byId[id]!];
  final frontIds = front.map(idOf).toSet();
  return [...front, ...targets.where((t) => !frontIds.contains(idOf(t)))];
}
