import 'package:flutter/material.dart';

import 'format.dart';
import 'theme/fs_theme.dart';

/// Header: accent app tile + "FriendSend", optional trailing action.
class FsAppHeader extends StatelessWidget {
  const FsAppHeader({super.key, this.trailing, this.title = 'FriendSend', this.onBack});

  final Widget? trailing;
  final String title;
  final VoidCallback? onBack;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    return SizedBox(
      height: FsSize.appHeaderHeight,
      child: Row(
        children: [
          if (onBack != null)
            IconButton(key: const Key('header_back'), tooltip: 'Back', onPressed: onBack, icon: Icon(Icons.arrow_back, color: p.text))
          else
            Container(
              width: 30,
              height: 30,
              decoration: BoxDecoration(color: p.accent, borderRadius: BorderRadius.circular(FsRadius.small)),
              child: Icon(Icons.send_outlined, size: 18, color: p.onAccent),
            ),
          SizedBox(width: onBack != null ? FsSpace.s4 : FsSpace.s12),
          Expanded(child: Text(title, style: FsText.title(context).copyWith(fontSize: 20), overflow: TextOverflow.ellipsis)),
          ?trailing,
        ],
      ),
    );
  }
}

class FsScreen extends StatelessWidget {
  const FsScreen({super.key, required this.child, this.header, this.bottom});

  final Widget child;
  final Widget? header;
  final Widget? bottom;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: FsSize.screenPadding),
          child: Column(
            children: [
              header ?? const FsAppHeader(),
              Expanded(child: child),
              if (bottom != null) ...[bottom!, const SizedBox(height: FsSpace.s16)],
            ],
          ),
        ),
      ),
    );
  }
}

class FsPrimaryButton extends StatelessWidget {
  const FsPrimaryButton({super.key, required this.label, required this.onPressed, this.icon});

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    return SizedBox(
      width: double.infinity,
      height: FsSize.buttonHeight,
      child: FilledButton(
        onPressed: onPressed,
        style: FilledButton.styleFrom(
          backgroundColor: p.accent,
          foregroundColor: p.onAccent,
          disabledBackgroundColor: p.surface2,
          disabledForegroundColor: p.textDisabled,
          shape: const StadiumBorder(),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
        child: Row(mainAxisAlignment: MainAxisAlignment.center, mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[Icon(icon, size: FsSize.iconInline), const SizedBox(width: FsSpace.s8)],
          Flexible(child: Text(label, overflow: TextOverflow.ellipsis)),
        ]),
      ),
    );
  }
}

class FsSecondaryButton extends StatelessWidget {
  const FsSecondaryButton({super.key, required this.label, required this.onPressed, this.icon});

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    return SizedBox(
      width: double.infinity,
      height: FsSize.buttonHeight,
      child: OutlinedButton(
        onPressed: onPressed,
        style: OutlinedButton.styleFrom(
          foregroundColor: p.text,
          side: BorderSide(color: p.borderStrong),
          shape: const StadiumBorder(),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
        child: Row(mainAxisAlignment: MainAxisAlignment.center, mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[Icon(icon, size: FsSize.iconInline), const SizedBox(width: FsSpace.s8)],
          Flexible(child: Text(label, overflow: TextOverflow.ellipsis)),
        ]),
      ),
    );
  }
}

class FsTextButton extends StatelessWidget {
  const FsTextButton({super.key, required this.label, required this.onPressed, this.destructive = false, this.icon});

  final String label;
  final VoidCallback? onPressed;
  final bool destructive;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    final color = destructive ? p.errorText : p.accentText;
    return TextButton(
      onPressed: onPressed,
      style: TextButton.styleFrom(
        foregroundColor: color,
        minimumSize: const Size(FsSize.touchTarget, FsSize.touchTarget),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        if (icon != null) ...[Icon(icon, size: FsSize.iconInline), const SizedBox(width: FsSpace.s8)],
        Flexible(child: Text(label, overflow: TextOverflow.ellipsis)),
      ]),
    );
  }
}

enum FsTone { success, error, info, neutral }

Color toneColor(FsPalette p, FsTone tone) => switch (tone) {
  FsTone.success => p.successText,
  FsTone.error => p.errorText,
  FsTone.info => p.infoText,
  FsTone.neutral => p.text2,
};

class FsBadge extends StatelessWidget {
  const FsBadge({super.key, required this.label, this.tone = FsTone.success, this.icon = Icons.check_circle_outline});

  final String label;
  final FsTone tone;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final c = toneColor(context.fs, tone);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: FsSpace.s12, vertical: FsSpace.s4),
      decoration: BoxDecoration(borderRadius: BorderRadius.circular(FsRadius.pill), border: Border.all(color: c.withValues(alpha: 0.7))),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 14, color: c),
        const SizedBox(width: FsSpace.s6),
        Flexible(child: Text(label, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: c), overflow: TextOverflow.ellipsis)),
      ]),
    );
  }
}

/// Circular hero icon with the soft outer ring used on status screens.
class FsHero extends StatelessWidget {
  const FsHero({super.key, required this.icon, this.tone = FsTone.neutral, this.accent = false});

  final IconData icon;
  final FsTone tone;
  final bool accent;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    final c = accent ? p.accentText : toneColor(p, tone);
    return Container(
      width: 132,
      height: 132,
      alignment: Alignment.center,
      decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: c.withValues(alpha: 0.25))),
      child: Container(
        width: 104,
        height: 104,
        decoration: BoxDecoration(shape: BoxShape.circle, color: c.withValues(alpha: p.tintOpacity)),
        child: Icon(icon, size: FsSize.iconHero, color: c),
      ),
    );
  }
}

class FsFileTile extends StatelessWidget {
  const FsFileTile({super.key, required this.kind, this.size = 48});

  final FileKind kind;
  final double size;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    final icon = switch (kind) {
      FileKind.video => Icons.videocam_outlined,
      FileKind.audio => Icons.audiotrack_outlined,
      FileKind.other => Icons.insert_drive_file_outlined,
    };
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(size / 3.4),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [p.accentPressed, p.info]),
      ),
      child: Icon(icon, size: size * 0.5, color: Colors.white),
    );
  }
}

class FsCard extends StatelessWidget {
  const FsCard({super.key, required this.child, this.padding = const EdgeInsets.all(FsSpace.s16), this.onTap});

  final Widget child;
  final EdgeInsets padding;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    return Material(
      color: p.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(FsRadius.large), side: BorderSide(color: p.border)),
      child: InkWell(customBorder: RoundedRectangleBorder(borderRadius: BorderRadius.circular(FsRadius.large)), onTap: onTap, child: Padding(padding: padding, child: child)),
    );
  }
}

class FsProgressBar extends StatelessWidget {
  const FsProgressBar({super.key, this.value});

  /// null = indeterminate (never a made-up percentage).
  final double? value;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    return ClipRRect(
      borderRadius: BorderRadius.circular(FsRadius.pill),
      child: LinearProgressIndicator(value: value, minHeight: FsSize.progressHeight, color: p.info, backgroundColor: p.surface2),
    );
  }
}

/// Three-state row used on the Verifying screen.
enum FsStepState { done, active, pending }

class FsStepRow extends StatelessWidget {
  const FsStepRow({super.key, required this.label, required this.state});

  final String label;
  final FsStepState state;

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    final icon = switch (state) {
      FsStepState.done => Icon(Icons.check_circle, color: p.success, size: 24),
      FsStepState.active => SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2.5, color: p.info)),
      FsStepState.pending => Icon(Icons.radio_button_unchecked, color: p.textDisabled, size: 24),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: FsSpace.s8),
      child: Row(children: [
        icon,
        const SizedBox(width: FsSpace.s12),
        Expanded(
          child: Text(label, style: FsText.body(context, color: state == FsStepState.pending ? p.text2 : p.text).copyWith(fontWeight: state == FsStepState.active ? FontWeight.w600 : FontWeight.w400)),
        ),
      ]),
    );
  }
}
