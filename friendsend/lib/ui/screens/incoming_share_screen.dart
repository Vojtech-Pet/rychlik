import 'package:flutter/material.dart';

import '../theme/fs_theme.dart';
import '../widgets.dart';

/// Shown when another app shares text (usually a link) to FriendSend. Works without pairing: it only helps the
/// user pick the app that should receive the text next.
class IncomingShareScreen extends StatelessWidget {
  const IncomingShareScreen({super.key, required this.text, required this.onChooseApp, required this.onClose});

  final String text;
  final VoidCallback onChooseApp;
  final VoidCallback onClose;

  static bool looksLikeLink(String text) {
    final t = text.trim();
    return (t.startsWith('http://') || t.startsWith('https://')) && !t.contains(RegExp(r'\s'));
  }

  @override
  Widget build(BuildContext context) {
    final isLink = looksLikeLink(text);
    return FsScreen(
      header: const FsAppHeader(),
      bottom: Column(mainAxisSize: MainAxisSize.min, children: [
        FsPrimaryButton(key: const Key('incoming_choose_app'), label: 'Choose app', onPressed: onChooseApp),
        const SizedBox(height: FsSpace.s8),
        FsTextButton(key: const Key('incoming_close'), label: 'Close', onPressed: onClose),
      ]),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const SizedBox(height: FsSpace.s16),
          Text(isLink ? 'Share link' : 'Share text', key: const Key('incoming_title'), style: FsText.heading(context)),
          const SizedBox(height: FsSpace.s8),
          Text('Pick the app that should receive it.', style: FsText.muted(context)),
          const SizedBox(height: FsSpace.s24),
          FsCard(
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(isLink ? Icons.link : Icons.notes, color: context.fs.icon),
              const SizedBox(width: FsSpace.s12),
              Expanded(child: Text(text, key: const Key('incoming_text'), maxLines: 6, overflow: TextOverflow.ellipsis, style: FsText.body(context))),
            ]),
          ),
        ]),
      ),
    );
  }
}
