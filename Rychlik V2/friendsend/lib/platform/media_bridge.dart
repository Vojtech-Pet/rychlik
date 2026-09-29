import 'dart:async';

import 'package:flutter/services.dart';

/// A video downloaded into FriendSend's private cache, ready to be handed to another app.
class DownloadedVideo {
  const DownloadedVideo({required this.path, required this.displayName, required this.mimeType, required this.size});

  final String path;
  final String displayName;
  final String mimeType;
  final int size;
}

class VideoProgress {
  const VideoProgress(this.done, this.total);

  final int done;
  final int total; // 0 = unknown

  double? get fraction => total > 0 ? (done / total).clamp(0.0, 1.0) : null;
}

enum VideoOutcomeKind { done, cancelled, failed }

class VideoOutcome {
  const VideoOutcome.done(DownloadedVideo this.video) : kind = VideoOutcomeKind.done, message = null;
  const VideoOutcome.cancelled() : kind = VideoOutcomeKind.cancelled, video = null, message = null;
  const VideoOutcome.failed(String this.message) : kind = VideoOutcomeKind.failed, video = null;

  final VideoOutcomeKind kind;
  final DownloadedVideo? video;
  final String? message;
}

enum SaveOutcome { saved, unsupported, failed }

abstract class VideoFetcher {
  Stream<VideoProgress> get progress;

  Future<VideoOutcome> download(String url);

  Future<void> cancel();

  /// Copies the downloaded video into the phone's Movies/FriendSend folder (Gallery).
  Future<SaveOutcome> saveToPhone(DownloadedVideo video);
}

/// Production fetcher: yt-dlp running inside the app (Chaquopy), reached through a narrow method channel.
class PlatformVideoFetcher implements VideoFetcher {
  PlatformVideoFetcher({MethodChannel? channel}) : _channel = channel ?? const MethodChannel('app.friendsend/media') {
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'videoProgress') {
        final args = call.arguments;
        if (args is Map) _progress.add(VideoProgress((args['done'] as num?)?.toInt() ?? 0, (args['total'] as num?)?.toInt() ?? 0));
      }
    });
  }

  final MethodChannel _channel;
  final StreamController<VideoProgress> _progress = StreamController<VideoProgress>.broadcast();

  @override
  Stream<VideoProgress> get progress => _progress.stream;

  @override
  Future<VideoOutcome> download(String url) async {
    try {
      final raw = await _channel.invokeMapMethod<String, Object?>('downloadVideo', {'url': url});
      if (raw == null) return const VideoOutcome.failed('The video could not be downloaded.');
      if (raw['cancelled'] == true) return const VideoOutcome.cancelled();
      final error = raw['error'];
      if (error is String) return VideoOutcome.failed(error);
      final path = raw['path'];
      final name = raw['displayName'];
      final mime = raw['mimeType'];
      if (path is String && name is String && mime is String) {
        return VideoOutcome.done(DownloadedVideo(path: path, displayName: name, mimeType: mime, size: (raw['size'] as num?)?.toInt() ?? 0));
      }
      return const VideoOutcome.failed('The video could not be downloaded.');
    } on MissingPluginException {
      return const VideoOutcome.failed('Video download is not available here.');
    } on PlatformException {
      return const VideoOutcome.failed('The video could not be downloaded.');
    }
  }

  @override
  Future<SaveOutcome> saveToPhone(DownloadedVideo video) async {
    try {
      final result = await _channel.invokeMethod<String>('saveVideo', {'path': video.path, 'displayName': video.displayName, 'mimeType': video.mimeType});
      switch (result) {
        case 'SAVED':
          return SaveOutcome.saved;
        case 'UNSUPPORTED':
          return SaveOutcome.unsupported;
        default:
          return SaveOutcome.failed;
      }
    } on MissingPluginException {
      return SaveOutcome.failed;
    } on PlatformException {
      return SaveOutcome.failed;
    }
  }

  @override
  Future<void> cancel() async {
    try {
      await _channel.invokeMethod<void>('cancelVideoDownload');
    } on MissingPluginException {
      // nothing to cancel
    } on PlatformException {
      // best effort
    }
  }
}
