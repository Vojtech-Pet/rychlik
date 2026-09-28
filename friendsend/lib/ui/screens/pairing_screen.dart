import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme/fs_theme.dart';
import '../widgets.dart';

class PairingScreen extends StatefulWidget {
  const PairingScreen({super.key, required this.onPair, this.error, this.busy = false, this.validate});

  final void Function(String code) onPair;

  /// Syntactic check of the pasted text (no network): returns a user-facing problem, or null when it looks like a
  /// usable pairing code. Pair stays disabled until it returns null.
  final String? Function(String code)? validate;
  final String? error;
  final bool busy;

  @override
  State<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends State<PairingScreen> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    if (data?.text != null && mounted) setState(() => _controller.text = data!.text!);
  }

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    final text = _controller.text.trim();
    final localError = text.isEmpty ? null : widget.validate?.call(text);
    final shownError = localError ?? (text.isEmpty ? widget.error : null);
    final canPair = text.isNotEmpty && localError == null;
    if (widget.busy) {
      return FsScreen(
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          const FsHero(icon: Icons.link, accent: true),
          const SizedBox(height: FsSpace.s24),
          Text('Pairing…', key: const Key('pairing_progress'), style: FsText.heading(context)),
          const SizedBox(height: FsSpace.s8),
          Text('Checking the code with Rýchlik.', style: FsText.muted(context), textAlign: TextAlign.center),
          const SizedBox(height: FsSpace.s24),
          const SizedBox(width: 200, child: FsProgressBar()),
        ]),
      );
    }
    return FsScreen(
      bottom: Column(mainAxisSize: MainAxisSize.min, children: [
        FsPrimaryButton(key: const Key('pairing_pair_button'), label: 'Pair', onPressed: canPair ? () => widget.onPair(text) : null),
        const SizedBox(height: FsSpace.s8),
        Text('Your paired desktop will be remembered.', style: FsText.caption(context)),
      ]),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const SizedBox(height: FsSpace.s16),
          Text('Connect to Rýchlik', key: const Key('unpaired_message'), style: FsText.heading(context)),
          const SizedBox(height: FsSpace.s8),
          Text('Pair this phone once with your desktop.', style: FsText.muted(context)),
          const SizedBox(height: FsSpace.s24),
          FsCard(
            child: Column(children: const [
              _Step(1, 'On your computer, open Rýchlik › Devices › Pair FriendSend.'),
              SizedBox(height: FsSpace.s12),
              _Step(2, 'Copy the pairing code.'),
              SizedBox(height: FsSpace.s12),
              _Step(3, 'Paste it here.'),
            ]),
          ),
          const SizedBox(height: FsSpace.s16),
          Container(
            padding: const EdgeInsets.fromLTRB(FsSpace.s16, FsSpace.s12, FsSpace.s12, FsSpace.s12),
            decoration: BoxDecoration(
              color: p.surface,
              borderRadius: BorderRadius.circular(FsRadius.large),
              border: Border.all(color: shownError != null ? p.error : p.border, width: shownError != null ? 1.5 : 1),
            ),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              TextField(
                key: const Key('pairing_paste_field'),
                controller: _controller,
                maxLines: 3,
                minLines: 2,
                onChanged: (_) => setState(() {}),
                style: FsText.body(context),
                decoration: InputDecoration(border: InputBorder.none, hintText: 'Paste code from Rýchlik', hintStyle: FsText.muted(context), isCollapsed: true),
              ),
              if (shownError != null)
                Padding(
                  padding: const EdgeInsets.only(top: FsSpace.s8),
                  child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Icon(Icons.warning_amber_rounded, size: 16, color: p.errorText),
                    const SizedBox(width: FsSpace.s6),
                    Expanded(child: Text(shownError, key: const Key('pairing_error'), style: FsText.caption(context, color: p.errorText))),
                  ]),
                ),
              const SizedBox(height: FsSpace.s8),
              Align(
                alignment: Alignment.centerRight,
                child: OutlinedButton.icon(
                key: const Key('pairing_paste_button'),
                onPressed: _paste,
                icon: const Icon(Icons.content_paste, size: 18),
                label: const Text('Paste'),
                style: OutlinedButton.styleFrom(foregroundColor: p.text, side: BorderSide(color: p.borderStrong), shape: const StadiumBorder(), minimumSize: const Size(0, 40)),
                ),
              ),
            ]),
          ),
        ]),
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step(this.n, this.text);

  final int n;
  final String text;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Container(
        width: 24,
        height: 24,
        alignment: Alignment.center,
        decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: p.accent)),
        child: Text('$n', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: p.accentText)),
      ),
      const SizedBox(width: FsSpace.s12),
      Expanded(child: Text(text, style: FsText.body(context))),
    ]);
  }
}
