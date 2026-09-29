import 'dart:io';

/// The production receiver must accept connections from the desktop over the LAN. Binding to loopback (what the
/// server class defaults to, which is right for tests) makes it unreachable from any other device; only a real
/// phone on a real network exposes that. Security does not depend on the bind address: every connection still
/// needs the pinned TLS identity and a valid Ed25519 desktop signature.
InternetAddress get receiverBindAddress => InternetAddress.anyIPv4;

/// Picks the IPv4 address the desktop should use to reach this phone, from (interface name, address) pairs.
/// Prefers Wi-Fi / Ethernet-style interfaces; ignores loopback, link-local and mobile-data / VPN interfaces.
String? pickLanAddress(Iterable<(String name, String address)> candidates) {
  bool usable((String, String) c) {
    final a = c.$2;
    if (a.startsWith('127.') || a.startsWith('169.254.') || a == '0.0.0.0') return false;
    final n = c.$1.toLowerCase();
    return !(n.startsWith('rmnet') || n.startsWith('ccmni') || n.startsWith('tun') || n.startsWith('ppp') || n.startsWith('dummy'));
  }

  int rank((String, String) c) {
    final n = c.$1.toLowerCase();
    if (n.startsWith('wlan') || n.startsWith('wifi')) return 0;
    if (n.startsWith('eth') || n.startsWith('en')) return 1;
    return 2;
  }

  final usableList = candidates.where(usable).toList()..sort((a, b) => rank(a).compareTo(rank(b)));
  return usableList.isEmpty ? null : usableList.first.$2;
}

Future<String?> bestLanIpv4() async {
  try {
    final interfaces = await NetworkInterface.list(type: InternetAddressType.IPv4, includeLoopback: false, includeLinkLocal: false);
    return pickLanAddress([for (final i in interfaces) for (final a in i.addresses) (i.name, a.address)]);
  } on Object {
    return null;
  }
}
