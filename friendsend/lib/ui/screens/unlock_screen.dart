import 'package:flutter/material.dart';

import '../../monetization/billing_adapter.dart';
import '../theme/fs_theme.dart';
import '../widgets.dart';

/// Shown instead of the send flow once the 5 free sends are used. FriendSend keeps receiving files for free
/// forever; only *sending* needs the lifetime unlock.
class UnlockScreen extends StatefulWidget {
  const UnlockScreen({super.key, required this.billing, required this.onUnlocked, this.onNotNow});

  final BillingAdapter billing;
  final VoidCallback onUnlocked;
  final VoidCallback? onNotNow;

  @override
  State<UnlockScreen> createState() => _UnlockScreenState();
}

class _UnlockScreenState extends State<UnlockScreen> {
  String? _price;
  bool _buying = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.billing.lifetimeUnlockPrice().then((price) {
      if (mounted) setState(() => _price = price);
    });
  }

  Future<void> _buy() async {
    setState(() {
      _buying = true;
      _error = null;
    });
    final outcome = await widget.billing.purchaseLifetimeUnlock();
    if (!mounted) return;
    setState(() => _buying = false);
    switch (outcome) {
      case PurchaseOutcome.purchased:
        widget.onUnlocked();
      case PurchaseOutcome.cancelled:
        break; // the user changed their mind; no message needed
      case PurchaseOutcome.failed:
        setState(() => _error = 'Couldn’t complete the purchase. Try again in a moment.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    final label = _price == null ? 'Unlock FriendSend' : 'Unlock for $_price';
    return FsScreen(
      header: const FsAppHeader(),
      bottom: Column(mainAxisSize: MainAxisSize.min, children: [
        FsPrimaryButton(key: const Key('unlock_buy_button'), label: _buying ? 'Opening purchase…' : label, onPressed: _buying ? null : _buy),
        if (widget.onNotNow != null) ...[
          const SizedBox(height: FsSpace.s8),
          FsTextButton(key: const Key('unlock_not_now'), label: 'Not now', onPressed: _buying ? null : widget.onNotNow),
        ],
      ]),
      child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Icon(Icons.workspace_premium_outlined, size: 56, color: p.accent),
        const SizedBox(height: FsSpace.s16),
        Text('You’ve used your 5 free sends', key: const Key('unlock_title'), style: FsText.heading(context), textAlign: TextAlign.center),
        const SizedBox(height: FsSpace.s8),
        Text(
          'FriendSend keeps receiving files for free. Unlock once to keep sending — no ads, no subscription.',
          style: FsText.muted(context),
          textAlign: TextAlign.center,
        ),
        if (_error != null) ...[
          const SizedBox(height: FsSpace.s16),
          Text(_error!, key: const Key('unlock_error'), style: FsText.caption(context, color: p.errorText), textAlign: TextAlign.center),
        ],
      ]),
    );
  }
}

/// A small, truthful reminder on screens that can still send for free ("3 free sends remaining").
class TrialBadge extends StatelessWidget {
  const TrialBadge({super.key, required this.remaining});

  final int remaining;

  @override
  Widget build(BuildContext context) {
    return Text(
      remaining == 1 ? '1 free send remaining' : '$remaining free sends remaining',
      key: const Key('trial_remaining_badge'),
      style: FsText.caption(context),
    );
  }
}
