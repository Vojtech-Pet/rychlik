/// Kept separate from [EntitlementService] on purpose: entitlement logic (counting, persistence, gating) is
/// testable with a fake billing provider, without any real payment code running in tests.
enum PurchaseOutcome { purchased, cancelled, failed }

abstract class BillingAdapter {
  /// The lifetime-unlock product's localised price, or null if the store hasn't answered yet / is unavailable.
  Future<String?> lifetimeUnlockPrice();

  Future<PurchaseOutcome> purchaseLifetimeUnlock();
}

/// No store attached (desktop test harness, or Play Billing not reachable). Purchases always fail with a
/// truthful reason -- this must never silently report success.
class UnavailableBillingAdapter implements BillingAdapter {
  const UnavailableBillingAdapter();

  @override
  Future<String?> lifetimeUnlockPrice() async => null;

  @override
  Future<PurchaseOutcome> purchaseLifetimeUnlock() async => PurchaseOutcome.failed;
}
