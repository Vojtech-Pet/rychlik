import 'dart:async';

import 'package:flutter/material.dart';

import '../handoff/handoff_controller.dart';
import '../identity/device_identity.dart';
import '../platform/share_bridge.dart';
import '../platform/share_targets.dart';
import '../security/desktop_trust_store.dart';
import '../security/secure_pairing_manager.dart';
import 'screens/pairing_screen.dart';
import 'screens/target_picker.dart';
import 'screens/transfer_screens.dart';
import 'screens/trusted_screen.dart';

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
  });

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

  @override
  void initState() {
    super.initState();
    _sub = widget.controller.snapshots.listen(_onState);
    _loadDesktop();
    if (widget.controller.current.state == AppState.choosingTarget) _scheduleSheet();
  }

  @override
  void dispose() {
    _sub?.cancel();
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
      backgroundColor: Theme.of(context).colorScheme.surface,
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
        widget.controller.cancelChoosing(); // targeted send is wired natively in the target-picker stage
      case null:
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

  Future<void> _discard() async {
    final s = widget.controller.current;
    if (s.handoffId != null && s.filePath != null) await widget.controller.discardCurrent(s.handoffId!, s.filePath!);
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
    return StreamBuilder<HandoffUiSnapshot>(
      stream: widget.controller.snapshots,
      initialData: widget.controller.current,
      builder: (context, snapshot) => _screenFor(snapshot.data ?? widget.controller.current),
    );
  }

  Widget _screenFor(HandoffUiSnapshot s) {
    switch (s.state) {
      case AppState.unpaired:
        return PairingScreen(onPair: _completePairing, error: s.pairingError);
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
