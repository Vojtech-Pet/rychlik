import 'dart:async';

import 'package:flutter/material.dart';

import '../handoff/handoff_controller.dart';
import '../identity/device_identity.dart';
import '../platform/incoming_share.dart';
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
  });

  static Widget _defaultScanner(BuildContext context) => const QrScanScreen();

  /// Builds the QR scanner route (replaced by a fake in widget tests, which have no camera).
  final Widget Function(BuildContext) scannerBuilder;

  /// Text other apps share to FriendSend (Android Share button), and how to hand it on to a chosen app.
  final IncomingShareSource incomingShares;
  final TextSharer? textSharer;

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
  bool _textPickerOpen = false;

  @override
  void initState() {
    super.initState();
    widget.incomingShares.takeInitial().then((text) {
      if (text != null && mounted) setState(() => _incomingText = text);
    });
    _incomingSub = widget.incomingShares.updates.listen((text) {
      if (mounted) setState(() => _incomingText = text); // the newest share replaces an older unfinished one
    });
    _sub = widget.controller.snapshots.listen(_onState);
    _loadDesktop();
    if (widget.controller.current.state == AppState.choosingTarget) _scheduleSheet();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _incomingSub?.cancel();
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
    if (result == TargetShareResult.opened || !mounted) return;
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

  Future<void> _showTextPicker() async {
    final text = _incomingText;
    final sharer = widget.textSharer;
    if (text == null || _textPickerOpen) return;
    _textPickerOpen = true;
    final targetsFuture = widget.targetProvider.targetsFor('text/plain');
    final preview = text.replaceAll(RegExp(r'\s+'), ' ').trim();
    final result = await showModalBottomSheet<PickerResult>(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.fs.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => FutureBuilder<List<ShareTarget>>(
        future: targetsFuture,
        builder: (_, snap) => TargetPickerSheet(
          fileName: preview,
          sizeBytes: null,
          mimeType: 'text/plain',
          targets: snap.data ?? const [],
          loading: snap.connectionState != ConnectionState.done,
          allowDiscard: false,
        ),
      ),
    );
    _textPickerOpen = false;
    if (!mounted || sharer == null || result == null) return; // dismissed: the text stays here, nothing is lost
    if (result.action == PickerAction.target) {
      final outcome = await sharer.shareTextToTarget(text: text, targetId: result.target!.id);
      if (!mounted) return;
      if (outcome == TargetShareResult.opened) {
        setState(() => _incomingText = null);
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(outcome == TargetShareResult.targetUnavailable ? '${result.target!.label} isn’t available any more. Pick another app.' : 'Couldn’t open ${result.target!.label}.'),
      ));
      if (outcome == TargetShareResult.targetUnavailable) await _showTextPicker();
    } else if (result.action == PickerAction.systemSheet) {
      final outcome = await sharer.shareText(text);
      if (!mounted) return;
      if (outcome == ShareResult.opened) {
        setState(() => _incomingText = null);
      } else {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Couldn’t open the Android Sharesheet.')));
      }
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
    final incoming = _incomingText;
    if (incoming != null) {
      return IncomingShareScreen(text: incoming, onChooseApp: _showTextPicker, onClose: () => setState(() => _incomingText = null));
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
        return ReceivedScreen(state: s, onChooseApp: widget.controller.chooseApp, onDiscard: _discard);
      case AppState.handoffAccepted:
        return HandoffAcceptedScreen(state: s, onDone: () => widget.controller.done(), onSendAgain: widget.controller.chooseApp);
      case AppState.cancelled:
        return CancelledScreen(onDone: () => widget.controller.done());
      case AppState.error:
        return ErrorScreen(state: s, onDone: () => widget.controller.done());
    }
  }
}
