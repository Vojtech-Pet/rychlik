import 'package:flutter/material.dart';

import '../handoff/handoff_controller.dart';
import '../identity/device_identity.dart';
import '../pairing/pairing_manager.dart';
import '../protocol/models.dart';

/// Minimal functional UI (Prompt A14 §16, §74-82, §148: no final design
/// polish -- this proves the workflow, not the brand).
class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.identity,
    required this.pairingManager,
    required this.controller,
  });

  final DeviceIdentity identity;
  final PairingManager pairingManager;
  final HandoffController controller;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _pasteController = TextEditingController();
  String? _pairingError;

  @override
  void dispose() {
    _pasteController.dispose();
    super.dispose();
  }

  void _completePairing() {
    try {
      final payload = widget.pairingManager.parse(_pasteController.text.trim());
      widget.pairingManager.completePairing(payload);
      setState(() => _pairingError = null);
      widget.controller.markPaired();
    } on ProtocolFormatException catch (e) {
      setState(() => _pairingError = e.message);
    } on PairingException catch (e) {
      setState(() => _pairingError = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('FriendSend')),
      body: StreamBuilder<HandoffUiSnapshot>(
        stream: widget.controller.snapshots,
        initialData: widget.controller.current,
        builder: (context, snapshot) {
          final state = snapshot.data ?? widget.controller.current;
          return Padding(padding: const EdgeInsets.all(16), child: _buildBody(state));
        },
      ),
    );
  }

  Widget _buildBody(HandoffUiSnapshot state) {
    switch (state.state) {
      case AppState.unpaired:
        return _pairingView();
      case AppState.paired:
        return _readyView();
      case AppState.receiving:
        return _receivingView(state);
      case AppState.received:
        return _receivedView(state);
      case AppState.error:
        return _errorView(state);
    }
  }

  Widget _pairingView() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        const Text('Pair FriendSend with Rýchlik to receive a file.', key: Key('unpaired_message')),
        const SizedBox(height: 12),
        TextField(
          key: const Key('pairing_paste_field'),
          controller: _pasteController,
          maxLines: 4,
          decoration: const InputDecoration(labelText: 'Paste pairing payload'),
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            ElevatedButton(
              key: const Key('pairing_paste_button'),
              onPressed: () {},
              child: const Text('Paste'),
            ),
            const SizedBox(width: 8),
            ElevatedButton(
              key: const Key('pairing_pair_button'),
              onPressed: _completePairing,
              child: const Text('Pair'),
            ),
          ],
        ),
        if (_pairingError != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              _pairingError!,
              key: const Key('pairing_error'),
              style: const TextStyle(color: Colors.red),
            ),
          ),
      ],
    );
  }

  Widget _readyView() {
    return Text(
      'Ready to receive on ${widget.identity.displayName}',
      key: const Key('ready_message'),
    );
  }

  Widget _receivingView(HandoffUiSnapshot state) {
    final fraction = state.progressFraction;
    final percent = fraction == null ? 0 : (fraction * 100).round();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text('Receiving...', key: const Key('receiving_message')),
        const SizedBox(height: 8),
        Text('$percent %', key: const Key('receiving_percent')),
        Text('${state.bytesReceived} / ${state.totalBytes} bytes'),
        const SizedBox(height: 8),
        LinearProgressIndicator(value: fraction),
        const SizedBox(height: 8),
        ElevatedButton(
          key: const Key('cancel_button'),
          onPressed: () {
            final id = state.handoffId;
            if (id != null) {
              widget.controller.receiver.requestCancel(id);
            }
          },
          child: const Text('Cancel'),
        ),
      ],
    );
  }

  Widget _receivedView(HandoffUiSnapshot state) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(state.displayName ?? 'Received file', key: const Key('received_filename')),
        Text('${state.bytesReceived} bytes'),
        const SizedBox(height: 8),
        Row(
          children: [
            ElevatedButton(key: const Key('share_button'), onPressed: () {}, child: const Text('Share')),
            const SizedBox(width: 8),
            ElevatedButton(key: const Key('discard_button'), onPressed: () {}, child: const Text('Discard')),
          ],
        ),
      ],
    );
  }

  Widget _errorView(HandoffUiSnapshot state) {
    return Text(
      'Error: ${state.errorCode?.wireName ?? 'unknown'}',
      key: const Key('error_message'),
      style: const TextStyle(color: Colors.red),
    );
  }
}
