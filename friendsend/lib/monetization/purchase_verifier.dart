/// Whether a purchase token is genuine, kept separate from [BillingAdapter] so entitlement logic can be tested
/// with a fake verifier, without any real payment or network code running in tests.
enum VerificationResult { verified, rejected, temporaryFailure }

abstract class PurchaseVerifier {
  Future<VerificationResult> verify({required String productId, required String purchaseToken});
}

/// No verification backend has been built yet. Every check truthfully reports [VerificationResult.temporaryFailure]
/// -- never [VerificationResult.verified] -- so a real purchase can still grant grace-period access
/// (`EntitlementState.fullUnverified`) but this placeholder can never fabricate a permanent unlock. Replace with a
/// real implementation (an HTTP call to a backend that checks the purchase against the Play Developer API) before
/// relying on purchases in production.
class ServerPurchaseVerifier implements PurchaseVerifier {
  const ServerPurchaseVerifier();

  @override
  Future<VerificationResult> verify({required String productId, required String purchaseToken}) async => VerificationResult.temporaryFailure;
}
