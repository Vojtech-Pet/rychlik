import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/handoff/handoff_controller.dart';
import 'package:friendsend/platform/share_bridge.dart';
import 'package:friendsend/protocol/protocol.dart';
import 'package:friendsend/receiver/receiver_interface.dart';
import 'package:friendsend/receiver/receiver_server.dart' show ReceiverEvent, ReceiverEventKind;
import 'package:friendsend/receiver/temp_cache.dart';

class _Receiver implements FriendSendReceiverLike {
  final _c = StreamController<ReceiverEvent>.broadcast();
  final cancelled = <String>[];

  @override
  Stream<ReceiverEvent> get events => _c.stream;

  @override
  void requestCancel(String handoffId) => cancelled.add(handoffId);

  void emit(ReceiverEvent e) => _c.add(e);
}

class _Bridge extends ShareBridge {
  _Bridge(this.result);

  ShareResult result;
  int calls = 0;

  @override
  Future<ShareResult> shareFile({required String path, required String displayName, required String mimeType}) async {
    calls++;
    return result;
  }
}

ReceiverEvent _progress(int got, int total, {String id = 'h1'}) =>
    ReceiverEvent(handoffId: id, kind: ReceiverEventKind.progress, bytesReceived: got, totalBytes: total, mimeType: 'video/mp4', preferredFilename: 'holiday.mp4');

void main() {
  late Directory root;
  late TempCache cache;
  late _Receiver receiver;
  late _Bridge bridge;
  late HandoffController c;
  late File file;

  Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 5));

  Future<void> deliver({int total = 1000}) async {
    receiver.emit(_progress(100, total));
    await settle();
    receiver.emit(_progress(total, total));
    await settle();
    receiver.emit(ReceiverEvent(handoffId: 'h1', kind: ReceiverEventKind.received, bytesReceived: total, totalBytes: total, file: file, mimeType: 'video/mp4', preferredFilename: 'holiday.mp4'));
    await settle();
  }

  setUp(() {
    root = Directory.systemTemp.createTempSync('fs_state_machine_');
    cache = TempCache(root);
    receiver = _Receiver();
    bridge = _Bridge(ShareResult.opened);
    file = File('${root.path}/h1.bin')..writeAsBytesSync(List.filled(10, 1));
    c = HandoffController(receiver: receiver, tempCache: cache, shareBridge: bridge);
  });

  tearDown(() async {
    await c.dispose();
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  test('the presentation flow is receiving -> verifying -> received, with verifying entered only once every byte is in', () async {
    c.markPaired();
    final seen = <AppState>[];
    c.snapshots.listen((s) => seen.add(s.state));
    await deliver();
    expect(seen, [AppState.receiving, AppState.verifying, AppState.received]);
    expect(c.current.filePath, file.path);
    expect(c.current.displayName, 'holiday.mp4');
  });

  test('verifying never carries a fabricated speed, and receiving speed is measured from real progress events', () async {
    c.markPaired();
    receiver.emit(_progress(100, 1000));
    await settle();
    expect(c.current.bytesPerSecond, isNull); // one event: nothing to measure
    await Future<void>.delayed(const Duration(milliseconds: 80));
    receiver.emit(_progress(500, 1000));
    await settle();
    expect(c.current.bytesPerSecond, isNotNull);
    receiver.emit(_progress(1000, 1000));
    await settle();
    expect(c.current.state, AppState.verifying);
    expect(c.current.bytesPerSecond, isNull);
  });

  test('a verification failure goes to error and never leaves a file path to share', () async {
    c.markPaired();
    receiver.emit(_progress(1000, 1000));
    await settle();
    receiver.emit(ReceiverEvent(handoffId: 'h1', kind: ReceiverEventKind.failed, bytesReceived: 1000, totalBytes: 1000, errorCode: HandoffErrorCode.integrityMismatch));
    await settle();
    expect(c.current.state, AppState.error);
    expect(c.current.errorCode, HandoffErrorCode.integrityMismatch);
    expect(c.current.filePath, isNull);
    expect(c.chooseApp(), isFalse); // cannot choose an app for a discarded file
    await c.done();
    expect(c.current.state, AppState.ready);
  });

  test('cancel is a real state, distinct from error, and Done returns to ready', () async {
    c.markPaired();
    receiver.emit(_progress(100, 1000));
    await settle();
    receiver.emit(ReceiverEvent(handoffId: 'h1', kind: ReceiverEventKind.cancelled, bytesReceived: 100, totalBytes: 1000));
    await settle();
    expect(c.current.state, AppState.cancelled);
    await c.done();
    expect(c.current.state, AppState.ready);
  });

  test('received -> choosingTarget -> handoffAccepted -> ready (Done removes the temporary copy)', () async {
    c.markPaired();
    await deliver();
    expect(c.chooseApp(), isTrue);
    expect(c.current.state, AppState.choosingTarget);
    expect(c.current.shareOpened, isFalse);
    expect(c.handoffAccepted(targetLabel: 'Messenger'), isTrue);
    expect(c.current.state, AppState.handoffAccepted);
    expect(c.current.targetLabel, 'Messenger');
    expect(c.current.shareOpened, isTrue);
    expect(file.existsSync(), isTrue); // handoff accepted does not delete the file yet
    await c.done();
    expect(c.current.state, AppState.ready);
    expect(file.existsSync(), isFalse);
  });

  test('closing the picker returns to received and the file is kept', () async {
    c.markPaired();
    await deliver();
    c.chooseApp();
    expect(c.cancelChoosing(), isTrue);
    expect(c.current.state, AppState.received);
    expect(c.current.filePath, file.path);
    expect(file.existsSync(), isTrue);
  });

  test('"send with another app" reopens the picker from handoffAccepted', () async {
    c.markPaired();
    await deliver();
    c.chooseApp();
    c.handoffAccepted();
    expect(c.chooseApp(), isTrue);
    expect(c.current.state, AppState.choosingTarget);
  });

  test('discard from received or choosingTarget removes the real file and returns to ready', () async {
    c.markPaired();
    await deliver();
    c.chooseApp();
    await c.discardCurrent('h1', file.path);
    expect(c.current.state, AppState.ready);
    expect(file.existsSync(), isFalse);
  });

  test('a failed Sharesheet open never claims handoff accepted', () async {
    bridge.result = ShareResult.platformError;
    c.markPaired();
    await deliver();
    c.chooseApp();
    final r = await c.shareCurrentFile(file.path, 'holiday.mp4', 'video/mp4');
    expect(r, ShareResult.platformError);
    expect(c.current.state, AppState.choosingTarget);
    expect(c.current.shareOpened, isFalse);
  });

  test('illegal UI transitions are refused and emit nothing', () async {
    final seen = <AppState>[];
    c.snapshots.listen((s) => seen.add(s.state));
    expect(c.chooseApp(), isFalse); // unpaired
    expect(c.handoffAccepted(), isFalse);
    expect(c.cancelChoosing(), isFalse);
    c.markPaired();
    expect(c.chooseApp(), isFalse); // ready: nothing to choose
    receiver.emit(_progress(100, 1000));
    await settle();
    expect(c.chooseApp(), isFalse); // receiving
    expect(c.markForgotten(), isFalse); // cannot forget mid-transfer
    await settle();
    expect(seen, [AppState.ready, AppState.receiving]);
  });

  test('pairing states: unpaired -> pairing -> (failure keeps the message) -> ready clears it', () {
    expect(c.beginPairing(), isTrue);
    expect(c.current.state, AppState.pairing);
    expect(c.pairingFailed('This does not look like a pairing code.'), isTrue);
    expect(c.current.state, AppState.unpaired);
    expect(c.current.pairingError, 'This does not look like a pairing code.');
    c.beginPairing();
    expect(c.current.pairingError, isNull);
    c.markPaired();
    expect(c.current.state, AppState.ready);
    expect(c.markForgotten(), isTrue);
    expect(c.current.state, AppState.unpaired);
  });

  test('every state has an entry in the transition table', () {
    expect(allowedUiTransitions.keys.toSet(), AppState.values.toSet());
  });
}
