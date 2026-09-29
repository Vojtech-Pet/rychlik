// Screenshot harness for the visual acceptance review. Runs only when FS_SCREENSHOT_DIR is set:
//   FS_SCREENSHOT_DIR=../artifacts/gui_implementation_review/friendsend flutter test test/screenshots_test.dart
// Renders the real screens with the real Roboto + Material Icons fonts from the Flutter SDK cache.
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/handoff/handoff_controller.dart';
import 'package:friendsend/platform/share_targets.dart';
import 'package:friendsend/protocol/protocol.dart';
import 'package:friendsend/security/desktop_trust_store.dart';
import 'package:friendsend/ui/screens/pairing_screen.dart';
import 'package:friendsend/ui/screens/target_picker.dart';
import 'package:friendsend/ui/screens/transfer_screens.dart';
import 'package:friendsend/ui/screens/trusted_screen.dart';
import 'package:friendsend/ui/theme/fs_theme.dart';

Future<void> _loadRoboto() async {
  final root = Platform.environment['FLUTTER_ROOT'] ?? '/mnt/Basic_data_partition1/vojtech/flutter';
  final dir = '$root/bin/cache/artifacts/material_fonts';
  final loader = FontLoader('Roboto');
  for (final f in ['Roboto-Regular.ttf', 'Roboto-Medium.ttf', 'Roboto-Bold.ttf']) {
    final file = File('$dir/$f');
    if (file.existsSync()) loader.addFont(Future.value(ByteData.sublistView(file.readAsBytesSync())));
  }
  await loader.load();
  final icons = File('$dir/MaterialIcons-Regular.otf');
  if (icons.existsSync()) {
    final iconLoader = FontLoader('MaterialIcons')..addFont(Future.value(ByteData.sublistView(icons.readAsBytesSync())));
    await iconLoader.load();
  }
}

void main() {
  final outDir = Platform.environment['FS_SCREENSHOT_DIR'];
  if (outDir == null) {
    test('screenshots skipped (set FS_SCREENSHOT_DIR)', () {}, skip: true);
    return;
  }
  TestWidgetsFlutterBinding.ensureInitialized();

  const video = HandoffUiSnapshot(state: AppState.received, displayName: 'holiday.mp4', mimeType: 'video/mp4', bytesReceived: 207618048, totalBytes: 207618048);
  const receiving = HandoffUiSnapshot(state: AppState.receiving, displayName: 'holiday.mp4', mimeType: 'video/mp4', bytesReceived: 153600000, totalBytes: 207618048, bytesPerSecond: 13421772);
  const verifying = HandoffUiSnapshot(state: AppState.verifying, displayName: 'holiday.mp4', mimeType: 'video/mp4', bytesReceived: 207618048, totalBytes: 207618048);
  const accepted = HandoffUiSnapshot(state: AppState.handoffAccepted, displayName: 'holiday.mp4', targetLabel: 'Messenger');
  const acceptedSystem = HandoffUiSnapshot(state: AppState.handoffAccepted, displayName: 'holiday.mp4');
  final desktop = TrustedDesktop(desktopInstanceId: 'd', desktopPublicSigningKey: List<int>.generate(32, (i) => 0xb6 + i), pairedAtUtc: '2026-09-28T06:20:00Z');
  const targets = [
    ShareTarget(id: 'a', label: 'Messenger'),
    ShareTarget(id: 'b', label: 'WhatsApp'),
    ShareTarget(id: 'c', label: 'Telegram'),
    ShareTarget(id: 'd', label: 'Signal'),
    ShareTarget(id: 'e', label: 'Messages'),
    ShareTarget(id: 'f', label: 'Gmail'),
  ];

  final cases = <String, Widget Function()>{
    '01_pairing': () => PairingScreen(onPair: (_) {}),
    '01d_pairing_error': () => PairingScreen(onPair: (_) {}, error: 'This does not look like a pairing code.'),
    '01e_pairing_progress': () => PairingScreen(onPair: (_) {}, busy: true),
    '02_ready': () => ReadyScreen(computerName: 'Rýchlik', onOpenComputer: () {}),
    '03_receiving': () => ReceivingScreen(state: receiving, onCancel: () {}),
    '04_verifying': () => const VerifyingScreen(state: verifying),
    '05_received': () => ReceivedScreen(state: video, onChooseApp: () {}, onDiscard: () {}),
    '06_picker': () => Stack(children: [
      ReceivedScreen(state: video, onChooseApp: () {}, onDiscard: () {}),
      Builder(builder: (c) => ColoredBox(color: c.fs.scrim, child: const SizedBox.expand())),
      Align(alignment: Alignment.bottomCenter, child: Builder(builder: (c) => Material(color: c.fs.surface, borderRadius: const BorderRadius.vertical(top: Radius.circular(20)), child: const TargetPickerSheet(fileName: 'holiday.mp4', sizeBytes: 207618048, mimeType: 'video/mp4', targets: targets)))),
    ]),
    '06b_picker_no_targets': () => Stack(children: [
      ReceivedScreen(state: video, onChooseApp: () {}, onDiscard: () {}),
      Builder(builder: (c) => ColoredBox(color: c.fs.scrim, child: const SizedBox.expand())),
      Align(alignment: Alignment.bottomCenter, child: Builder(builder: (c) => Material(color: c.fs.surface, borderRadius: const BorderRadius.vertical(top: Radius.circular(20)), child: const TargetPickerSheet(fileName: 'holiday.mp4', sizeBytes: 207618048, mimeType: 'video/mp4', targets: [])))),
    ]),
    '07f_handoff_accepted': () => HandoffAcceptedScreen(state: accepted, onDone: () {}, onSendAgain: () {}),
    '07g_handoff_system_sheet': () => HandoffAcceptedScreen(state: acceptedSystem, onDone: () {}, onSendAgain: () {}),
    '08b_cancelled': () => CancelledScreen(onDone: () {}),
    '08c_integrity_failure': () => ErrorScreen(state: const HandoffUiSnapshot(state: AppState.error, errorCode: HandoffErrorCode.integrityMismatch), onDone: () {}),
    '08_transfer_error': () => ErrorScreen(state: const HandoffUiSnapshot(state: AppState.error, errorCode: HandoffErrorCode.incompleteTransfer), onDone: () {}),
    '09_trusted_desktop': () => TrustedComputerScreen(desktop: desktop, onForget: () async {}),
  };

  Future<void> shoot(WidgetTester t, String name, Brightness b, Size size, Widget Function() build, {bool forgetDialog = false}) async {
    final key = GlobalKey();
    t.view.physicalSize = size * 2;
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    await t.pumpWidget(RepaintBoundary(key: key, child: MaterialApp(debugShowCheckedModeBanner: false, theme: FsTheme.build(b), home: build())));
    await t.pump();
    if (forgetDialog) {
      await t.tap(find.byKey(const Key('forget_button')));
      await t.pumpAndSettle();
    }
    final bytes = await t.runAsync(() async {
      final boundary = key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final image = await boundary.toImage(pixelRatio: 2);
      final data = await image.toByteData(format: ui.ImageByteFormat.png);
      return data!.buffer.asUint8List();
    });
    final file = File('$outDir/${size.width.toInt()}x${size.height.toInt()}/${b.name}/$name.png');
    file.parent.createSync(recursive: true);
    file.writeAsBytesSync(bytes!);
  }

  setUpAll(_loadRoboto);

  for (final size in const [Size(390, 844), Size(360, 800), Size(412, 915)]) {
    for (final b in Brightness.values) {
      testWidgets('screens ${size.width.toInt()}x${size.height.toInt()} ${b.name}', (t) async {
        for (final e in cases.entries) {
          await shoot(t, e.key, b, size, e.value);
        }
        await shoot(t, '10_forget_confirmation', b, size, () => TrustedComputerScreen(desktop: desktop, onForget: () async {}), forgetDialog: true);
        expect(t.takeException(), isNull);
      });
    }
  }
}
