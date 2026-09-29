import 'dart:io';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/platform/share_bridge.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('app.friendsend/share');
  final bridge = ShareBridge();
  final calls = <MethodCall>[];

  void native(Future<Object?> Function(MethodCall) handler) {
    calls.clear();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (c) {
      calls.add(c);
      return handler(c);
    });
  }

  tearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, null));

  test('listTargets parses id/label/icon and ignores malformed rows', () async {
    native((c) async => [
      {'id': 'p.a/p.a.Send', 'label': 'Alpha', 'icon': Uint8List.fromList([1, 2, 3])},
      {'id': 'p.b/p.b.Send', 'label': 'Beta', 'icon': null},
      {'id': 5, 'label': 'bad'},
      {'label': 'no id'},
    ]);
    final targets = await bridge.listTargets('video/mp4');
    expect(calls.single.method, 'listShareTargets');
    expect(calls.single.arguments, {'mimeType': 'video/mp4'});
    expect(targets.map((t) => t.label), ['Alpha', 'Beta']);
    expect(targets.first.icon, isNotNull);
    expect(targets.last.icon, isNull);
  });

  test('listTargets failure is an empty list, never an exception', () async {
    native((c) async => throw PlatformException(code: 'x'));
    expect(await bridge.listTargets('video/mp4'), isEmpty);
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, null);
    expect(await bridge.listTargets('video/mp4'), isEmpty); // no platform attached
  });

  test('shareToTarget maps every bounded native result and sends the opaque target id', () async {
    const cases = {
      'TARGET_OPENED': TargetShareResult.opened,
      'TARGET_UNAVAILABLE': TargetShareResult.targetUnavailable,
      'INVALID_TEMP_FILE': TargetShareResult.invalidTempFile,
      'PLATFORM_ERROR': TargetShareResult.platformError,
      'SOMETHING_ELSE': TargetShareResult.platformError,
    };
    for (final e in cases.entries) {
      native((c) async => e.key);
      final r = await bridge.shareToTarget(path: '/cache/friendsend/x', displayName: 'holiday.mp4', mimeType: 'video/mp4', targetId: 'p.a/p.a.Send');
      expect(r, e.value, reason: e.key);
      expect(calls.single.arguments['targetId'], 'p.a/p.a.Send');
    }
  });

  test('a missing platform implementation is a bounded platform error', () async {
    expect(await bridge.shareToTarget(path: '/x', displayName: 'x', mimeType: 'video/mp4', targetId: 'a/b'), TargetShareResult.platformError);
  });

  group('manifest and native sources (privacy guard)', () {
    // Comments may legitimately mention forbidden names ("no QUERY_ALL_PACKAGES"); only code counts.
    String stripXml(String s) => s.replaceAll(RegExp(r'<!--.*?-->', dotAll: true), '');
    String stripKotlin(String s) => s.replaceAll(RegExp(r'/\*.*?\*/', dotAll: true), '').replaceAll(RegExp(r'//[^\n]*'), '');
    final manifest = stripXml(File('android/app/src/main/AndroidManifest.xml').readAsStringSync());
    final kotlin = stripKotlin(File('android/app/src/main/kotlin/app/friendsend/friendsend/MainActivity.kt').readAsStringSync()) +
        stripKotlin(File('android/app/src/main/kotlin/app/friendsend/friendsend/ShareTargets.kt').readAsStringSync());

    test('no broad package-visibility or contacts permission is declared or used', () {
      for (final forbidden in ['QUERY_ALL_PACKAGES', 'READ_CONTACTS', 'GET_ACCOUNTS', 'READ_CALL_LOG', 'READ_SMS']) {
        expect(manifest.contains(forbidden), isFalse, reason: forbidden);
        expect(kotlin.contains(forbidden), isFalse, reason: forbidden);
      }
      expect(kotlin.contains('ContactsContract'), isFalse);
    });

    test('package visibility is limited to ACTION_SEND for video/audio (plus the Flutter text-processing query)', () {
      final queries = manifest.substring(manifest.indexOf('<queries>'), manifest.indexOf('</queries>'));
      expect(queries.contains('android.intent.action.SEND'), isTrue);
      expect(queries.contains('android:mimeType="video/*"'), isTrue);
      expect(queries.contains('android:mimeType="audio/*"'), isTrue);
      expect(RegExp('<package ').hasMatch(queries), isFalse, reason: 'no per-package visibility entries');
    });

    test('targeted send goes through content:// with a read grant and validates the component', () {
      expect(kotlin.contains('FLAG_GRANT_READ_URI_PERMISSION'), isTrue);
      expect(kotlin.contains('setComponent'), isTrue);
      expect(kotlin.contains('parseComponentId'), isTrue);
      expect(kotlin.contains('file://'), isFalse);
    });
  });
}
