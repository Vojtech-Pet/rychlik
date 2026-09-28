import 'dart:async';

import 'package:flutter/services.dart';

/// Text (typically a link) another app shared to FriendSend via Android's Share button.
abstract class IncomingShareSource {
  /// Text that arrived before Dart was listening (cold start), or null. Returns it once.
  Future<String?> takeInitial();

  /// Text that arrives while FriendSend is already running.
  Stream<String> get updates;
}

class NoIncomingShares implements IncomingShareSource {
  const NoIncomingShares();

  @override
  Future<String?> takeInitial() async => null;

  @override
  Stream<String> get updates => const Stream.empty();
}

/// Kotlin keeps the shared text until Dart pulls it (`takeIncomingText`), so neither a cold start nor a share that
/// arrives while the app is open can lose or duplicate it.
class PlatformIncomingShares implements IncomingShareSource {
  PlatformIncomingShares({MethodChannel? channel}) : _channel = channel ?? const MethodChannel('app.friendsend/incoming') {
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'incomingAvailable') {
        final text = await takeInitial();
        if (text != null) _controller.add(text);
      }
    });
  }

  final MethodChannel _channel;
  final StreamController<String> _controller = StreamController<String>.broadcast();

  @override
  Future<String?> takeInitial() async {
    try {
      return await _channel.invokeMethod<String>('takeIncomingText');
    } on MissingPluginException {
      return null;
    } on PlatformException {
      return null;
    }
  }

  @override
  Stream<String> get updates => _controller.stream;
}
