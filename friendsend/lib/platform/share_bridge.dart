import 'package:flutter/services.dart';

/// Thin Flutter<->Kotlin bridge (Prompt A14 §51/§131-135).
///
/// Deliberately tiny: one method, one bounded result enum. Kotlin owns all
/// Android-specific `FileProvider`/`Intent` work; this class never exposes
/// a generic method-channel surface, and the raw Android exception object
/// never reaches Dart/UI code (§134).
enum ShareResult { opened, noShareTarget, invalidTempFile, platformError }

class ShareBridge {
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
}
