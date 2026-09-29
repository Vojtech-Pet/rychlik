/// The single Play Console product id for FriendSend's lifetime unlock. Every layer that needs it (the Google
/// Play Billing adapter, a future server verifier) imports this constant; it is never redefined elsewhere.
const String lifetimeUnlockProductId = 'friendsend_lifetime_unlock';
