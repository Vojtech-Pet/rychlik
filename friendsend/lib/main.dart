import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';

import 'handoff/handoff_controller.dart';
import 'identity/device_identity.dart';
import 'platform/share_bridge.dart';
import 'receiver/receiver_server.dart' show ReceiverConfig;
import 'receiver/temp_cache.dart';
import 'security/auth_challenge.dart';
import 'security/desktop_trust_store.dart';
import 'security/mdns_advertiser.dart';
import 'security/secure_pairing_manager.dart';
import 'security/secure_receiver_server.dart';
import 'security/tls_identity.dart';
import 'ui/home_screen.dart';

/// Prompt A15: production Device Mode now defaults to the
/// `pinned-tls-signature-v1` security profile -- a real self-signed TLS
/// identity, real Ed25519-authenticated desktop challenges, and mDNS
/// advertisement, replacing A14's plain-HTTP + bearer-token receiver as
/// the app's actual runtime path. The old profile
/// (`FriendSendReceiverServer`/`PairingManager`) remains in the codebase
/// only for isolated tests/legacy fixtures (§3 of the A15 prompt) and is
/// never constructed here.
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final supportDir = await getApplicationSupportDirectory();
  final identityStore = DeviceIdentityStore(supportDir);
  final identity = await identityStore.loadOrCreate();

  final cacheDir = await getTemporaryDirectory();
  final tempCache = TempCache(Directory('${cacheDir.path}/friendsend'));

  final tlsIdentity = await TlsIdentityStore(supportDir, commonName: identity.deviceId).loadOrCreate();
  final trustStore = DesktopTrustStore(supportDir);
  final challengeManager = AuthChallengeManager(trustStore: trustStore);

  final receiver = SecureFriendSendReceiverServer(
    config: ReceiverConfig(deviceId: identity.deviceId, displayName: identity.displayName),
    tempCache: tempCache,
    tlsIdentity: tlsIdentity,
    authChallengeManager: challengeManager,
  );
  await receiver.start();

  // Best-effort mDNS advertisement (Prompt A15 §70): only while the
  // secure receiver is actually bound and accepting connections. A
  // failure here (e.g. no NsdManager platform implementation attached)
  // never blocks the receiver itself from working for an already-known
  // endpoint -- see docs/FRIENDSEND_ANDROID_SECURITY.md.
  final mdnsAdvertiser = MdnsAdvertiser();
  await mdnsAdvertiser.register(
    port: receiver.port,
    deviceId: identity.deviceId,
    protocolVersion: 1,
    securityProfile: 'pinned-tls-signature-v1',
  );

  final pairingManager = SecurePairingManager(
    deviceId: identity.deviceId,
    displayName: identity.displayName,
    friendSendTlsSpkiSha256: tlsIdentity.spkiSha256,
    friendSendEndpointHost: receiver.host,
    friendSendEndpointPort: receiver.port,
    trustStore: trustStore,
  );
  final controller = HandoffController(receiver: receiver, tempCache: tempCache, shareBridge: ShareBridge());
  await controller.runStartupCleanup();
  controller.startPeriodicCleanup();

  runApp(FriendSendApp(identity: identity, pairingManager: pairingManager, controller: controller));
}

class FriendSendApp extends StatelessWidget {
  const FriendSendApp({
    super.key,
    required this.identity,
    required this.pairingManager,
    required this.controller,
  });

  final DeviceIdentity identity;
  final SecurePairingManager pairingManager;
  final HandoffController controller;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'FriendSend',
      theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
      home: HomeScreen(identity: identity, pairingManager: pairingManager, controller: controller),
    );
  }
}
