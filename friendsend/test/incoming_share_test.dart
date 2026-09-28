import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/handoff/handoff_controller.dart';
import 'package:friendsend/identity/device_identity.dart';
import 'package:friendsend/platform/incoming_share.dart';
import 'package:friendsend/platform/recent_targets.dart';
import 'package:friendsend/platform/share_bridge.dart';
import 'package:friendsend/platform/share_targets.dart';
import 'package:friendsend/receiver/receiver_interface.dart';
import 'package:friendsend/receiver/receiver_server.dart' show ReceiverEvent;
import 'package:friendsend/receiver/temp_cache.dart';
import 'package:friendsend/security/desktop_trust_store.dart';
import 'package:friendsend/security/secure_pairing_manager.dart';
import 'package:friendsend/ui/home_screen.dart';
import 'package:friendsend/ui/theme/fs_theme.dart';

const _facebook = 'https://www.facebook.com/share/v/1AbCdEf/?mibextid=abc&x=1#frag';

class _Receiver implements FriendSendReceiverLike {
  final _c = StreamController<ReceiverEvent>.broadcast();
  @override
  Stream<ReceiverEvent> get events => _c.stream;
  @override
  void requestCancel(String handoffId) {}
}

class _Source implements IncomingShareSource {
  _Source([this.initial]);
  String? initial;
  final _c = StreamController<String>.broadcast();
  @override
  Future<String?> takeInitial() async {
    final v = initial;
    initial = null;
    return v;
  }

  @override
  Stream<String> get updates => _c.stream;
  void push(String t) => _c.add(t);
}

class _Sharer implements TextSharer {
  TargetShareResult targetResult = TargetShareResult.opened;
  ShareResult sheetResult = ShareResult.opened;
  final targetCalls = <(String, String)>[];
  final sheetCalls = <String>[];
  @override
  Future<TargetShareResult> shareTextToTarget({required String text, required String targetId}) async {
    targetCalls.add((text, targetId));
    return targetResult;
  }

  @override
  Future<ShareResult> shareText(String text) async {
    sheetCalls.add(text);
    return sheetResult;
  }
}

class _Recents implements RecentTargetsStore {
  _Recents(this.ids);
  List<String> ids;
  @override
  Future<List<String>> load() async => ids;
  @override
  Future<void> record(String targetId) async => ids = [targetId, ...ids.where((e) => e != targetId)];
}

class _Targets implements ShareTargetProvider {
  final requestedMime = <String>[];
  @override
  Future<List<ShareTarget>> targetsFor(String mimeType) async {
    requestedMime.add(mimeType);
    return const [ShareTarget(id: 'com.fb.orca/.Send', label: 'Messenger'), ShareTarget(id: 'com.whatsapp/.Send', label: 'WhatsApp')];
  }
}

void main() {
  orderTests();
  group('ShareBridge text', () {
    const channel = MethodChannel('test.share');
    final calls = <MethodCall>[];

    void reply(Object? Function(MethodCall) handler) {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (call) async {
        calls.add(call);
        return handler(call);
      });
    }

    setUp(calls.clear);
    tearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, null));

    test('shareTextToTarget sends the text and component exactly and maps results', () async {
      final bridge = ShareBridge(channel: channel);
      for (final (raw, expected) in [
        ('TARGET_OPENED', TargetShareResult.opened),
        ('TARGET_UNAVAILABLE', TargetShareResult.targetUnavailable),
        ('WHATEVER', TargetShareResult.platformError),
      ]) {
        reply((_) => raw);
        expect(await bridge.shareTextToTarget(text: _facebook, targetId: 'p/c'), expected);
      }
      expect(calls.first.method, 'shareTextToTarget');
      expect(calls.first.arguments, {'text': _facebook, 'targetId': 'p/c'});
    });

    test('shareText maps results and never throws', () async {
      final bridge = ShareBridge(channel: channel);
      reply((_) => 'SHARE_SHEET_OPENED');
      expect(await bridge.shareText(_facebook), ShareResult.opened);
      reply((_) => 'NO_SHARE_TARGET');
      expect(await bridge.shareText(_facebook), ShareResult.noShareTarget);
      reply((_) => throw PlatformException(code: 'x'));
      expect(await bridge.shareText(_facebook), ShareResult.platformError);
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, null);
      expect(await bridge.shareText(_facebook), ShareResult.platformError); // no platform attached
    });
  });

  group('PlatformIncomingShares', () {
    const channel = MethodChannel('test.incoming');
    late String? pending;

    setUp(() {
      pending = null;
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (call) async {
        if (call.method == 'takeIncomingText') {
          final v = pending;
          pending = null;
          return v;
        }
        return null;
      });
    });
    tearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, null));

    test('a cold-start share is taken exactly once', () async {
      pending = _facebook;
      final source = PlatformIncomingShares(channel: channel);
      expect(await source.takeInitial(), _facebook);
      expect(await source.takeInitial(), isNull);
    });

    test('a share that arrives while running is announced, pulled once and streamed', () async {
      final source = PlatformIncomingShares(channel: channel);
      final got = <String>[];
      final sub = source.updates.listen(got.add);
      pending = _facebook;
      await TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.handlePlatformMessage(
        channel.name,
        channel.codec.encodeMethodCall(const MethodCall('incomingAvailable')),
        (_) {},
      );
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(got, [_facebook]);
      expect(pending, isNull);
      await sub.cancel();
    });
  });

  group('HomeScreen incoming share', () {
    late Directory root;
    late HandoffController c;
    late SecurePairingManager pairing;
    late _Sharer sharer;
    late _Targets targets;

    setUp(() {
      root = Directory.systemTemp.createTempSync('fs_incoming_');
      c = HandoffController(receiver: _Receiver(), tempCache: TempCache(root), shareBridge: ShareBridge());
      pairing = SecurePairingManager(
        deviceId: 'd1', displayName: 'Phone', friendSendTlsSpkiSha256: List<int>.filled(32, 1), friendSendEndpointHost: '127.0.0.1', friendSendEndpointPort: 1,
        trustStore: DesktopTrustStore(root),
      );
      sharer = _Sharer();
      targets = _Targets();
    });

    tearDown(() async {
      await c.dispose();
      if (root.existsSync()) root.deleteSync(recursive: true);
    });

    Future<void> pump(WidgetTester t, _Source source, {RecentTargetsStore? recents}) async {
      t.view.physicalSize = const Size(780, 1688);
      t.view.devicePixelRatio = 2;
      addTearDown(t.view.reset);
      await t.pumpWidget(MaterialApp(
        theme: FsTheme.build(Brightness.dark),
        home: HomeScreen(
          identity: const DeviceIdentity(deviceId: 'd1', displayName: 'Phone'), pairingManager: pairing, controller: c,
          targetProvider: targets, incomingShares: source, textSharer: sharer, recentTargets: recents ?? const NoRecentTargets(),
        ),
      ));
      await t.pump();
      await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
      await t.pump();
    }

    testWidgets('a shared link is shown untouched, without any pairing screen, even when never paired', (t) async {
      await pump(t, _Source(_facebook));
      expect(find.byKey(const Key('incoming_title')), findsOneWidget);
      expect(find.text('Share link'), findsOneWidget);
      expect(t.widget<Text>(find.byKey(const Key('incoming_text'))).data, _facebook);
      expect(find.byKey(const Key('unpaired_message')), findsNothing);
      expect(find.byKey(const Key('pairing_paste_field')), findsNothing);
    });

    testWidgets('the installed apps are right on the share screen: one tap sends the exact original text to that app only', (t) async {
      await pump(t, _Source(_facebook));
      expect(targets.requestedMime, ['text/plain']);
      expect(find.byKey(const Key('target_com.fb.orca/.Send')), findsOneWidget); // no extra "Choose app" step, no sheet
      expect(find.byKey(const Key('incoming_more_apps')), findsOneWidget); // system Sharesheet stays available
      expect(find.text('Choose app'), findsNothing);
      await t.tap(find.byKey(const Key('target_com.fb.orca/.Send')));
      await t.pumpAndSettle();
      expect(sharer.targetCalls, [(_facebook, 'com.fb.orca/.Send')]);
      expect(sharer.sheetCalls, isEmpty);
      expect(find.byKey(const Key('incoming_title')), findsNothing); // done: back to the normal screen
      expect(find.byKey(const Key('unpaired_message')), findsOneWidget);
    });

    testWidgets('More apps opens the Android Sharesheet with the same text', (t) async {
      await pump(t, _Source(_facebook));
      await t.tap(find.byKey(const Key('incoming_more_apps')));
      await t.pumpAndSettle();
      expect(sharer.sheetCalls, [_facebook]);
      expect(sharer.targetCalls, isEmpty);
    });

    testWidgets('recently used apps come first, and a successful send is remembered', (t) async {
      final recents = _Recents(['com.whatsapp/.Send']);
      await pump(t, _Source(_facebook), recents: recents);
      final whatsapp = t.getTopLeft(find.byKey(const Key('target_com.whatsapp/.Send')));
      final messenger = t.getTopLeft(find.byKey(const Key('target_com.fb.orca/.Send')));
      expect(whatsapp.dx < messenger.dx && whatsapp.dy == messenger.dy, isTrue); // WhatsApp first
      await t.tap(find.byKey(const Key('target_com.fb.orca/.Send')));
      await t.pumpAndSettle();
      expect(recents.ids, ['com.fb.orca/.Send', 'com.whatsapp/.Send']);
    });

    testWidgets('an app that vanished keeps the text and refreshes the list; a platform error keeps the text', (t) async {
      await pump(t, _Source(_facebook));
      sharer.targetResult = TargetShareResult.targetUnavailable;
      await t.tap(find.byKey(const Key('target_com.fb.orca/.Send')));
      await t.pumpAndSettle();
      expect(targets.requestedMime.length, 2); // fresh list
      expect(find.byKey(const Key('incoming_title')), findsOneWidget);
      sharer.targetResult = TargetShareResult.platformError;
      await t.tap(find.byKey(const Key('target_com.whatsapp/.Send')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('incoming_title')), findsOneWidget);
      expect(t.widget<Text>(find.byKey(const Key('incoming_text'))).data, _facebook);
    });

    testWidgets('Close returns to the normal screen; a share that arrives later replaces an unfinished one', (t) async {
      final source = _Source(_facebook);
      await pump(t, source);
      source.push('https://example.com/second');
      await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
      await t.pump();
      expect(t.widget<Text>(find.byKey(const Key('incoming_text'))).data, 'https://example.com/second');
      await t.tap(find.byKey(const Key('incoming_close')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('unpaired_message')), findsOneWidget);
    });

    testWidgets('plain text that is not a link is labelled as text and kept verbatim', (t) async {
      const text = '  Look at this\nhttps://example.com/a  ';
      await pump(t, _Source(text));
      expect(find.text('Share text'), findsOneWidget);
      expect(t.widget<Text>(find.byKey(const Key('incoming_text'))).data, text);
    });

    testWidgets('without a share the normal pairing screen is unchanged', (t) async {
      await pump(t, _Source());
      expect(find.byKey(const Key('unpaired_message')), findsOneWidget);
      expect(find.byKey(const Key('incoming_title')), findsNothing);
    });
  });
}

void orderTests() {
  group('orderByRecents', () {
    test('recents first in recency order, only installed ones, the rest keep their order', () {
      final ordered = orderByRecents(['a', 'b', 'c', 'd'], ['d', 'gone', 'b'], (e) => e);
      expect(ordered, ['d', 'b', 'a', 'c']);
    });
  });
}
