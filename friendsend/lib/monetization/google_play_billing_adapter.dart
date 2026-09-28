import 'dart:async';

import 'package:in_app_purchase/in_app_purchase.dart';

import 'billing_adapter.dart';

/// The Play Console product id for the lifetime unlock. Must exist as a one-time (non-consumable) managed
/// product in the FriendSend app's Play Console listing before a real purchase can succeed -- creating that
/// product needs a Play Console account and is outside what this code can do.
const String lifetimeUnlockProductId = 'friendsend_lifetime_unlock';

/// Wraps `in_app_purchase` (Google Play Billing on Android). Returns a truthful [PurchaseOutcome] rather than
/// ever guessing success: [PurchaseOutcome.failed] covers "store unreachable", "product not configured yet" and
/// any billing error alike, since none of them should unlock the app.
class GooglePlayBillingAdapter implements BillingAdapter {
  GooglePlayBillingAdapter({InAppPurchase? purchases}) : _purchases = purchases ?? InAppPurchase.instance;

  final InAppPurchase _purchases;
  ProductDetails? _product;

  Future<ProductDetails?> _details() async {
    if (_product != null) return _product;
    if (!await _purchases.isAvailable()) return null;
    final response = await _purchases.queryProductDetails({lifetimeUnlockProductId});
    if (response.error != null || response.productDetails.isEmpty) return null;
    return _product = response.productDetails.first;
  }

  @override
  Future<String?> lifetimeUnlockPrice() async => (await _details())?.price;

  @override
  Future<PurchaseOutcome> purchaseLifetimeUnlock() async {
    final product = await _details();
    if (product == null) return PurchaseOutcome.failed;
    final stream = _purchases.purchaseStream;
    final result = Completer<PurchaseOutcome>();
    late final StreamSubscription<List<PurchaseDetails>> sub;
    sub = stream.listen((updates) {
      for (final purchase in updates) {
        if (purchase.productID != lifetimeUnlockProductId) continue;
        switch (purchase.status) {
          case PurchaseStatus.purchased:
          case PurchaseStatus.restored:
            if (purchase.pendingCompletePurchase) _purchases.completePurchase(purchase);
            if (!result.isCompleted) result.complete(PurchaseOutcome.purchased);
          case PurchaseStatus.canceled:
            if (!result.isCompleted) result.complete(PurchaseOutcome.cancelled);
          case PurchaseStatus.error:
            if (!result.isCompleted) result.complete(PurchaseOutcome.failed);
          case PurchaseStatus.pending:
            break;
        }
      }
    }, onError: (_) {
      if (!result.isCompleted) result.complete(PurchaseOutcome.failed);
    });
    try {
      final started = await _purchases.buyNonConsumable(purchaseParam: PurchaseParam(productDetails: product));
      if (!started && !result.isCompleted) return PurchaseOutcome.failed;
      return await result.future.timeout(const Duration(minutes: 5), onTimeout: () => PurchaseOutcome.failed);
    } finally {
      await sub.cancel();
    }
  }
}
