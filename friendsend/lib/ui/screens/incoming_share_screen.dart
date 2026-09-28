import 'package:flutter/material.dart';

import '../../platform/media_bridge.dart';
import '../../platform/share_targets.dart';
import '../format.dart';
import '../theme/fs_theme.dart';
import '../widgets.dart';
import 'target_picker.dart';

enum IncomingVideoPhase { idle, downloading, ready, failed }

/// What the screen shows about the optional "send the video itself" path.
class IncomingVideoUi {
  const IncomingVideoUi({this.available = false, this.phase = IncomingVideoPhase.idle, this.progress, this.video, this.error, this.saveNote, this.saving = false});

  final bool available; // a video fetcher exists and the shared text is a link
  final IncomingVideoPhase phase;
  final VideoProgress? progress;
  final DownloadedVideo? video;
  final String? error;
  final String? saveNote; // result of "Save to phone" (shown under the button); null until tried
  final bool saving;
}

/// Shown when another app shares text (usually a link) to FriendSend. Works without pairing: the installed apps
/// (social/messaging apps first, then your recent ones) are right here, so sharing is one tap.
class IncomingShareScreen extends StatelessWidget {
  const IncomingShareScreen({
    super.key,
    required this.text,
    required this.targets,
    required this.onPick,
    required this.onMoreApps,
    required this.onClose,
    this.video = const IncomingVideoUi(),
    this.onSendVideo,
    this.onCancelVideo,
    this.onBackToLink,
    this.onSaveVideo,
  });

  final String text;
  final Future<List<ShareTarget>> targets;
  final void Function(ShareTarget) onPick;
  final VoidCallback onMoreApps;
  final VoidCallback onClose;
  final IncomingVideoUi video;
  final VoidCallback? onSendVideo;
  final VoidCallback? onCancelVideo;
  final VoidCallback? onBackToLink;
  final VoidCallback? onSaveVideo;

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
        const SizedBox(height: FsSpace.s12),
        ..._videoSection(context),
        const SizedBox(height: FsSpace.s16),
        Text(video.phase == IncomingVideoPhase.ready ? 'Send video to' : 'Send to', style: FsText.caption(context)),
        const SizedBox(height: FsSpace.s4),
        Expanded(child: video.phase == IncomingVideoPhase.downloading ? const SizedBox.shrink() : _grid(context)),
      ]),
    );
  }

  List<Widget> _videoSection(BuildContext context) {
    if (!video.available) return const [];
    switch (video.phase) {
      case IncomingVideoPhase.idle:
      case IncomingVideoPhase.failed:
        return [
          if (video.phase == IncomingVideoPhase.failed && video.error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: FsSpace.s8),
              child: Text(video.error!, key: const Key('incoming_video_error'), style: FsText.caption(context, color: context.fs.errorText)),
            ),
          FsSecondaryButton(key: const Key('incoming_send_video'), label: video.phase == IncomingVideoPhase.failed ? 'Try the video again' : 'Send as video', onPressed: onSendVideo),
          const SizedBox(height: FsSpace.s4),
          Text('Downloads the video so it stays in the other app instead of opening this site.', style: FsText.caption(context)),
        ];
      case IncomingVideoPhase.downloading:
        return [
          FsCard(
            key: const Key('incoming_video_progress'),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Getting the video…', style: FsText.body(context).copyWith(fontWeight: FontWeight.w600)),
              const SizedBox(height: FsSpace.s8),
              LinearProgressIndicator(value: video.progress?.fraction),
              const SizedBox(height: FsSpace.s8),
              FsTextButton(key: const Key('incoming_video_cancel'), label: 'Send the link instead', onPressed: onCancelVideo),
            ]),
          ),
        ];
      case IncomingVideoPhase.ready:
        final v = video.video!;
        return [
          FsCard(
            key: const Key('incoming_video_ready'),
            child: Row(children: [
              Icon(Icons.movie_outlined, color: context.fs.icon),
              const SizedBox(width: FsSpace.s12),
              Expanded(child: Text('${v.displayName} · ${formatBytes(v.size)}', maxLines: 2, overflow: TextOverflow.ellipsis, style: FsText.body(context))),
            ]),
          ),
          Wrap(alignment: WrapAlignment.spaceBetween, crossAxisAlignment: WrapCrossAlignment.center, children: [
            FsTextButton(key: const Key('incoming_save_video'), label: video.saving ? 'Saving…' : 'Save to phone', onPressed: video.saving || video.saveNote == 'Saved to Movies/FriendSend' ? null : onSaveVideo),
            FsTextButton(key: const Key('incoming_back_to_link'), label: 'Send the link instead', onPressed: onBackToLink),
          ]),
          if (video.saveNote != null) Text(video.saveNote!, key: const Key('incoming_save_note'), style: FsText.caption(context)),
        ];
    }
  }

  Widget _grid(BuildContext context) {
    return FutureBuilder<List<ShareTarget>>(
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
    );
  }
}
