import 'package:flutter/material.dart';

import '../../platform/share_targets.dart';
import '../theme/fs_theme.dart';
import '../widgets.dart';
import 'target_picker.dart';

/// Shown when another app shares text (usually a link) to FriendSend. Works without pairing: the installed apps
/// (social/messaging apps first, then your recent ones) are right here, so sharing is one tap.
class IncomingShareScreen extends StatelessWidget {
  const IncomingShareScreen({super.key, required this.text, required this.targets, required this.onPick, required this.onMoreApps, required this.onClose});

  final String text;
  final Future<List<ShareTarget>> targets;
  final void Function(ShareTarget) onPick;
  final VoidCallback onMoreApps;
  final VoidCallback onClose;

  static bool looksLikeLink(String text) {
    final t = text.trim();
    return (t.startsWith('http://') || t.startsWith('https://')) && !t.contains(RegExp(r'\s'));
  }

  @override
  Widget build(BuildContext context) {
    final isLink = looksLikeLink(text);
    final p = context.fs;
    return FsScreen(
      header: const FsAppHeader(),
      bottom: Column(mainAxisSize: MainAxisSize.min, children: [
        Material(
          color: p.surface2,
          borderRadius: BorderRadius.circular(FsRadius.medium),
          child: InkWell(
            key: const Key('incoming_more_apps'),
            borderRadius: BorderRadius.circular(FsRadius.medium),
            onTap: onMoreApps,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: FsSpace.s16, vertical: FsSpace.s16),
              child: Row(children: [
                Icon(Icons.apps, color: p.icon),
                const SizedBox(width: FsSpace.s12),
                Text('More apps…', style: FsText.body(context).copyWith(fontWeight: FontWeight.w600)),
                const SizedBox(width: FsSpace.s12),
                Expanded(child: Text('Open Android Sharesheet', style: FsText.caption(context), textAlign: TextAlign.end, overflow: TextOverflow.ellipsis)),
              ]),
            ),
          ),
        ),
        const SizedBox(height: FsSpace.s4),
        FsTextButton(key: const Key('incoming_close'), label: 'Close', onPressed: onClose),
      ]),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const SizedBox(height: FsSpace.s8),
        Text(isLink ? 'Share link' : 'Share text', key: const Key('incoming_title'), style: FsText.heading(context)),
        const SizedBox(height: FsSpace.s12),
        FsCard(
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(isLink ? Icons.link : Icons.notes, color: p.icon),
            const SizedBox(width: FsSpace.s12),
            Expanded(child: Text(text, key: const Key('incoming_text'), maxLines: 3, overflow: TextOverflow.ellipsis, style: FsText.body(context))),
          ]),
        ),
        const SizedBox(height: FsSpace.s16),
        Text('Send to', style: FsText.caption(context)),
        const SizedBox(height: FsSpace.s4),
        Expanded(
          child: FutureBuilder<List<ShareTarget>>(
            future: targets,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) return const Center(child: CircularProgressIndicator());
              final found = snap.data ?? const <ShareTarget>[];
              if (found.isEmpty) {
                return Padding(
                  padding: const EdgeInsets.only(top: FsSpace.s12),
                  child: Text('No apps were found. Use More apps… below.', key: const Key('incoming_no_targets'), style: FsText.muted(context)),
                );
              }
              return SingleChildScrollView(child: ShareTargetGrid(targets: found, onSelected: onPick));
            },
          ),
        ),
      ]),
    );
  }
}
