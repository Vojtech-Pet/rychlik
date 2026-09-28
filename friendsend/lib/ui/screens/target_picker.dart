import 'package:flutter/material.dart';

import '../../platform/share_targets.dart';
import '../format.dart';
import '../theme/fs_theme.dart';
import '../widgets.dart';

enum PickerAction { target, systemSheet, discard }

class PickerResult {
  const PickerResult.target(ShareTarget this.target) : action = PickerAction.target;
  const PickerResult.systemSheet() : action = PickerAction.systemSheet, target = null;
  const PickerResult.discard() : action = PickerAction.discard, target = null;

  final PickerAction action;
  final ShareTarget? target;
}

/// "Where do you want to send it?" bottom sheet. Quick targets come from the platform resolver; the separate
/// "More apps… / Open Android Sharesheet" row is always present and never replaced by the custom list.
class TargetPickerSheet extends StatelessWidget {
  const TargetPickerSheet({super.key, required this.fileName, required this.sizeBytes, required this.mimeType, required this.targets, this.loading = false, this.allowDiscard = true});

  final String fileName;
  final int? sizeBytes; // null for shared text: no size is shown
  final String? mimeType;
  final List<ShareTarget> targets;
  final bool loading;
  final bool allowDiscard;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(FsSize.screenPadding, FsSpace.s8, FsSize.screenPadding, FsSpace.s12),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Center(child: Container(width: 40, height: 4, decoration: BoxDecoration(color: p.borderStrong, borderRadius: BorderRadius.circular(2)))),
          const SizedBox(height: FsSpace.s16),
          Text('Where do you want to send it?', key: const Key('picker_title'), style: FsText.title(context)),
          const SizedBox(height: FsSpace.s12),
          Row(children: [
            FsFileTile(kind: fileKindOf(mimeType), size: 36),
            const SizedBox(width: FsSpace.s12),
            Expanded(child: Text(sizeBytes == null ? fileName : '$fileName · ${formatBytes(sizeBytes!)}', style: FsText.body(context), maxLines: 1, overflow: TextOverflow.ellipsis)),
          ]),
          const SizedBox(height: FsSpace.s16),
          if (loading)
            const Padding(padding: EdgeInsets.symmetric(vertical: FsSpace.s16), child: Center(child: CircularProgressIndicator()))
          else if (targets.isEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: FsSpace.s12),
              child: Text(mimeType == 'text/plain' ? 'No apps were found. Use the Android Sharesheet below.' : 'No apps were found for this file. Use the Android Sharesheet below.', key: const Key('picker_no_targets'), style: FsText.muted(context)),
            )
          else
            _Grid(targets: targets),
          const SizedBox(height: FsSpace.s8),
          Material(
            color: p.surface2,
            borderRadius: BorderRadius.circular(FsRadius.medium),
            child: InkWell(
              key: const Key('picker_more_apps'),
              borderRadius: BorderRadius.circular(FsRadius.medium),
              onTap: () => Navigator.of(context).pop(const PickerResult.systemSheet()),
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
          const SizedBox(height: FsSpace.s8),
          Center(child: Text('Pick an app, then choose the person inside that app.', style: FsText.caption(context), textAlign: TextAlign.center)),
          if (allowDiscard) Center(child: FsTextButton(key: const Key('picker_discard'), label: 'Discard', destructive: true, onPressed: () => Navigator.of(context).pop(const PickerResult.discard()))),
        ]),
      ),
    );
  }
}

class _Grid extends StatelessWidget {
  const _Grid({required this.targets});

  final List<ShareTarget> targets;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final columns = (c.maxWidth / FsSize.shareTileWidth).floor().clamp(FsSize.shareTileMinColumns, 6);
      final width = c.maxWidth / columns;
      return Wrap(children: [
        for (final t in targets)
          SizedBox(
            width: width,
            height: FsSize.shareTileHeight,
            child: InkWell(
              key: Key('target_${t.id}'),
              borderRadius: BorderRadius.circular(FsRadius.medium),
              onTap: () => Navigator.of(context).pop(PickerResult.target(t)),
              child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                _TargetIcon(target: t),
                const SizedBox(height: FsSpace.s6),
                Padding(padding: const EdgeInsets.symmetric(horizontal: FsSpace.s4), child: Text(t.label, maxLines: 1, overflow: TextOverflow.ellipsis, style: FsText.caption(context, color: context.fs.text))),
              ]),
            ),
          ),
      ]);
    });
  }
}

class _TargetIcon extends StatelessWidget {
  const _TargetIcon({required this.target});

  final ShareTarget target;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    final size = FsSize.iconShareTile;
    if (target.icon != null) {
      return ClipRRect(borderRadius: BorderRadius.circular(FsRadius.medium), child: Image.memory(target.icon!, width: size, height: size, gaplessPlayback: true));
    }
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(color: p.surface2, borderRadius: BorderRadius.circular(FsRadius.medium)),
      child: Icon(Icons.apps, color: p.icon),
    );
  }
}
