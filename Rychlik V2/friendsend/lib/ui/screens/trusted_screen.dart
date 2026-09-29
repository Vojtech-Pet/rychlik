import 'package:flutter/material.dart';

import '../../security/desktop_trust_store.dart';
import '../theme/fs_theme.dart';
import '../widgets.dart';

String identityFingerprint(List<int> key) {
  final hex = key.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return hex.length <= 12 ? hex : '${hex.substring(0, 6)} ${hex.substring(6, 12)} …';
}

String formatPaired(String iso) {
  final t = DateTime.tryParse(iso)?.toLocal();
  if (t == null) return iso;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  String two(int n) => n.toString().padLeft(2, '0');
  return '${t.day} ${months[t.month - 1]} ${t.year}, ${two(t.hour)}:${two(t.minute)}';
}

class TrustedComputerScreen extends StatelessWidget {
  const TrustedComputerScreen({super.key, required this.desktop, required this.onForget});

  final TrustedDesktop desktop;
  final Future<void> Function() onForget;

  Future<void> _confirmForget(BuildContext context) async {
    final p = context.fs;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: p.elevated,
        key: const Key('forget_dialog'),
        title: Text('Forget this computer?', style: FsText.title(ctx)),
        content: Text('FriendSend will stop accepting files from Rýchlik until you pair again.', style: FsText.muted(ctx)),
        actions: [
          TextButton(key: const Key('forget_cancel'), onPressed: () => Navigator.pop(ctx, false), child: Text('Cancel', style: TextStyle(color: p.accentText))),
          TextButton(key: const Key('forget_confirm'), onPressed: () => Navigator.pop(ctx, true), child: Text('Forget', style: TextStyle(color: p.errorText, fontWeight: FontWeight.w600))),
        ],
      ),
    );
    if (ok == true) await onForget();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    return FsScreen(
      header: FsAppHeader(title: 'Trusted computer', onBack: () => Navigator.of(context).maybePop()),
      bottom: FsTextButton(key: const Key('forget_button'), label: 'Forget this computer', destructive: true, icon: Icons.delete_outline, onPressed: () => _confirmForget(context)),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const SizedBox(height: FsSpace.s8),
          FsCard(
            child: Row(children: [
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(color: p.accent.withValues(alpha: p.tintOpacity), borderRadius: BorderRadius.circular(FsRadius.medium)),
                child: Icon(Icons.desktop_windows_outlined, color: p.accentText),
              ),
              const SizedBox(width: FsSpace.s12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(desktop.displayName ?? 'Rýchlik', style: FsText.body(context).copyWith(fontWeight: FontWeight.w600)),
                  Text('Your computer', style: FsText.caption(context)),
                ]),
              ),
            ]),
          ),
          const SizedBox(height: FsSpace.s12),
          FsCard(
            padding: const EdgeInsets.symmetric(horizontal: FsSpace.s16),
            child: Column(children: [
              _Row('Status', const FsBadge(label: 'Trusted', icon: Icons.verified_user_outlined)),
              Divider(color: p.border, height: 1),
              _Row('Identity', Text(identityFingerprint(desktop.desktopPublicSigningKey), key: const Key('trusted_identity'), style: FsText.body(context).copyWith(letterSpacing: 1.2, fontFeatures: const [FontFeature.tabularFigures()]))),
              Divider(color: p.border, height: 1),
              _Row('Paired', Text(formatPaired(desktop.pairedAtUtc), key: const Key('trusted_paired'), style: FsText.body(context))),
            ]),
          ),
          const SizedBox(height: FsSpace.s8),
          Text('FriendSend only accepts files from computers you paired.', style: FsText.caption(context)),
        ]),
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row(this.label, this.value);

  final String label;
  final Widget value;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: FsSpace.s16),
    child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [Text(label, style: FsText.muted(context)), Flexible(child: value)]),
  );
}
