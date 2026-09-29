import 'package:flutter/services.dart';

import 'share_targets.dart';

/// Thin Flutter<->Kotlin bridge (Prompt A14 §51/§131-135).
///
/// Deliberately tiny: one method, one bounded result enum. Kotlin owns all
/// Android-specific `FileProvider`/`Intent` work; this class never exposes
/// a generic method-channel surface, and the raw Android exception object
/// never reaches Dart/UI code (§134).
enum ShareResult { opened, noShareTarget, invalidTempFile, platformError }

/// Result of a targeted ACTION_SEND. `opened` means only that Android accepted the launch.
enum TargetShareResult { opened, targetUnavailable, invalidTempFile, platformError }

/// What the incoming-share screen needs from the platform (the production ShareBridge, or a test double).
abstract class TextSharer {
  Future<TargetShareResult> shareTextToTarget({required String text, required String targetId});

  Future<ShareResult> shareText(String text);

  /// A downloaded video handed to a chosen app / the Android Sharesheet (same private-cache guard as received files).
  Future<TargetShareResult> shareFileToTarget({required String path, required String displayName, required String mimeType, required String targetId});

  Future<ShareResult> shareFileViaSheet({required String path, required String displayName, required String mimeType});
}

class ShareBridge implements TextSharer {
  ShareBridge({MethodChannel? channel}) : _channel = channel ?? const MethodChannel('app.friendsend/share');

  final MethodChannel _channel;

  Future<ShareResult> shareFile({
    required String path,
    required String displayName,
    required String mimeType,
  }) async {
    try {
      final result = await _channel.invokeMethod<String>('shareFile', {
        'path': path,
        'displayName': displayName,
        'mimeType': mimeType,
      });
      switch (result) {
        case 'SHARE_SHEET_OPENED':
          return ShareResult.opened;
        case 'NO_SHARE_TARGET':
          return ShareResult.noShareTarget;
        case 'INVALID_TEMP_FILE':
          return ShareResult.invalidTempFile;
        default:
          return ShareResult.platformError;
      }
    } on MissingPluginException {
      // No Android platform implementation attached (host harness /
      // widget tests without a real device/emulator) -- a bounded,
      // honest failure, never a crash.
      return ShareResult.platformError;
    } on PlatformException {
      return ShareResult.platformError;
    }
  }

  /// Real installed apps that can take this MIME type (resolved natively; icons are the apps' own).
  /// Any failure yields an empty list -- the picker then still offers the system Sharesheet row.
  Future<List<ShareTarget>> listTargets(String mimeType) async {
    try {
      final raw = await _channel.invokeListMethod<Map<Object?, Object?>>('listShareTargets', {'mimeType': mimeType});
      if (raw == null) return const [];
      return [
        for (final m in raw)
          if (m['id'] is String && m['label'] is String)
            ShareTarget(id: m['id']! as String, label: m['label']! as String, icon: m['icon'] is Uint8List ? m['icon']! as Uint8List : null),
      ];
    } on MissingPluginException {
      return const [];
    } on PlatformException {
      return const [];
    }
  }

  /// Targeted ACTION_SEND of plain text (a shared link) to one app chosen from the picker.
  @override
  Future<TargetShareResult> shareTextToTarget({required String text, required String targetId}) async {
    try {
      final result = await _channel.invokeMethod<String>('shareTextToTarget', {'text': text, 'targetId': targetId});
      switch (result) {
        case 'TARGET_OPENED':
          return TargetShareResult.opened;
        case 'TARGET_UNAVAILABLE':
          return TargetShareResult.targetUnavailable;
        default:
          return TargetShareResult.platformError;
      }
    } on MissingPluginException {
      return TargetShareResult.platformError;
    } on PlatformException {
      return TargetShareResult.platformError;
    }
  }

  /// The Android Sharesheet for the text (FriendSend itself excluded natively).
  @override
  Future<TargetShareResult> shareFileToTarget({required String path, required String displayName, required String mimeType, required String targetId}) =>
      shareToTarget(path: path, displayName: displayName, mimeType: mimeType, targetId: targetId);

  @override
  Future<ShareResult> shareFileViaSheet({required String path, required String displayName, required String mimeType}) =>
      shareFile(path: path, displayName: displayName, mimeType: mimeType);

  @override
  Future<ShareResult> shareText(String text) async {
    try {
      final result = await _channel.invokeMethod<String>('shareText', {'text': text});
      switch (result) {
        case 'SHARE_SHEET_OPENED':
          return ShareResult.opened;
        case 'NO_SHARE_TARGET':
          return ShareResult.noShareTarget;
        default:
          return ShareResult.platformError;
      }
    } on MissingPluginException {
      return ShareResult.platformError;
    } on PlatformException {
      return ShareResult.platformError;
    }
  }

  Future<TargetShareResult> shareToTarget({
    required String path,
    required String displayName,
    required String mimeType,
    required String targetId,
  }) async {
    try {
      final result = await _channel.invokeMethod<String>('shareToTarget', {
        'path': path,
        'displayName': displayName,
        'mimeType': mimeType,
        'targetId': targetId,
      });
      switch (result) {
        case 'TARGET_OPENED':
          return TargetShareResult.opened;
        case 'TARGET_UNAVAILABLE':
          return TargetShareResult.targetUnavailable;
        case 'INVALID_TEMP_FILE':
          return TargetShareResult.invalidTempFile;
        default:
          return TargetShareResult.platformError;
      }
    } on MissingPluginException {
      return TargetShareResult.platformError;
    } on PlatformException {
      return TargetShareResult.platformError;
    }
  }
}

/// Production provider: asks the platform bridge.
class PlatformShareTargetProvider implements ShareTargetProvider {
  PlatformShareTargetProvider(this._bridge);

  final ShareBridge _bridge;

  @override
  Future<List<ShareTarget>> targetsFor(String mimeType) => _bridge.listTargets(mimeType);
}
