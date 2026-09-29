import 'dart:async';

import 'package:flutter/material.dart';

import '../handoff/handoff_controller.dart';
import '../identity/device_identity.dart';
import '../platform/incoming_share.dart';
import '../platform/media_bridge.dart';
import '../monetization/billing_adapter.dart';
import '../monetization/entitlement_service.dart';
import '../monetization/purchase_verifier.dart';
import '../platform/recent_targets.dart';
import '../platform/share_bridge.dart';
import '../platform/share_targets.dart';
import '../security/desktop_trust_store.dart';
import '../security/secure_pairing_manager.dart';
import 'screens/incoming_share_screen.dart';
import 'screens/pairing_screen.dart';
import 'screens/qr_scan_screen.dart';
import 'screens/target_picker.dart';
import 'screens/transfer_screens.dart';
import 'screens/trusted_screen.dart';
import 'screens/unlock_screen.dart';
import 'theme/fs_theme.dart';

/// Chooses the approved screen for the controller's presentation state. Screens render controller state; they
/// never invent one (Verifying, Received, Choosing-target and Handoff-accepted all come from real transitions).
class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.identity,
    required this.pairingManager,
    required this.controller,
    this.trustStore,
    this.targetProvider = const NoShareTargets(),
    this.scannerBuilder = _defaultScanner,
    this.incomingShares = const NoIncomingShares(),
    this.textSharer,
    this.recentTargets = const NoRecentTargets(),
    this.videoFetcher,
    this.entitlement = const AlwaysUnlockedEntitlement(),
    this.billing = const UnavailableBillingAdapter(),
    this.verifier = const ServerPurchaseVerifier(),
  });

  static Widget _defaultScanner(BuildContext context) => const QrScanScreen();

  /// Builds the QR scanner route (replaced by a fake in widget tests, which have no camera).
  final Widget Function(BuildContext) scannerBuilder;

  /// Text other apps share to FriendSend (Android Share button), and how to hand it on to a chosen app.
  final IncomingShareSource incomingShares;
  final TextSharer? textSharer;
  final RecentTargetsStore recentTargets;

  /// When present, a shared link can also be sent as the downloaded video itself.
  final VideoFetcher? videoFetcher;

  /// FriendSend's 5-free-sends-then-unlock model. Gates every send action (targeted or system-sheet); only a
  /// successful *targeted* send is ever counted against the trial.
  final EntitlementSource entitlement;
  final BillingAdapter billing;
  final PurchaseVerifier verifier;

  final DeviceIdentity identity;
  final SecurePairingManager pairingManager;
  final HandoffController controller;
  final DesktopTrustStore? trustStore;
  final ShareTargetProvider targetProvider;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  StreamSubscription<HandoffUiSnapshot>? _sub;
  bool _pickerOpen = false;
  TrustedDesktop? _desktop;
  String? _incomingText;
  StreamSubscription<String>? _incomingSub;
  Future<List<ShareTarget>> _incomingTargets = Future.value(const <ShareTarget>[]);
  IncomingVideoPhase _videoPhase = IncomingVideoPhase.idle;
  VideoProgress? _videoProgress;
  DownloadedVideo? _video;
  String? _videoError;
  StreamSubscription<VideoProgress>? _videoSub;
  String? _saveNote;
  bool _saving = false;
  int _videoRun = 0; // identifies the current download so a late result of a cancelled/replaced one is ignored
  EntitlementStatus _entitlementStatus = const EntitlementStatus(state: EntitlementState.fullVerified, remainingTrialSends: 0);
  bool _showUnlock = false;
  StreamSubscription<EntitlementStatus>? _entitlementSub;

  @override
  void initState() {
    super.initState();
    widget.incomingShares.takeInitial().then((text) {
      if (text != null && mounted) _showIncoming(text);
    });
    _videoSub = widget.videoFetcher?.progress.listen((p) {
      if (mounted && _videoPhase == IncomingVideoPhase.downloading) setState(() => _videoProgress = p);
    });
    _incomingSub = widget.incomingShares.updates.listen((text) {
      if (mounted) _showIncoming(text);
    });
    widget.entitlement.status().then((status) {
      if (mounted) setState(() => _entitlementStatus = status);
    });
    _entitlementSub = widget.entitlement.changes.listen((status) {
      if (mounted) setState(() => _entitlementStatus = status);
    });
    _sub = widget.controller.snapshots.listen(_onState);
    _loadDesktop();
    if (widget.controller.current.state == AppState.choosingTarget) _scheduleSheet();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _incomingSub?.cancel();
    _videoSub?.cancel();
    _entitlementSub?.cancel();
    super.dispose();
  }

  Future<void> _loadDesktop() async {
    final desktops = await widget.trustStore?.allDesktops();
    if (mounted) setState(() => _desktop = (desktops == null || desktops.isEmpty) ? null : desktops.first);
  }

  void _onState(HandoffUiSnapshot s) {
    if (s.state == AppState.ready) _loadDesktop();
    if (s.state == AppState.choosingTarget) _scheduleSheet();
  }

  /// True when a send may proceed; otherwise shows the unlock screen and returns false. Every send entry point
  /// (targeted or system-sheet, from either the received-file flow or the incoming-share flow) goes through this.
  bool _requireEntitlement() {
    if (_entitlementStatus.canSend) return true;
    setState(() => _showUnlock = true);
    return false;
  }

  void _gatedChooseApp() {
    if (_requireEntitlement()) widget.controller.chooseApp();
  }

  void _onPurchaseResolved(EntitlementStatus status) {
    setState(() {
      _entitlementStatus = status;
      _showUnlock = false;
    });
  }

  void _scheduleSheet() {
    if (_pickerOpen) return;
    _pickerOpen = true;
    WidgetsBinding.instance.addPostFrameCallback((_) => _showPicker());
  }

  Future<void> _showPicker() async {
    if (!mounted) {
      _pickerOpen = false;
      return;
    }
    final s = widget.controller.current;
    final targetsFuture = widget.targetProvider.targetsFor(s.mimeType ?? 'application/octet-stream');
    final result = await showModalBottomSheet<PickerResult>(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.fs.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => FutureBuilder<List<ShareTarget>>(
        future: targetsFuture,
        builder: (_, snap) => TargetPickerSheet(
          fileName: s.displayName ?? 'Received file',
          sizeBytes: s.totalBytes > 0 ? s.totalBytes : s.bytesReceived,
          mimeType: s.mimeType,
          targets: snap.data ?? const [],
          loading: snap.connectionState != ConnectionState.done,
        ),
      ),
    );
    _pickerOpen = false;
    if (!mounted) return;
    final current = widget.controller.current;
    if (current.state != AppState.choosingTarget) return;
    switch (result?.action) {
      case PickerAction.discard:
        await _discard();
      case PickerAction.systemSheet:
        await _openSystemSheet();
      case PickerAction.target:
        await _sendToTarget(result!.target!);
      case null:
        widget.controller.cancelChoosing();
    }
  }

  Future<void> _sendToTarget(ShareTarget target) async {
    final result = await widget.controller.shareToTarget(target);
    if (result == TargetShareResult.opened) {
      final status = await widget.entitlement.recordSuccessfulTargetedSend();
      if (mounted) setState(() => _entitlementStatus = status);
      return;
    }
    if (!mounted) return;
    final message = result == TargetShareResult.targetUnavailable
        ? '${target.label} isn’t available any more. Pick another app.'
        : 'Couldn’t open ${target.label}. The file is still here.';
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
    if (result == TargetShareResult.targetUnavailable && widget.controller.current.state == AppState.choosingTarget) {
      _scheduleSheet(); // reopen with a freshly resolved list
    } else {
      widget.controller.cancelChoosing();
    }
  }

  Future<void> _openSystemSheet() async {
    final s = widget.controller.current;
    final path = s.filePath;
    if (path == null) {
      widget.controller.cancelChoosing();
      return;
    }
    final result = await widget.controller.shareCurrentFile(path, s.displayName ?? 'received', s.mimeType ?? 'application/octet-stream');
    if (result != ShareResult.opened) {
      widget.controller.cancelChoosing();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Couldn’t open the Android Sharesheet. The file is still here.')));
      }
    }
  }

  /// Apps that can take the shared text, most recently used first (platform order otherwise).
  Future<List<ShareTarget>> _loadTextTargets([String mimeType = 'text/plain']) async {
    final found = await widget.targetProvider.targetsFor(mimeType);
    return orderByRecents(found, await widget.recentTargets.load(), (t) => t.id);
  }

  void _showIncoming(String text) {
    if (_videoPhase == IncomingVideoPhase.downloading) widget.videoFetcher?.cancel();
    _videoRun++;
    setState(() {
      _incomingText = text; // the newest share replaces an older unfinished one
      _incomingTargets = _loadTextTargets();
      _videoPhase = IncomingVideoPhase.idle;
      _video = null;
      _videoProgress = null;
      _videoError = null;
      _saveNote = null;
      _saving = false;
    });
    // A shared link starts fetching its video in the background right away; no extra tap.
    if (widget.videoFetcher != null && IncomingShareScreen.looksLikeLink(text)) _sendVideo();
  }

  Future<void> _sendVideo() async {
    final url = _incomingText?.trim();
    final fetcher = widget.videoFetcher;
    if (url == null || fetcher == null || _videoPhase == IncomingVideoPhase.downloading) return;
    final run = ++_videoRun;
    setState(() {
      _videoPhase = IncomingVideoPhase.downloading;
      _videoProgress = null;
      _videoError = null;
    });
    final outcome = await fetcher.download(url);
    if (!mounted || run != _videoRun) return; // cancelled, closed or replaced by a newer share
    setState(() {
      switch (outcome.kind) {
        case VideoOutcomeKind.done:
          _video = outcome.video;
          _videoPhase = IncomingVideoPhase.ready;
          _incomingTargets = _loadTextTargets(outcome.video!.mimeType); // apps that take a video, not just text
        case VideoOutcomeKind.cancelled:
          _videoPhase = IncomingVideoPhase.idle;
        case VideoOutcomeKind.failed:
          _videoPhase = IncomingVideoPhase.failed;
          _videoError = outcome.message ?? 'The video could not be downloaded.';
      }
    });
  }

  Future<void> _saveVideo() async {
    final video = _video;
    final fetcher = widget.videoFetcher;
    if (video == null || fetcher == null || _saving) return;
    final run = _videoRun;
    setState(() => _saving = true);
    final outcome = await fetcher.saveToPhone(video);
    if (!mounted || run != _videoRun) return;
    setState(() {
      _saving = false;
      _saveNote = switch (outcome) {
        SaveOutcome.saved => 'Saved to Movies/FriendSend',
        SaveOutcome.unsupported => 'Saving needs Android 10 or newer.',
        SaveOutcome.failed => 'Couldn’t save the video.',
      };
    });
  }

  void _cancelVideo() {
    _videoRun++;
    widget.videoFetcher?.cancel();
    setState(() {
      _videoPhase = IncomingVideoPhase.idle;
      _videoProgress = null;
    });
  }

  void _closeIncoming() {
    if (_videoPhase == IncomingVideoPhase.downloading) widget.videoFetcher?.cancel();
    _videoRun++;
    setState(() {
      _incomingText = null;
      _videoPhase = IncomingVideoPhase.idle;
      _video = null;
    });
  }

  Future<void> _pickTextTarget(ShareTarget target) async {
    final text = _incomingText;
    final sharer = widget.textSharer;
    if (text == null || sharer == null) return;
    if (!_requireEntitlement()) return;
    final video = _videoPhase == IncomingVideoPhase.ready ? _video : null;
    final outcome = video != null
        ? await sharer.shareFileToTarget(path: video.path, displayName: video.displayName, mimeType: video.mimeType, targetId: target.id)
        : await sharer.shareTextToTarget(text: text, targetId: target.id);
    if (!mounted) return;
    if (outcome == TargetShareResult.opened) {
      await widget.recentTargets.record(target.id);
      final status = await widget.entitlement.recordSuccessfulTargetedSend();
      if (!mounted) return;
      setState(() {
        _incomingText = null;
        _entitlementStatus = status;
      });
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(outcome == TargetShareResult.targetUnavailable ? '${target.label} isn’t available any more. Pick another app.' : 'Couldn’t open ${target.label}.'),
    ));
    if (outcome == TargetShareResult.targetUnavailable) {
      setState(() {
        _incomingTargets = _loadTextTargets(); // fresh list
      });
    }
  }

  Future<void> _textSystemSheet() async {
    final text = _incomingText;
    final sharer = widget.textSharer;
    if (text == null || sharer == null) return;
    if (!_requireEntitlement()) return;
    final video = _videoPhase == IncomingVideoPhase.ready ? _video : null;
    final outcome = video != null
        ? await sharer.shareFileViaSheet(path: video.path, displayName: video.displayName, mimeType: video.mimeType)
        : await sharer.shareText(text);
    if (!mounted) return;
    if (outcome == ShareResult.opened) {
      setState(() => _incomingText = null);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Couldn’t open the Android Sharesheet.')));
    }
  }

  Future<void> _discard() async {
    final s = widget.controller.current;
    if (s.handoffId != null && s.filePath != null) await widget.controller.discardCurrent(s.handoffId!, s.filePath!);
  }

  /// Same checks completePairing would fail on, minus the network part, so an obviously wrong paste (a URL, random
  /// text, an old code) never enables Pair.
  String? _validateCode(String code) {
    try {
      final payload = widget.pairingManager.parse(code);
      if (payload.protocolVersion != widget.pairingManager.protocolVersion) {
        return 'This code is from a different version of Rýchlik. Update both apps and try again.';
      }
      if (payload.isExpired) return 'This code expired. Create a new pairing code in Rýchlik.';
      return null;
    } on PairingFormatException {
      return 'This does not look like a pairing code.';
    } catch (_) {
      return 'This does not look like a pairing code.';
    }
  }

  Future<void> _scanAndPair() async {
    final scanned = await Navigator.of(context).push<String>(MaterialPageRoute<String>(builder: (_) => widget.scannerBuilder(context)));
    if (scanned == null || !mounted) return;
    final problem = _validateCode(scanned.trim());
    if (problem != null) {
      widget.controller.pairingFailed(problem);
      return;
    }
    await _completePairing(scanned.trim());
  }

  Future<void> _completePairing(String code) async {
    widget.controller.beginPairing();
    try {
      final payload = widget.pairingManager.parse(code);
      await widget.pairingManager.completePairing(payload);
      if (!mounted) return;
      widget.controller.markPaired();
    } on PairingFormatException {
      widget.controller.pairingFailed('This does not look like a pairing code.');
    } on PairingExpiredException {
      widget.controller.pairingFailed('This code expired. Create a new pairing code in Rýchlik.');
    } on PairingUnsupportedProtocolException {
      widget.controller.pairingFailed('This code is from a different version of Rýchlik. Update both apps and try again.');
    } on PairingProofMismatchException {
      widget.controller.pairingFailed('Pairing couldn’t be verified. Create a new code and try again.');
    } catch (_) {
      widget.controller.pairingFailed('Pairing failed. Create a new code and try again.');
    }
  }

  void _openTrusted() {
    final desktop = _desktop;
    final store = widget.trustStore;
    if (desktop == null || store == null) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (ctx) => TrustedComputerScreen(
          desktop: desktop,
          onForget: () async {
            await store.forget(desktop.desktopInstanceId);
            widget.controller.markForgotten();
            if (ctx.mounted) Navigator.of(ctx).popUntil((r) => r.isFirst);
          },
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_showUnlock) {
      return UnlockScreen(
        billing: widget.billing,
        entitlement: widget.entitlement,
        verifier: widget.verifier,
        onResolved: _onPurchaseResolved,
        onNotNow: () => setState(() => _showUnlock = false),
      );
    }
    final incoming = _incomingText;
    if (incoming != null) {
      return IncomingShareScreen(
        text: incoming,
        targets: _incomingTargets,
        onPick: _pickTextTarget,
        onMoreApps: _textSystemSheet,
        onClose: _closeIncoming,
        video: IncomingVideoUi(
          available: widget.videoFetcher != null && IncomingShareScreen.looksLikeLink(incoming),
          phase: _videoPhase,
          progress: _videoProgress,
          video: _video,
          error: _videoError,
          saveNote: _saveNote,
          saving: _saving,
        ),
        onSaveVideo: _saveVideo,
        onSendVideo: _sendVideo,
        onCancelVideo: _cancelVideo,
        trialRemaining: _entitlementStatus.unlocked ? null : _entitlementStatus.remainingTrialSends,
        onBackToLink: () => setState(() {
          _videoPhase = IncomingVideoPhase.idle;
          _video = null;
          _saveNote = null;
          _incomingTargets = _loadTextTargets();
        }),
      );
    }
    return StreamBuilder<HandoffUiSnapshot>(
      stream: widget.controller.snapshots,
      initialData: widget.controller.current,
      builder: (context, snapshot) => _screenFor(snapshot.data ?? widget.controller.current),
    );
  }

  Widget _screenFor(HandoffUiSnapshot s) {
    switch (s.state) {
      case AppState.unpaired:
        return PairingScreen(onPair: _completePairing, error: s.pairingError, validate: _validateCode, onScan: _scanAndPair);
      case AppState.pairing:
        return PairingScreen(onPair: _completePairing, busy: true);
      case AppState.ready:
        return ReadyScreen(computerName: _desktop?.displayName ?? 'Rýchlik', onOpenComputer: _desktop == null ? null : _openTrusted);
      case AppState.receiving:
        return ReceivingScreen(
          state: s,
          onCancel: () {
            final id = s.handoffId;
            if (id != null) widget.controller.receiver.requestCancel(id);
          },
        );
      case AppState.verifying:
        return VerifyingScreen(state: s);
      case AppState.received:
      case AppState.choosingTarget:
        return ReceivedScreen(state: s, onChooseApp: _gatedChooseApp, onDiscard: _discard, trialRemaining: _entitlementStatus.unlocked ? null : _entitlementStatus.remainingTrialSends);
      case AppState.handoffAccepted:
        return HandoffAcceptedScreen(state: s, onDone: () => widget.controller.done(), onSendAgain: _gatedChooseApp);
      case AppState.cancelled:
        return CancelledScreen(onDone: () => widget.controller.done());
      case AppState.error:
        return ErrorScreen(state: s, onDone: () => widget.controller.done());
    }
  }
}
