import 'dart:async';

import 'package:in_app_purchase/in_app_purchase.dart';

import 'billing_adapter.dart';
import 'entitlement_service.dart' show PurchaseRecord;
import 'product.dart';

/// Wraps `in_app_purchase` (Google Play Billing on Android). Never itself decides whether a purchase is real --
/// it only reports what the store said, using the purchase's real server-verifiable token
/// (`verificationData.serverVerificationData`), which is what a backend verifier needs later.
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

  static PurchaseRecord? _recordFor(PurchaseDetails purchase) {
    if (purchase.productID != lifetimeUnlockProductId) return null;
    final token = purchase.verificationData.serverVerificationData;
    if (token.isEmpty) return null;
    return PurchaseRecord(productId: purchase.productID, purchaseToken: token);
  }

  @override
  Future<String?> lifetimeUnlockPrice() async => (await _details())?.price;

  @override
  Future<PurchaseAttempt> purchaseLifetimeUnlock() async {
    final product = await _details();
    if (product == null) return const PurchaseAttempt.failed();
    final completer = Completer<PurchaseAttempt>();
    late final StreamSubscription<List<PurchaseDetails>> sub;
    sub = _purchases.purchaseStream.listen((updates) {
      for (final purchase in updates) {
        if (purchase.productID != lifetimeUnlockProductId) continue;
        switch (purchase.status) {
          case PurchaseStatus.purchased:
          case PurchaseStatus.restored:
            if (purchase.pendingCompletePurchase) _purchases.completePurchase(purchase);
            final record = _recordFor(purchase);
            if (!completer.isCompleted) completer.complete(record == null ? const PurchaseAttempt.failed() : PurchaseAttempt.purchased(record));
          case PurchaseStatus.canceled:
            if (!completer.isCompleted) completer.complete(const PurchaseAttempt.cancelled());
          case PurchaseStatus.error:
            if (!completer.isCompleted) completer.complete(const PurchaseAttempt.failed());
          case PurchaseStatus.pending:
            break;
        }
      }
    }, onError: (_) {
      if (!completer.isCompleted) completer.complete(const PurchaseAttempt.failed());
    });
    try {
      final started = await _purchases.buyNonConsumable(purchaseParam: PurchaseParam(productDetails: product));
      if (!started && !completer.isCompleted) return const PurchaseAttempt.failed();
      return await completer.future.timeout(const Duration(minutes: 5), onTimeout: () => const PurchaseAttempt.failed());
    } finally {
      await sub.cancel();
    }
  }

  @override
  Future<PurchaseRecord?> restorePurchases() async {
    if (!await _purchases.isAvailable()) return null;
    final completer = Completer<PurchaseRecord?>();
    late final StreamSubscription<List<PurchaseDetails>> sub;
    sub = _purchases.purchaseStream.listen((updates) {
      for (final purchase in updates) {
        if (purchase.status != PurchaseStatus.restored && purchase.status != PurchaseStatus.purchased) continue;
        final record = _recordFor(purchase);
        if (record == null) continue;
        if (purchase.pendingCompletePurchase) _purchases.completePurchase(purchase);
        if (!completer.isCompleted) completer.complete(record);
      }
    }, onError: (_) {
      if (!completer.isCompleted) completer.complete(null);
    });
    try {
      await _purchases.restorePurchases();
      return await completer.future.timeout(const Duration(seconds: 15), onTimeout: () => null);
    } finally {
      await sub.cancel();
    }
  }
}
