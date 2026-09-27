import 'dart:async';
import 'dart:io';

import '../platform/share_bridge.dart';
import '../protocol/protocol.dart';
import '../receiver/receiver_interface.dart';
import '../receiver/receiver_server.dart' show ReceiverEvent, ReceiverEventKind;
import '../receiver/temp_cache.dart';

/// App-presentation state machine (Prompt A14 §76) -- distinct from the
/// wire-protocol [HandoffState] enum. Never redefines wire acknowledgement
/// semantics; only describes what the current screen should show.
enum AppState { unpaired, paired, receiving, received, error }

class HandoffUiSnapshot {
  const HandoffUiSnapshot({
    required this.state,
    this.handoffId,
    this.displayName,
    this.bytesReceived = 0,
    this.totalBytes = 0,
    this.errorCode,
    this.shareOpened = false,
  });

  final AppState state;
  final String? handoffId;
  final String? displayName;
  final int bytesReceived;
  final int totalBytes;
  final HandoffErrorCode? errorCode;

  /// True only once the Android platform bridge actually reports the
  /// Sharesheet was opened -- this is the local, honest analogue of
  /// `HANDOFF_ACCEPTED` (§58/§135): it is never set merely because bytes
  /// were verified.
  final bool shareOpened;

  double? get progressFraction => totalBytes > 0 ? bytesReceived / totalBytes : null;

  HandoffUiSnapshot copyWith({
    AppState? state,
    String? handoffId,
    String? displayName,
    int? bytesReceived,
    int? totalBytes,
    HandoffErrorCode? errorCode,
    bool? shareOpened,
  }) => HandoffUiSnapshot(
    state: state ?? this.state,
    handoffId: handoffId ?? this.handoffId,
    displayName: displayName ?? this.displayName,
    bytesReceived: bytesReceived ?? this.bytesReceived,
    totalBytes: totalBytes ?? this.totalBytes,
    errorCode: errorCode ?? this.errorCode,
    shareOpened: shareOpened ?? this.shareOpened,
  );
}

/// Wires a [FriendSendReceiverLike] receiver's events into UI-facing
/// state, and
/// drives the platform Sharesheet bridge once a payload is verified.
/// Owns TTL/startup cleanup scheduling -- never one thread per file
/// (§66).
class HandoffController {
  HandoffController({
    required this.receiver,
    required this.tempCache,
    required this.shareBridge,
    this.autoShare = true,
  }) {
    _subscription = receiver.events.listen(_onEvent);
  }

  final FriendSendReceiverLike receiver;
  final TempCache tempCache;
  final ShareBridge shareBridge;
  final bool autoShare;

  final StreamController<HandoffUiSnapshot> _snapshots = StreamController.broadcast();
  StreamSubscription<ReceiverEvent>? _subscription;
  Timer? _cleanupTimer;
  HandoffUiSnapshot _current = const HandoffUiSnapshot(state: AppState.unpaired);

  Stream<HandoffUiSnapshot> get snapshots => _snapshots.stream;
  HandoffUiSnapshot get current => _current;

  void markPaired() => _emit(_current.copyWith(state: AppState.paired));

  /// Periodic, bounded cleanup check -- a single timer, not one per file
  /// (§66/§127).
  void startPeriodicCleanup({Duration interval = const Duration(minutes: 5)}) {
    _cleanupTimer?.cancel();
    _cleanupTimer = Timer.periodic(interval, (_) => tempCache.cleanupExpired());
  }

  Future<void> runStartupCleanup() => tempCache.sweepUntrackedOnStartup();

  void _onEvent(ReceiverEvent event) {
    switch (event.kind) {
      case ReceiverEventKind.progress:
        _emit(
          _current.copyWith(
            state: AppState.receiving,
            handoffId: event.handoffId,
            bytesReceived: event.bytesReceived,
            totalBytes: event.totalBytes,
          ),
        );
        break;
      case ReceiverEventKind.received:
        _emit(
          _current.copyWith(
            state: AppState.received,
            handoffId: event.handoffId,
            displayName: event.preferredFilename,
            bytesReceived: event.bytesReceived,
            totalBytes: event.totalBytes,
            shareOpened: false,
          ),
        );
        if (autoShare) {
          unawaited(shareCurrentFile(event.file!.path, event.preferredFilename ?? 'received', event.mimeType ?? 'application/octet-stream'));
        }
        break;
      case ReceiverEventKind.failed:
        _emit(
          _current.copyWith(state: AppState.error, handoffId: event.handoffId, errorCode: event.errorCode),
        );
        break;
      case ReceiverEventKind.cancelled:
        _emit(_current.copyWith(state: AppState.paired, handoffId: event.handoffId));
        break;
    }
  }

  /// Retries opening the Sharesheet for the currently-received file
  /// (§59-61) -- never claims [HandoffState.handoffAccepted] unless the
  /// platform bridge reports success.
  Future<ShareResult> shareCurrentFile(String path, String displayName, String mimeType) async {
    final result = await shareBridge.shareFile(path: path, displayName: displayName, mimeType: mimeType);
    if (result == ShareResult.opened) {
      _emit(_current.copyWith(shareOpened: true));
    }
    return result;
  }

  Future<void> discardCurrent(String handoffId, String filePath) async {
    await tempCache.discard(handoffId, File(filePath));
    _emit(const HandoffUiSnapshot(state: AppState.paired));
  }

  void _emit(HandoffUiSnapshot next) {
    _current = next;
    _snapshots.add(next);
  }

  Future<void> dispose() async {
    _cleanupTimer?.cancel();
    await _subscription?.cancel();
    await _snapshots.close();
  }
}
