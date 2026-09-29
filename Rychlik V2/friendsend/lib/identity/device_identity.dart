import 'dart:convert';
import 'dart:io';
import 'dart:math';

/// This device's stable, non-secret identity (Prompt A14 §10-12).
///
/// Never derived from Android hardware identifiers (model, MAC, IMEI,
/// hostname) -- generated with a cryptographically secure random source
/// and persisted only in this app's private storage so it survives a
/// normal restart. `displayName` is presentation-only, never identity.
class DeviceIdentity {
  const DeviceIdentity({required this.deviceId, required this.displayName});

  final String deviceId;
  final String displayName;

  Map<String, dynamic> toJson() => {
    'device_id': deviceId,
    'display_name': displayName,
  };

  factory DeviceIdentity.fromJson(Map<String, dynamic> json) => DeviceIdentity(
    deviceId: json['device_id'] as String,
    displayName: json['display_name'] as String,
  );
}

/// Loads/creates the device identity under a caller-supplied private
/// directory. The Flutter app passes `getApplicationSupportDirectory()`;
/// the host cross-language test harness passes a directory of its own so
/// the same logic runs without any Flutter/platform dependency.
class DeviceIdentityStore {
  DeviceIdentityStore(this._directory, {String defaultDisplayName = 'FriendSend Android'})
    : _defaultDisplayName = defaultDisplayName;

  final Directory _directory;
  final String _defaultDisplayName;

  File get _file => File('${_directory.path}/friendsend_device_identity.json');

  /// Idempotent: returns the existing identity if present, otherwise
  /// generates and persists a fresh one. Never regenerates an existing
  /// device_id (identity must survive restart, §10).
  Future<DeviceIdentity> loadOrCreate() async {
    final file = _file;
    if (await file.exists()) {
      final raw = await file.readAsString();
      final json = jsonDecode(raw) as Map<String, dynamic>;
      return DeviceIdentity.fromJson(json);
    }
    final identity = DeviceIdentity(
      deviceId: _generateSecureDeviceId(),
      displayName: _defaultDisplayName,
    );
    await _directory.create(recursive: true);
    await file.writeAsString(jsonEncode(identity.toJson()));
    return identity;
  }

  static String _generateSecureDeviceId() {
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    return bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }
}
