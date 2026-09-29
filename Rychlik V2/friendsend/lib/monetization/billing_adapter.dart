import 'entitlement_service.dart' show PurchaseRecord;

/// Kept separate from [EntitlementSource]/[PurchaseVerifier] on purpose: this only talks to the platform store
/// (Google Play Billing) to *attempt* a purchase or *find* an owned one. Whether either of those actually grants
/// access is decided by [EntitlementSource] after [PurchaseVerifier] checks it -- never here.
enum PurchaseOutcome { purchased, cancelled, failed }

class PurchaseAttempt {
  const PurchaseAttempt.purchased(PurchaseRecord this.record) : outcome = PurchaseOutcome.purchased;
  const PurchaseAttempt.cancelled()
      : outcome = PurchaseOutcome.cancelled,
        record = null;
  const PurchaseAttempt.failed()
      : outcome = PurchaseOutcome.failed,
        record = null;

  final PurchaseOutcome outcome;
  final PurchaseRecord? record;
}

abstract class BillingAdapter {
  /// The lifetime-unlock product's localised price, or null if the store hasn't answered yet / is unavailable.
  Future<String?> lifetimeUnlockPrice();

  Future<PurchaseAttempt> purchaseLifetimeUnlock();

  /// Whatever this account already owns for the lifetime unlock, or null if nothing is owned.
  Future<PurchaseRecord?> restorePurchases();
}

/// No store attached (desktop test harness, or Play Billing not reachable). Every call is truthfully "nothing
/// here" -- this must never silently report a purchase or an owned product.
class UnavailableBillingAdapter implements BillingAdapter {
  const UnavailableBillingAdapter();

  @override
  Future<String?> lifetimeUnlockPrice() async => null;

  @override
  Future<PurchaseAttempt> purchaseLifetimeUnlock() async => const PurchaseAttempt.failed();

  @override
  Future<PurchaseRecord?> restorePurchases() async => null;
}
