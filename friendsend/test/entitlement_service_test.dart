import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/monetization/entitlement_service.dart';

void main() {
  late Directory root;

  setUp(() => root = Directory.systemTemp.createTempSync('fs_entitlement_'));
  tearDown(() {
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  test('fresh install gives 5 free sends and can send', () async {
    final service = EntitlementService(root);
    final status = await service.status();
    expect(status.unlocked, isFalse);
    expect(status.remainingTrialSends, 5);
    expect(status.canSend, isTrue);
  });

  test('a successful targeted send decrements by exactly one', () async {
    final service = EntitlementService(root);
    final status = await service.recordSuccessfulTargetedSend();
    expect(status.remainingTrialSends, 4);
    expect((await service.status()).remainingTrialSends, 4);
  });

  test('five successful sends exhaust the trial; a sixth never goes negative', () async {
    final service = EntitlementService(root);
    late EntitlementStatus status;
    for (var i = 0; i < 5; i++) {
      status = await service.recordSuccessfulTargetedSend();
    }
    expect(status.remainingTrialSends, 0);
    expect(status.canSend, isFalse);
    final sixth = await service.recordSuccessfulTargetedSend();
    expect(sixth.remainingTrialSends, 0); // never negative
  });

  test('unlock makes canSend true regardless of remaining count and stops counting', () async {
    final service = EntitlementService(root);
    await service.recordSuccessfulTargetedSend();
    await service.recordSuccessfulTargetedSend();
    final unlocked = await service.unlock();
    expect(unlocked.unlocked, isTrue);
    expect(unlocked.canSend, isTrue);
    final after = await service.recordSuccessfulTargetedSend(); // unlimited: does nothing to the counter
    expect(after.unlocked, isTrue);
  });

  test('state persists across a fresh EntitlementService instance (app / phone restart)', () async {
    await EntitlementService(root).recordSuccessfulTargetedSend();
    await EntitlementService(root).recordSuccessfulTargetedSend();
    final reopened = await EntitlementService(root).status();
    expect(reopened.remainingTrialSends, 3);

    await EntitlementService(root).unlock();
    final reopenedAfterUnlock = await EntitlementService(root).status();
    expect(reopenedAfterUnlock.unlocked, isTrue);
  });

  test('an unreadable or missing entitlement file is treated as a fresh trial, never a crash', () async {
    final file = File('${root.path}/entitlement.json');
    file.writeAsStringSync('{not json');
    final status = await EntitlementService(root).status();
    expect(status, isA<EntitlementStatus>());
    expect(status.remainingTrialSends, 5);
  });

  test('changes stream announces every mutation', () async {
    final service = EntitlementService(root);
    final events = <EntitlementStatus>[];
    final sub = service.changes.listen(events.add);
    await service.recordSuccessfulTargetedSend();
    await service.unlock();
    await Future<void>.delayed(Duration.zero);
    expect(events.length, 2);
    expect(events.last.unlocked, isTrue);
    await sub.cancel();
  });

  test('status() only ever reads the file once (cached) until a mutation', () async {
    final file = File('${root.path}/entitlement.json');
    final service = EntitlementService(root);
    await service.status();
    file.writeAsStringSync('{"unlocked": true, "successfulSends": 5}'); // changed on disk behind the service's back
    expect((await service.status()).unlocked, isFalse); // still the cached, pre-write value
  });

  test('AlwaysUnlockedEntitlement never blocks and never counts', () async {
    const service = AlwaysUnlockedEntitlement();
    final status = await service.status();
    expect(status.unlocked, isTrue);
    expect((await service.recordSuccessfulTargetedSend()).canSend, isTrue);
  });
}
