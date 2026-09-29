import 'dart:async';
import 'dart:io';

import '../platform/share_bridge.dart';
import '../platform/share_targets.dart';
import '../protocol/protocol.dart';
import '../receiver/receiver_interface.dart';
import '../receiver/receiver_server.dart' show ReceiverEvent, ReceiverEventKind;
import '../receiver/temp_cache.dart';
import '../security/desktop_trust_store.dart';

/// App-presentation state machine (Prompt A14 §76, extended by the final GUI implementation) -- distinct
/// from the wire-protocol [HandoffState] enum. Never redefines wire acknowledgement semantics; only
/// describes what the current screen should show.
///
///   unpaired -> pairing -> ready
///   ready -> receiving -> verifying -> received -> choosingTarget -> handoffAccepted -> ready
///   receiving -> cancelled -> ready          any failed receive -> error -> ready
///
/// `verifying` is real: it is entered when every announced byte has arrived and the SHA-256 check is still
/// running (the receiver only emits `received` after the hash matched). `handoffAccepted` means only that the
/// Android Sharesheet / a target app was opened -- never that anyone received the file.
enum AppState { unpaired, pairing, ready, receiving, verifying, received, choosingTarget, handoffAccepted, cancelled, error }

/// User-driven transitions (receiver events are authoritative for the wire and are mapped separately).
const Map<AppState, Set<AppState>> allowedUiTransitions = {
  AppState.unpaired: {AppState.pairing, AppState.ready},
  AppState.pairing: {AppState.unpaired, AppState.ready},
  AppState.ready: {AppState.unpaired},
  AppState.receiving: {},
  AppState.verifying: {},
  AppState.received: {AppState.choosingTarget, AppState.handoffAccepted, AppState.ready},
  AppState.choosingTarget: {AppState.received, AppState.handoffAccepted, AppState.ready},
  AppState.handoffAccepted: {AppState.choosingTarget, AppState.ready},
  AppState.cancelled: {AppState.ready},
  AppState.error: {AppState.ready},
};

class HandoffUiSnapshot {
  const HandoffUiSnapshot({
    required this.state,
    this.handoffId,
    this.displayName,
    this.mimeType,
    this.filePath,
    this.bytesReceived = 0,
    this.totalBytes = 0,
    this.bytesPerSecond,
    this.errorCode,
    this.pairingError,
    this.targetLabel,
    this.shareOpened = false,
  });

  final AppState state;
  final String? handoffId;
  final String? displayName;
  final String? mimeType;

  /// Verified temp file; set only from the `received` event until the file is discarded.
  final String? filePath;
  final int bytesReceived;
  final int totalBytes;

  /// Measured from real progress events; null until two events have been seen.
  final double? bytesPerSecond;
  final HandoffErrorCode? errorCode;
  final String? pairingError;

  /// Label of the app the file was handed to (only in [AppState.handoffAccepted]).
  final String? targetLabel;

  /// True only once the Android platform bridge actually reports the Sharesheet was opened -- the local,
  /// honest analogue of `HANDOFF_ACCEPTED`; never set merely because bytes were verified.
  final bool shareOpened;

  double? get progressFraction => totalBytes > 0 ? bytesReceived / totalBytes : null;

  HandoffUiSnapshot copyWith({
    AppState? state,
    String? handoffId,
    String? displayName,
    String? mimeType,
    String? filePath,
    int? bytesReceived,
    int? totalBytes,
    double? bytesPerSecond,
    HandoffErrorCode? errorCode,
    String? pairingError,
    String? targetLabel,
    bool? shareOpened,
    bool clearFile = false,
    bool clearPairingError = false,
    bool clearSpeed = false,
    bool clearTarget = false,
  }) => HandoffUiSnapshot(
    state: state ?? this.state,
    handoffId: handoffId ?? this.handoffId,
    displayName: displayName ?? this.displayName,
    mimeType: mimeType ?? this.mimeType,
    filePath: clearFile ? null : (filePath ?? this.filePath),
    bytesReceived: bytesReceived ?? this.bytesReceived,
    totalBytes: totalBytes ?? this.totalBytes,
    bytesPerSecond: clearSpeed ? null : (bytesPerSecond ?? this.bytesPerSecond),
    errorCode: errorCode ?? this.errorCode,
    pairingError: clearPairingError ? null : (pairingError ?? this.pairingError),
    targetLabel: clearTarget ? null : (targetLabel ?? this.targetLabel),
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
    this.autoShare = false,
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

  bool _move(AppState to, HandoffUiSnapshot Function(HandoffUiSnapshot) apply) {
    if (_current.state != to && !(allowedUiTransitions[_current.state] ?? const {}).contains(to)) return false;
    _emit(apply(_current.copyWith(state: to)));
    return true;
  }

  /// Pairing succeeded (or trust was restored from disk).
  bool markPaired() => _current.state == AppState.ready || _move(AppState.ready, (s) => s.copyWith(clearPairingError: true));

  bool beginPairing() => _move(AppState.pairing, (s) => s.copyWith(clearPairingError: true));

  bool pairingFailed(String message) => _move(AppState.unpaired, (s) => s.copyWith(pairingError: message));

  /// The user forgot the trusted computer on this phone (the caller removes it from the trust store).
  bool markForgotten() => _move(AppState.unpaired, (s) => const HandoffUiSnapshot(state: AppState.unpaired));

  /// received -> choosingTarget (the picker sheet is shown by the UI).
  bool chooseApp() => _move(AppState.choosingTarget, (s) => s);

  /// The picker was dismissed without choosing: the verified file is still there.
  bool cancelChoosing() => _current.state == AppState.choosingTarget && _move(AppState.received, (s) => s);

  /// A target app / the system Sharesheet was really opened for the file.
  bool handoffAccepted({String? targetLabel}) =>
      _move(AppState.handoffAccepted, (s) => s.copyWith(shareOpened: true, targetLabel: targetLabel));

  /// Leaves cancelled / error screens, or finishes after a handoff (removing the temporary copy).
  Future<void> done() async {
    if (_current.state == AppState.handoffAccepted && _current.handoffId != null && _current.filePath != null) {
      await tempCache.discard(_current.handoffId!, File(_current.filePath!));
    }
    _move(AppState.ready, (s) => HandoffUiSnapshot(state: AppState.ready));
  }

  /// Prompt A17-E1: on a cold start, restores the `paired` UI state when a
  /// trusted desktop already exists on disk. Without this, a normal
  /// force-stop/relaunch (or device reboot) left the app showing the
  /// initial unpaired "Paste pairing payload" screen even though the real
  /// persistent trust (Prompt A15) was intact underneath -- the security
  /// state was never actually lost, only the UI failed to reflect it,
  /// which reads to a real user as "I have to pair again every time".
  Future<void> restoreTrustState(DesktopTrustStore trustStore) async {
    if ((await trustStore.allDesktops()).isNotEmpty) {
      markPaired();
    }
  }

  /// Periodic, bounded cleanup check -- a single timer, not one per file
  /// (§66/§127).
  void startPeriodicCleanup({Duration interval = const Duration(minutes: 5)}) {
    _cleanupTimer?.cancel();
    _cleanupTimer = Timer.periodic(interval, (_) => tempCache.cleanupExpired());
  }

  Future<void> runStartupCleanup() => tempCache.sweepUntrackedOnStartup();

  static const double _speedWindowSeconds = 0.4;
  DateTime? _windowStartAt;
  int _windowStartBytes = 0;
  double? _speed;

  void _onEvent(ReceiverEvent event) {
    switch (event.kind) {
      case ReceiverEventKind.progress:
        final now = DateTime.now();
        if (_current.handoffId != event.handoffId || _windowStartAt == null) {
          _speed = null;
          _windowStartAt = now;
          _windowStartBytes = event.bytesReceived;
        } else {
          // Speed is measured over a window (not per event): the receiver emits an event per network chunk,
          // often only milliseconds apart, and a per-event delta would be noise.
          final seconds = now.difference(_windowStartAt!).inMicroseconds / 1e6;
          final delta = event.bytesReceived - _windowStartBytes;
          if (seconds >= _speedWindowSeconds && delta >= 0) {
            final instant = delta / seconds;
            _speed = _speed == null ? instant : _speed! * 0.6 + instant * 0.4;
            _windowStartAt = now;
            _windowStartBytes = event.bytesReceived;
          }
        }
        final allBytesIn = event.totalBytes > 0 && event.bytesReceived >= event.totalBytes;
        _emit(
          _current.copyWith(
            state: allBytesIn ? AppState.verifying : AppState.receiving,
            handoffId: event.handoffId,
            displayName: event.preferredFilename,
            mimeType: event.mimeType,
            bytesReceived: event.bytesReceived,
            totalBytes: event.totalBytes,
            bytesPerSecond: allBytesIn ? null : _speed,
            clearSpeed: allBytesIn || _speed == null,
            clearFile: true,
            clearTarget: true,
            shareOpened: false,
          ),
        );
        break;
      case ReceiverEventKind.received:
        _emit(
          _current.copyWith(
            state: AppState.received,
            handoffId: event.handoffId,
            displayName: event.preferredFilename,
            mimeType: event.mimeType,
            filePath: event.file?.path,
            bytesReceived: event.bytesReceived,
            totalBytes: event.totalBytes,
            clearSpeed: true,
            shareOpened: false,
          ),
        );
        if (autoShare) {
          unawaited(shareCurrentFile(event.file!.path, event.preferredFilename ?? 'received', event.mimeType ?? 'application/octet-stream'));
        }
        break;
      case ReceiverEventKind.failed:
        _emit(_current.copyWith(state: AppState.error, handoffId: event.handoffId, errorCode: event.errorCode, clearSpeed: true, clearFile: true));
        break;
      case ReceiverEventKind.cancelled:
        _emit(_current.copyWith(state: AppState.cancelled, handoffId: event.handoffId, clearSpeed: true, clearFile: true));
        break;
    }
  }

  /// Retries opening the Sharesheet for the currently-received file
  /// (§59-61) -- never claims [HandoffState.handoffAccepted] unless the
  /// platform bridge reports success.
  Future<ShareResult> shareCurrentFile(String path, String displayName, String mimeType) async {
    final result = await shareBridge.shareFile(path: path, displayName: displayName, mimeType: mimeType);
    if (result == ShareResult.opened) {
      if (!handoffAccepted() && _current.state == AppState.received) _emit(_current.copyWith(shareOpened: true));
    }
    return result;
  }

  /// Targeted send to one app chosen in the picker. Marks handoff accepted only if Android accepted the launch;
  /// if the app vanished the picker stays open state-wise (choosingTarget) and the caller refreshes the list.
  Future<TargetShareResult> shareToTarget(ShareTarget target) async {
    final s = _current;
    final path = s.filePath;
    if (s.state != AppState.choosingTarget || path == null) return TargetShareResult.invalidTempFile;
    final result = await shareBridge.shareToTarget(
      path: path,
      displayName: s.displayName ?? 'received',
      mimeType: s.mimeType ?? 'application/octet-stream',
      targetId: target.id,
    );
    if (result == TargetShareResult.opened) handoffAccepted(targetLabel: target.label);
    return result;
  }

  Future<void> discardCurrent(String handoffId, String filePath) async {
    await tempCache.discard(handoffId, File(filePath));
    _move(AppState.ready, (s) => const HandoffUiSnapshot(state: AppState.ready));
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
