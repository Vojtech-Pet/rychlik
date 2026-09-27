/// FriendSend Protocol v1 constants (wire contract only).
///
/// Mirrors docs/FRIENDSEND_PROTOCOL_V1.md exactly -- this file must never
/// drift from that document, and must never be derived from the Python
/// `rychlik.device` package's internal class shapes (Prompt A14 §5).
library;

/// The only protocol version this receiver understands. An offer or
/// stream request declaring a different version is rejected with
/// [HandoffErrorCode.unsupportedProtocol] before any payload byte is read.
const int friendSendProtocolVersion = 1;

const String headerProtocolVersion = 'X-FriendSend-Protocol-Version';
const String headerAuthToken = 'X-FriendSend-Auth-Token';
const String headerHandoffId = 'X-FriendSend-Handoff-Id';

/// Generic, never a per-social-app whitelist (protocol doc "Capability
/// exchange").
enum DeviceCapability {
  receiveStream('RECEIVE_STREAM'),
  temporaryFileHandoff('TEMPORARY_FILE_HANDOFF'),
  shareToOs('SHARE_TO_OS');

  const DeviceCapability(this.wireName);

  final String wireName;

  static DeviceCapability? fromWire(String name) {
    for (final value in DeviceCapability.values) {
      if (value.wireName == name) return value;
    }
    return null;
  }
}

/// Bounded taxonomy (protocol doc "Errors") -- never invent a code outside
/// this set.
enum HandoffErrorCode {
  unknownDevice('UNKNOWN_DEVICE'),
  unsupportedProtocol('UNSUPPORTED_PROTOCOL'),
  authenticationFailed('AUTHENTICATION_FAILED'),
  pairingExpired('PAIRING_EXPIRED'),
  unsupportedCapability('UNSUPPORTED_CAPABILITY'),
  unsupportedMedia('UNSUPPORTED_MEDIA'),
  payloadTooLarge('PAYLOAD_TOO_LARGE'),
  artifactUnavailable('ARTIFACT_UNAVAILABLE'),
  connectionFailed('CONNECTION_FAILED'),
  incompleteTransfer('INCOMPLETE_TRANSFER'),
  integrityMismatch('INTEGRITY_MISMATCH'),
  receiverRejected('RECEIVER_REJECTED'),
  cancelled('CANCELLED');

  const HandoffErrorCode(this.wireName);

  final String wireName;

  static HandoffErrorCode? fromWire(String? name) {
    if (name == null) return null;
    for (final value in HandoffErrorCode.values) {
      if (value.wireName == name) return value;
    }
    return null;
  }
}

/// Truthful, bounded acknowledgement states (protocol doc "Acknowledgement
/// meanings"). `handoffAccepted` is defined for forward reference to the
/// Android Sharesheet step but is only ever reachable in [rychlik.device]
/// / [HandoffController] once the platform share bridge actually succeeds
/// -- never merely because bytes were verified.
enum HandoffState {
  created('CREATED'),
  connecting('CONNECTING'),
  transferring('TRANSFERRING'),
  received('RECEIVED'),
  handoffAccepted('HANDOFF_ACCEPTED'),
  failed('FAILED'),
  cancelled('CANCELLED');

  const HandoffState(this.wireName);

  final String wireName;
}
