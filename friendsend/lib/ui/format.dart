/// Presentation formatting only; never invents data.
String formatBytes(int bytes) {
  if (bytes < 1024) return '$bytes B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  var value = bytes / 1024.0;
  var unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  final text = value >= 100 || unit == 0 && value >= 10 ? value.toStringAsFixed(0) : value.toStringAsFixed(1);
  return '${text.endsWith('.0') ? text.substring(0, text.length - 2) : text} ${units[unit]}';
}

String formatSpeed(double bytesPerSecond) => '${formatBytes(bytesPerSecond.round())}/s';

enum FileKind { video, audio, other }

FileKind fileKindOf(String? mimeType) {
  if (mimeType == null) return FileKind.other;
  if (mimeType.startsWith('video/')) return FileKind.video;
  if (mimeType.startsWith('audio/')) return FileKind.audio;
  return FileKind.other;
}

String readyTitle(FileKind kind) => switch (kind) {
  FileKind.video => 'Video ready',
  FileKind.audio => 'Audio ready',
  FileKind.other => 'File ready',
};
