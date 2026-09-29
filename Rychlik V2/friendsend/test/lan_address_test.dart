import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/platform/lan_address.dart';

void main() {
  test('the production receiver binds all interfaces, never loopback only (a phone must be reachable over the LAN)', () {
    expect(receiverBindAddress, InternetAddress.anyIPv4);
    expect(receiverBindAddress.isLoopback, isFalse);
  });

  test('pickLanAddress prefers Wi-Fi, skips loopback/link-local/mobile/VPN interfaces', () {
    expect(pickLanAddress([('lo', '127.0.0.1'), ('rmnet_data0', '10.20.30.40'), ('wlan0', '192.168.123.50')]), '192.168.123.50');
    expect(pickLanAddress([('eth0', '10.0.0.5'), ('wlan0', '192.168.1.9')]), '192.168.1.9');
    expect(pickLanAddress([('tun0', '10.8.0.2'), ('wlan1', '169.254.3.3')]), isNull);
    expect(pickLanAddress(const []), isNull);
    expect(pickLanAddress([('wlan0', '0.0.0.0')]), isNull);
  });
}
