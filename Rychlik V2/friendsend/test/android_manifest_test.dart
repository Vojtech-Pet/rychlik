import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// Static assertions over the manifest/FileProvider *source* (Prompt A14
/// §100-102) -- the actual built APK was independently verified once with
/// `aapt dump permissions` / `aapt dump xmltree` (see
/// docs/FRIENDSEND_ANDROID_MVP_RESULT.md), this test guards against a
/// future regression reintroducing a dangerous permission or a broad
/// FileProvider path without needing a full Android build in CI.
void main() {
  final manifest = File('android/app/src/main/AndroidManifest.xml').readAsStringSync();
  final filePaths = File('android/app/src/main/res/xml/file_paths.xml').readAsStringSync();

  test('manifest requests INTERNET and nothing storage/contacts/phone/location-related', () {
    expect(manifest.contains('android.permission.INTERNET'), isTrue);
    for (final forbidden in [
      'READ_EXTERNAL_STORAGE',
      'WRITE_EXTERNAL_STORAGE',
      'MANAGE_EXTERNAL_STORAGE',
      'READ_PHONE_STATE',
      'READ_CONTACTS',
      'ACCESS_FINE_LOCATION',
      'ACCESS_COARSE_LOCATION',
    ]) {
      expect(manifest.contains(forbidden), isFalse, reason: 'forbidden permission: $forbidden');
    }
  });

  test('FileProvider is declared with exported=false and grantUriPermissions=true', () {
    final providerBlock = manifest.substring(
      manifest.indexOf('<provider'),
      manifest.indexOf('</provider>') + '</provider>'.length,
    );
    expect(providerBlock.contains('androidx.core.content.FileProvider'), isTrue);
    expect(providerBlock.contains('android:exported="false"'), isTrue);
    expect(providerBlock.contains('android:grantUriPermissions="true"'), isTrue);
  });

  test('file_paths.xml exposes only the narrow FriendSend cache subtree', () {
    expect(filePaths.contains('path="friendsend/"'), isTrue);
    // Never the bare cache root or external storage.
    expect(filePaths.contains('path="."'), isFalse);
    expect(filePaths.contains('path=""'), isFalse);
    expect(filePaths.contains('external-path'), isFalse);
  });
}
