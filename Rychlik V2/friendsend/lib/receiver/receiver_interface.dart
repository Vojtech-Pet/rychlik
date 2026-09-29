import 'receiver_server.dart' show ReceiverEvent;

/// The minimal surface [HandoffController] needs from a receiver --
/// implemented by both the A14 [FriendSendReceiverServer] (legacy/dev
/// plain-HTTP profile, §3 of the A15 prompt) and the A15
/// `SecureFriendSendReceiverServer` (production `pinned-tls-signature-v1`
/// profile), so the same controller/UI code drives either without
/// knowing which one is live.
abstract class FriendSendReceiverLike {
  Stream<ReceiverEvent> get events;
  void requestCancel(String handoffId);
}
