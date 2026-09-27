import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';

import 'handoff/handoff_controller.dart';
import 'identity/device_identity.dart';
import 'pairing/pairing_manager.dart';
import 'platform/share_bridge.dart';
import 'receiver/receiver_server.dart';
import 'receiver/temp_cache.dart';
import 'ui/home_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final supportDir = await getApplicationSupportDirectory();
  final identityStore = DeviceIdentityStore(supportDir);
  final identity = await identityStore.loadOrCreate();

  final cacheDir = await getTemporaryDirectory();
  final tempCache = TempCache(Directory('${cacheDir.path}/friendsend'));

  final receiver = FriendSendReceiverServer(
    config: ReceiverConfig(deviceId: identity.deviceId, displayName: identity.displayName),
    tempCache: tempCache,
  );
  await receiver.start();

  final pairingManager = PairingManager(receiver: receiver);
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
  final PairingManager pairingManager;
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
