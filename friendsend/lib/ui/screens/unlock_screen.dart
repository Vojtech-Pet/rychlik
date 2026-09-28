import 'package:flutter/material.dart';

import '../../monetization/billing_adapter.dart';
import '../../monetization/entitlement_service.dart';
import '../../monetization/purchase_verifier.dart';
import '../theme/fs_theme.dart';
import '../widgets.dart';

/// Shown instead of the send flow once the 5 free sends are used. Owns the whole purchase/restore/verify flow
/// (billing -> [PurchaseVerifier] -> [EntitlementSource]) so it is never scattered across the app; [onResolved]
/// fires only once the resulting [EntitlementStatus] can actually send.
class UnlockScreen extends StatefulWidget {
  const UnlockScreen({
    super.key,
    required this.billing,
    required this.entitlement,
    required this.verifier,
    required this.onResolved,
    this.onNotNow,
  });

  final BillingAdapter billing;
  final EntitlementSource entitlement;
  final PurchaseVerifier verifier;
  final void Function(EntitlementStatus status) onResolved;
  final VoidCallback? onNotNow;

  @override
  State<UnlockScreen> createState() => _UnlockScreenState();
}

class _UnlockScreenState extends State<UnlockScreen> {
  String? _price;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.billing.lifetimeUnlockPrice().then((price) {
      if (mounted) setState(() => _price = price);
    });
  }

  Future<void> _settle(EntitlementStatus status, {required String rejectedMessage, required String pendingMessage}) async {
    if (!mounted) return;
    setState(() => _busy = false);
    if (status.canSend) {
      widget.onResolved(status);
    } else {
      setState(() => _error = status.state == EntitlementState.trial ? rejectedMessage : pendingMessage);
    }
  }

  Future<void> _buy() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final attempt = await widget.billing.purchaseLifetimeUnlock();
    if (!mounted) return;
    switch (attempt.outcome) {
      case PurchaseOutcome.purchased:
        final record = attempt.record!;
        final status = await widget.entitlement.applyPurchase(productId: record.productId, purchaseToken: record.purchaseToken, verifier: widget.verifier);
        await _settle(
          status,
          rejectedMessage: 'This purchase could not be verified. Contact support if you were charged.',
          pendingMessage: 'Purchase received. It’s being checked and will unlock automatically.',
        );
      case PurchaseOutcome.cancelled:
        setState(() => _busy = false);
      case PurchaseOutcome.failed:
        setState(() {
          _busy = false;
          _error = 'Couldn’t complete the purchase. Try again in a moment.';
        });
    }
  }

  Future<void> _restore() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final record = await widget.billing.restorePurchases();
    final status = await widget.entitlement.restoreFrom(record, verifier: widget.verifier);
    if (record == null) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = 'No previous purchase was found for this account.';
        });
      }
      return;
    }
    await _settle(
      status,
      rejectedMessage: 'This purchase could not be verified. Contact support if you were charged.',
      pendingMessage: 'Purchase found. It’s being checked and will unlock automatically.',
    );
  }

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    final label = _price == null ? 'Unlock FriendSend' : 'Unlock for $_price';
    return FsScreen(
      header: const FsAppHeader(),
      bottom: Column(mainAxisSize: MainAxisSize.min, children: [
        FsPrimaryButton(key: const Key('unlock_buy_button'), label: _busy ? 'Working…' : label, onPressed: _busy ? null : _buy),
        const SizedBox(height: FsSpace.s8),
        FsTextButton(key: const Key('unlock_restore_button'), label: 'Restore purchases', onPressed: _busy ? null : _restore),
        if (widget.onNotNow != null) ...[
          const SizedBox(height: FsSpace.s8),
          FsTextButton(key: const Key('unlock_not_now'), label: 'Not now', onPressed: _busy ? null : widget.onNotNow),
        ],
      ]),
      child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Icon(Icons.workspace_premium_outlined, size: 56, color: p.accent),
        const SizedBox(height: FsSpace.s16),
        Text('You’ve used your 5 free sends', key: const Key('unlock_title'), style: FsText.heading(context), textAlign: TextAlign.center),
        const SizedBox(height: FsSpace.s8),
        Text(
          'FriendSend keeps receiving files for free. Unlock once to keep sending — no ads, no subscription.\nAlready bought it on another install? Restore purchases below.',
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
