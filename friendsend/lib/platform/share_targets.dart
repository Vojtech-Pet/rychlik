import 'dart:typed_data';

/// One app that can receive the verified file via a targeted ACTION_SEND (resolved natively in I8; the
/// picker UI depends only on this model).
class ShareTarget {
  const ShareTarget({required this.id, required this.label, this.icon});

  /// Opaque native identifier (component key) passed back to the platform bridge.
  final String id;
  final String label;

  /// Real launcher icon (PNG bytes) supplied by the platform; null shows a neutral placeholder tile.
  final Uint8List? icon;
}

abstract class ShareTargetProvider {
  Future<List<ShareTarget>> targetsFor(String mimeType);
}

class NoShareTargets implements ShareTargetProvider {
  const NoShareTargets();

  @override
  Future<List<ShareTarget>> targetsFor(String mimeType) async => const [];
}
