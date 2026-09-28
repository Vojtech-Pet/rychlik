import 'package:flutter/material.dart';

import '../../handoff/handoff_controller.dart';
import '../../protocol/protocol.dart';
import '../format.dart';
import '../theme/fs_theme.dart';
import '../widgets.dart';

class ReadyScreen extends StatelessWidget {
  const ReadyScreen({super.key, required this.computerName, this.onOpenComputer});

  final String computerName;
  final VoidCallback? onOpenComputer;

  @override
  Widget build(BuildContext context) {
    return FsScreen(
      header: FsAppHeader(
        trailing: onOpenComputer == null
            ? null
            : IconButton(key: const Key('open_trusted_computer_icon'), tooltip: 'Trusted computer', onPressed: onOpenComputer, icon: Icon(Icons.desktop_windows_outlined, color: context.fs.icon)),
      ),
      bottom: onOpenComputer == null
          ? null
          : FsCard(
              key: const Key('trusted_computer_card'),
              onTap: onOpenComputer,
              padding: const EdgeInsets.all(FsSpace.s12),
              child: Row(children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(color: context.fs.accent.withValues(alpha: context.fs.tintOpacity), borderRadius: BorderRadius.circular(FsRadius.medium)),
                  child: Icon(Icons.desktop_windows_outlined, color: context.fs.accentText),
                ),
                const SizedBox(width: FsSpace.s12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(computerName, style: FsText.body(context).copyWith(fontWeight: FontWeight.w600), overflow: TextOverflow.ellipsis),
                    Row(children: [
                      Icon(Icons.verified_user_outlined, size: 14, color: context.fs.text2),
                      const SizedBox(width: FsSpace.s4),
                      Text('Trusted computer', style: FsText.caption(context)),
                    ]),
                  ]),
                ),
                Icon(Icons.chevron_right, color: context.fs.text2),
              ]),
            ),
      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
        const FsBadge(label: 'Connected to Rýchlik'),
        const SizedBox(height: FsSpace.s16),
        const FsHero(icon: Icons.file_download_outlined, accent: true),
        const SizedBox(height: FsSpace.s24),
        Text('Ready to receive', key: const Key('ready_message'), style: FsText.heading(context)),
        const SizedBox(height: FsSpace.s8),
        Text('Keep FriendSend open while you send a file from your computer.', style: FsText.muted(context), textAlign: TextAlign.center),
      ]),
    );
  }
}

String _fileTitle(HandoffUiSnapshot s) => s.displayName ?? 'Incoming file';

class ReceivingScreen extends StatelessWidget {
  const ReceivingScreen({super.key, required this.state, required this.onCancel});

  final HandoffUiSnapshot state;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) {
    final fraction = state.progressFraction;
    final percent = fraction == null ? null : (fraction * 100).floor();
    return FsScreen(
      bottom: FsSecondaryButton(key: const Key('cancel_button'), label: 'Cancel', onPressed: onCancel),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const SizedBox(height: FsSpace.s16),
          Text('Receiving from Rýchlik', key: const Key('receiving_message'), style: FsText.title(context)),
          const SizedBox(height: FsSpace.s16),
          FsCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                FsFileTile(kind: fileKindOf(state.mimeType)),
                const SizedBox(width: FsSpace.s12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(_fileTitle(state), key: const Key('receiving_filename'), style: FsText.body(context).copyWith(fontWeight: FontWeight.w600), maxLines: 2, overflow: TextOverflow.ellipsis),
                    if (state.totalBytes > 0) Text(formatBytes(state.totalBytes), style: FsText.caption(context)),
                  ]),
                ),
              ]),
              const SizedBox(height: FsSpace.s16),
              FsProgressBar(value: fraction),
              const SizedBox(height: FsSpace.s8),
              Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                Text(percent == null ? '' : '$percent %', key: const Key('receiving_percent'), style: FsText.body(context)),
                if (state.bytesPerSecond != null) Text(formatSpeed(state.bytesPerSecond!), key: const Key('receiving_speed'), style: FsText.caption(context)),
              ]),
              if (state.totalBytes > 0) Text('${formatBytes(state.bytesReceived)} of ${formatBytes(state.totalBytes)}', style: FsText.caption(context)),
            ]),
          ),
        ]),
      ),
    );
  }
}

class VerifyingScreen extends StatelessWidget {
  const VerifyingScreen({super.key, required this.state});

  final HandoffUiSnapshot state;

  @override
  Widget build(BuildContext context) {
    return FsScreen(
      bottom: Text('You can’t share the file until it has been verified.', style: FsText.caption(context), textAlign: TextAlign.center),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const SizedBox(height: FsSpace.s16),
          Text('File received', key: const Key('verifying_message'), style: FsText.title(context)),
          const SizedBox(height: FsSpace.s4),
          Text('Checking that the file is complete and unchanged.', style: FsText.muted(context)),
          const SizedBox(height: FsSpace.s16),
          FsCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                FsFileTile(kind: fileKindOf(state.mimeType)),
                const SizedBox(width: FsSpace.s12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(_fileTitle(state), style: FsText.body(context).copyWith(fontWeight: FontWeight.w600), maxLines: 2, overflow: TextOverflow.ellipsis),
                    if (state.totalBytes > 0) Text(formatBytes(state.totalBytes), style: FsText.caption(context)),
                  ]),
                ),
              ]),
              const SizedBox(height: FsSpace.s16),
              const FsProgressBar(),
            ]),
          ),
          const SizedBox(height: FsSpace.s16),
          const FsStepRow(label: 'Received', state: FsStepState.done),
          const FsStepRow(label: 'Verifying file', state: FsStepState.active),
          const FsStepRow(label: 'Getting ready to share', state: FsStepState.pending),
        ]),
      ),
    );
  }
}

class ReceivedScreen extends StatelessWidget {
  const ReceivedScreen({super.key, required this.state, required this.onChooseApp, required this.onDiscard});

  final HandoffUiSnapshot state;
  final VoidCallback onChooseApp;
  final VoidCallback onDiscard;

  @override
  Widget build(BuildContext context) {
    final kind = fileKindOf(state.mimeType);
    return FsScreen(
      bottom: Column(mainAxisSize: MainAxisSize.min, children: [
        FsPrimaryButton(key: const Key('choose_app_button'), label: 'Choose app', icon: Icons.send_outlined, onPressed: onChooseApp),
        FsTextButton(key: const Key('discard_button'), label: 'Discard', destructive: true, onPressed: onDiscard),
        Text('The file is kept only until you share or discard it.', style: FsText.caption(context), textAlign: TextAlign.center),
      ]),
      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
        const FsBadge(label: 'Verified'),
        const SizedBox(height: FsSpace.s24),
        FsFileTile(kind: kind, size: 120),
        const SizedBox(height: FsSpace.s24),
        Text(readyTitle(kind), style: FsText.heading(context)),
        const SizedBox(height: FsSpace.s12),
        Text(state.displayName ?? 'Received file', key: const Key('received_filename'), style: FsText.body(context).copyWith(fontWeight: FontWeight.w600), textAlign: TextAlign.center, maxLines: 2, overflow: TextOverflow.ellipsis),
        Text(formatBytes(state.totalBytes > 0 ? state.totalBytes : state.bytesReceived), key: const Key('received_size'), style: FsText.caption(context)),
      ]),
    );
  }
}

class HandoffAcceptedScreen extends StatelessWidget {
  const HandoffAcceptedScreen({super.key, required this.state, required this.onDone, required this.onSendAgain});

  final HandoffUiSnapshot state;
  final VoidCallback onDone;
  final VoidCallback onSendAgain;

  @override
  Widget build(BuildContext context) {
    final label = state.targetLabel;
    return FsScreen(
      bottom: Column(mainAxisSize: MainAxisSize.min, children: [
        FsPrimaryButton(key: const Key('done_button'), label: 'Done', onPressed: onDone),
        FsTextButton(key: const Key('send_another_button'), label: 'Send with another app', onPressed: onSendAgain),
        Text('The temporary copy is removed when you tap Done, or automatically after a while.', style: FsText.caption(context), textAlign: TextAlign.center),
      ]),
      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
        const FsHero(icon: Icons.send_outlined, tone: FsTone.success),
        const SizedBox(height: FsSpace.s24),
        Text(label == null ? 'Android Sharesheet opened' : 'Handed off to $label', key: const Key('handoff_accepted_message'), style: FsText.heading(context), textAlign: TextAlign.center),
        const SizedBox(height: FsSpace.s12),
        Text(
          label == null
              ? 'Choose an app and a person there. FriendSend can’t see whether it was delivered.'
              : 'Choose who to send it to inside $label. FriendSend can’t see whether it was delivered.',
          style: FsText.muted(context),
          textAlign: TextAlign.center,
        ),
      ]),
    );
  }
}

class CancelledScreen extends StatelessWidget {
  const CancelledScreen({super.key, required this.onDone});

  final VoidCallback onDone;

  @override
  Widget build(BuildContext context) {
    return FsScreen(
      bottom: FsPrimaryButton(key: const Key('done_button'), label: 'Done', onPressed: onDone),
      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
        const FsHero(icon: Icons.cancel_outlined),
        const SizedBox(height: FsSpace.s24),
        Text('Transfer cancelled', key: const Key('cancelled_message'), style: FsText.heading(context)),
        const SizedBox(height: FsSpace.s8),
        Text('No complete file was saved.', style: FsText.muted(context), textAlign: TextAlign.center),
      ]),
    );
  }
}

class ErrorCopy {
  const ErrorCopy(this.title, this.text);
  final String title;
  final String text;
}

/// Plain wording only -- no wire codes, no delivery/success claims.
ErrorCopy errorCopy(HandoffErrorCode? code) => switch (code) {
  HandoffErrorCode.integrityMismatch => const ErrorCopy('File verification failed', 'The received file did not match the file sent by Rýchlik and was discarded.'),
  HandoffErrorCode.incompleteTransfer || HandoffErrorCode.connectionFailed => const ErrorCopy('Transfer interrupted', 'The connection was lost before the whole file arrived. No file was saved.'),
  HandoffErrorCode.payloadTooLarge => const ErrorCopy('File is too large', 'This phone can’t receive a file this big. No file was saved.'),
  HandoffErrorCode.untrustedDesktop || HandoffErrorCode.authenticationFailed || HandoffErrorCode.authReplay || HandoffErrorCode.challengeExpired || HandoffErrorCode.unknownDevice =>
    const ErrorCopy('Couldn’t confirm the computer', 'FriendSend only accepts files from a computer you paired. Try again, or pair this phone again.'),
  HandoffErrorCode.tlsPinMismatch || HandoffErrorCode.deviceIdentityMismatch => const ErrorCopy('Computer identity changed', 'This is not the computer you paired with, so the file was not accepted.'),
  _ => const ErrorCopy('Couldn’t receive the file', 'Nothing was saved. Try sending it again from Rýchlik.'),
};

class ErrorScreen extends StatelessWidget {
  const ErrorScreen({super.key, required this.state, required this.onDone});

  final HandoffUiSnapshot state;
  final VoidCallback onDone;

  @override
  Widget build(BuildContext context) {
    final copy = errorCopy(state.errorCode);
    return FsScreen(
      bottom: FsPrimaryButton(key: const Key('done_button'), label: 'Done', onPressed: onDone),
      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
        const FsHero(icon: Icons.shield_outlined, tone: FsTone.error),
        const SizedBox(height: FsSpace.s24),
        Text(copy.title, key: const Key('error_message'), style: FsText.heading(context), textAlign: TextAlign.center),
        const SizedBox(height: FsSpace.s8),
        Text(copy.text, style: FsText.muted(context), textAlign: TextAlign.center),
      ]),
    );
  }
}
