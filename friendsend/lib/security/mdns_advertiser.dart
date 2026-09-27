import 'package:flutter/services.dart';

/// Thin bridge to Android's native `NsdManager` (Prompt A15 §54/§112-114).
///
/// Deliberately narrow -- two methods, no generic Android networking API
/// surface exposed to Dart. FriendSend advertises only non-secret
/// metadata (device_id, protocol_version, security_profile); never a
/// pairing secret, desktop key, or auth signature (§56).
///
/// **Known limitation (A15):** no Android emulator/physical device was
/// available to prove this bridge against a real `NsdManager` at
/// implementation time -- see docs/OPEN_VALIDATION_DEBT.md,
/// `A15-PHYSICAL-MDNS-DISCOVERY`. The desktop-side discovery half
/// (`rychlik.device.security.discovery`) is proven with a real
/// `zeroconf` advertiser in `tests/test_friendsend_discovery.py`.
enum MdnsAdvertiseResult { registered, failed }

class MdnsAdvertiser {
  MdnsAdvertiser({MethodChannel? channel}) : _channel = channel ?? const MethodChannel('app.friendsend/mdns');

  final MethodChannel _channel;

  /// Advertise while (and only while) the secure receiver is actually
  /// bound and accepting connections (§70) -- callers must call this
  /// only after `SecureFriendSendReceiverServer.start()` succeeds, and
  /// [unregister] before stopping the receiver.
  Future<MdnsAdvertiseResult> register({
    required int port,
    required String deviceId,
    required int protocolVersion,
    required String securityProfile,
  }) async {
    try {
      final result = await _channel.invokeMethod<String>('registerFriendSendService', {
        'port': port,
        'deviceId': deviceId,
        'protocolVersion': protocolVersion,
        'securityProfile': securityProfile,
      });
      return result == 'REGISTERED' ? MdnsAdvertiseResult.registered : MdnsAdvertiseResult.failed;
    } on MissingPluginException {
      // No Android platform implementation attached (host harness /
      // widget tests, or an environment without a real NsdManager) --
      // a bounded, honest failure, never a crash, and the Device Mode
      // UI must not claim discoverable-online status (§113).
      return MdnsAdvertiseResult.failed;
    } on PlatformException {
      return MdnsAdvertiseResult.failed;
    }
  }

  Future<void> unregister() async {
    try {
      await _channel.invokeMethod<void>('unregisterFriendSendService');
    } on MissingPluginException {
      // Nothing was ever registered -- fine.
    } on PlatformException {
      // Best-effort cleanup only.
    }
  }
}
