import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../theme/fs_theme.dart';

/// Full-screen camera view that returns the first QR payload it reads (pops with the text).
class QrScanScreen extends StatefulWidget {
  const QrScanScreen({super.key});

  @override
  State<QrScanScreen> createState() => _QrScanScreenState();
}

class _QrScanScreenState extends State<QrScanScreen> {
  final _scanner = MobileScannerController(formats: const [BarcodeFormat.qrCode], detectionSpeed: DetectionSpeed.noDuplicates);
  bool _done = false;

  @override
  void dispose() {
    _scanner.dispose();
    super.dispose();
  }

  void _onDetect(BarcodeCapture capture) {
    if (_done) return;
    for (final b in capture.barcodes) {
      final text = b.rawValue;
      if (text != null && text.isNotEmpty) {
        _done = true;
        Navigator.of(context).pop(text);
        return;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.fs;
    return Scaffold(
      appBar: AppBar(title: const Text('Scan QR code')),
      body: Stack(children: [
        MobileScanner(
          key: const Key('qr_scanner_view'),
          controller: _scanner,
          onDetect: _onDetect,
          errorBuilder: (context, error) => Center(
            child: Padding(
              padding: const EdgeInsets.all(FsSpace.s24),
              child: Text(
                error.errorCode == MobileScannerErrorCode.permissionDenied
                    ? 'Camera access is off. Allow the camera for FriendSend in Settings, or paste the code instead.'
                    : 'The camera is not available. Paste the code instead.',
                key: const Key('qr_scanner_error'),
                textAlign: TextAlign.center,
                style: FsText.body(context),
              ),
            ),
          ),
        ),
        Positioned(
          left: 0,
          right: 0,
          bottom: FsSpace.s24,
          child: Center(
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: FsSpace.s16, vertical: FsSpace.s8),
              decoration: BoxDecoration(color: p.surface, borderRadius: BorderRadius.circular(FsRadius.large)),
              child: Text('Point the camera at the code in Rýchlik', style: FsText.caption(context)),
            ),
          ),
        ),
      ]),
    );
  }
}
