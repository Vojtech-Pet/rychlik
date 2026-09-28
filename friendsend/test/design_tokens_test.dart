import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:friendsend/ui/theme/fs_theme.dart';

Color _c(String hex) => Color(int.parse('FF${hex.substring(1)}', radix: 16));

void main() {
  final tokens = jsonDecode(File('../design/final_design_tokens.json').readAsStringSync()) as Map<String, dynamic>;

  for (final mode in ['dark', 'light']) {
    test('$mode palette equals design/final_design_tokens.json (no drift)', () {
      final c = tokens['color'][mode] as Map<String, dynamic>;
      final p = mode == 'dark' ? FsColors.dark : FsColors.light;
      expect(p.background, _c(c['surface']['background']));
      expect(p.surface, _c(c['surface']['primary']));
      expect(p.surface2, _c(c['surface']['secondary']));
      expect(p.elevated, _c(c['surface']['elevated']));
      expect(p.text, _c(c['text']['primary']));
      expect(p.text2, _c(c['text']['secondary']));
      expect(p.accent, _c(c['accent']['primary']));
      expect(p.accentText, _c(c['accent']['text']));
      expect(p.border, _c(c['border']['default']));
      expect(p.success, _c(c['status']['success']));
      expect(p.errorText, _c(c['status']['errorText']));
      expect(p.errorSolid, _c(c['status']['errorSolid']));
      expect(p.tintOpacity, c['accent']['tintOpacity']);
    });
  }

  test('mobile sizes, radii and type equal the token file', () {
    final s = tokens['size']['mobile'] as Map<String, dynamic>;
    expect(FsSize.touchTarget, s['touchTarget']);
    expect(FsSize.buttonHeight, s['buttonHeight']);
    expect(FsSize.screenPadding, s['screenPadding']);
    expect(FsRadius.large, tokens['radius']['mobile']['large']);
    expect(FsType.heading.size, tokens['typography']['mobile']['heading']['size']);
    expect(FsType.body.lineHeight, tokens['typography']['mobile']['body']['lineHeight']);
  });

  test('touch targets and primary button meet the 48dp minimum', () {
    expect(FsSize.touchTarget, greaterThanOrEqualTo(48));
    expect(FsSize.buttonHeight, greaterThanOrEqualTo(48));
  });

  test('theme builds for both brightness modes and exposes the palette', () {
    for (final b in Brightness.values) {
      final t = FsTheme.build(b);
      expect(t.extension<FsThemeExtension>()!.palette, b == Brightness.dark ? FsColors.dark : FsColors.light);
      expect(t.scaffoldBackgroundColor, (b == Brightness.dark ? FsColors.dark : FsColors.light).background);
    }
  });
}
