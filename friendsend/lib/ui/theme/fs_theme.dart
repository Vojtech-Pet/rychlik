import 'package:flutter/material.dart';

import 'tokens.g.dart';

export 'tokens.g.dart';

/// Approved FriendSend design (design/final_design_tokens.json -> tokens.g.dart). Roboto is the Android
/// system font and is not bundled or downloaded.
class FsTheme {
  static ThemeData build(Brightness brightness) {
    final p = brightness == Brightness.dark ? FsColors.dark : FsColors.light;
    final base = ColorScheme.fromSeed(seedColor: p.accent, brightness: brightness);
    final scheme = base.copyWith(
      primary: p.accent,
      onPrimary: p.onAccent,
      surface: p.background,
      onSurface: p.text,
      surfaceContainerHighest: p.surface2,
      outline: p.border,
      outlineVariant: p.border,
      error: p.error,
      onError: p.onAccent,
    );
    TextStyle style(FsTextSpec s, Color color) =>
        TextStyle(fontFamily: FsType.family, fontSize: s.size, height: s.height, fontWeight: _weight(s.weight), color: color);
    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: p.background,
      canvasColor: p.background,
      fontFamily: FsType.family,
      textTheme: TextTheme(
        headlineMedium: style(FsType.heading, p.text),
        titleLarge: style(FsType.title, p.text),
        bodyMedium: style(FsType.body, p.text),
        bodySmall: style(FsType.caption, p.text2),
      ),
      extensions: [FsThemeExtension(p)],
      appBarTheme: AppBarTheme(backgroundColor: p.background, foregroundColor: p.text, elevation: 0, scrolledUnderElevation: 0),
      snackBarTheme: SnackBarThemeData(backgroundColor: p.elevated, contentTextStyle: style(FsType.body, p.text)),
    );
  }

  static FontWeight _weight(int w) => w >= 600 ? FontWeight.w600 : (w >= 500 ? FontWeight.w500 : FontWeight.w400);
}

class FsThemeExtension extends ThemeExtension<FsThemeExtension> {
  const FsThemeExtension(this.palette);

  final FsPalette palette;

  @override
  FsThemeExtension copyWith({FsPalette? palette}) => FsThemeExtension(palette ?? this.palette);

  @override
  FsThemeExtension lerp(ThemeExtension<FsThemeExtension>? other, double t) => t < 0.5 ? this : (other as FsThemeExtension? ?? this);
}

extension FsContext on BuildContext {
  FsPalette get fs => Theme.of(this).extension<FsThemeExtension>()!.palette;
}

/// Small text helpers so screens do not hand-roll sizes.
class FsText {
  static TextStyle heading(BuildContext c) => TextStyle(fontSize: FsType.heading.size, height: FsType.heading.height, fontWeight: FontWeight.w600, color: c.fs.text);
  static TextStyle title(BuildContext c) => TextStyle(fontSize: FsType.title.size, height: FsType.title.height, fontWeight: FontWeight.w600, color: c.fs.text);
  static TextStyle body(BuildContext c, {Color? color}) => TextStyle(fontSize: FsType.body.size, height: FsType.body.height, color: color ?? c.fs.text);
  static TextStyle muted(BuildContext c) => TextStyle(fontSize: FsType.body.size, height: FsType.body.height, color: c.fs.text2);
  static TextStyle caption(BuildContext c, {Color? color}) => TextStyle(fontSize: FsType.caption.size, height: FsType.caption.height, color: color ?? c.fs.text2);
}
