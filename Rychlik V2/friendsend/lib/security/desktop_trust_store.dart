import 'dart:convert';
import 'dart:io';

/// A desktop this FriendSend instance has completed pairing with (Prompt
/// A15 §20). No raw pairing secret is ever stored here.
class TrustedDesktop {
  const TrustedDesktop({
    required this.desktopInstanceId,
    required this.desktopPublicSigningKey,
    this.displayName,
    required this.pairedAtUtc,
  });

  final String desktopInstanceId;

  /// 32 raw Ed25519 public key bytes.
  final List<int> desktopPublicSigningKey;
  final String? displayName;
  final String pairedAtUtc;

  Map<String, dynamic> toJson() => {
    'desktop_instance_id': desktopInstanceId,
    'desktop_public_signing_key_b64': base64.encode(desktopPublicSigningKey),
    'display_name': displayName,
    'paired_at_utc': pairedAtUtc,
  };

  factory TrustedDesktop.fromJson(Map<String, dynamic> json) => TrustedDesktop(
    desktopInstanceId: json['desktop_instance_id'] as String,
    desktopPublicSigningKey: base64.decode(json['desktop_public_signing_key_b64'] as String),
    displayName: json['display_name'] as String?,
    pairedAtUtc: json['paired_at_utc'] as String,
  );
}

/// Persists trusted-desktop records separately from temporary handoff
/// data (Prompt A15 §20/§88). Explicit JSON with atomic replacement.
/// Designed to hold more than one trusted desktop (§75), even though the
/// A15 UI focuses on the common one-desktop case.
class DesktopTrustStore {
  DesktopTrustStore(this._directory);

  final Directory _directory;
  Map<String, TrustedDesktop>? _cache;

  File get _file => File('${_directory.path}/friendsend_trusted_desktops.json');

  Future<Map<String, TrustedDesktop>> _load() async {
    if (_cache != null) return _cache!;
    final file = _file;
    if (!await file.exists()) {
      _cache = {};
      return _cache!;
    }
    final raw = await file.readAsString();
    final json = jsonDecode(raw) as Map<String, dynamic>;
    final entries = (json['desktops'] as List<dynamic>? ?? [])
        .map((e) => TrustedDesktop.fromJson(e as Map<String, dynamic>));
    _cache = {for (final d in entries) d.desktopInstanceId: d};
    return _cache!;
  }

  Future<List<TrustedDesktop>> allDesktops() async => (await _load()).values.toList();

  Future<TrustedDesktop?> get(String desktopInstanceId) async => (await _load())[desktopInstanceId];

  /// The only trust-changing write path other than [forget] -- callers
  /// must never call this to silently overwrite an existing desktop's
  /// signing key (Prompt A15 §137/§61); a changed key requires an
  /// explicit forget + re-pair.
  Future<void> upsert(TrustedDesktop desktop) async {
    final desktops = await _load();
    desktops[desktop.desktopInstanceId] = desktop;
    await _saveAtomic(desktops);
  }

  Future<void> forget(String desktopInstanceId) async {
    final desktops = await _load();
    desktops.remove(desktopInstanceId);
    await _saveAtomic(desktops);
  }

  Future<void> _saveAtomic(Map<String, TrustedDesktop> desktops) async {
    await _directory.create(recursive: true);
    final payload = jsonEncode({'desktops': desktops.values.map((d) => d.toJson()).toList()});
    final tmp = File('${_file.path}.tmp');
    await tmp.writeAsString(payload);
    await tmp.rename(_file.path); // atomic replace (§88)
  }
}
