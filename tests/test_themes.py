import unittest

from zone_launcher.themes import THEMES, stylesheet


def luminance(colour: str) -> float:
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class ThemeTests(unittest.TestCase):
    def test_text_can_be_read_everywhere(self):
        for key, theme in THEMES.items():
            for name in ("background", "surface", "header"):
                with self.subTest(theme=key, on=name):
                    self.assertGreaterEqual(contrast(theme.text, getattr(theme, name)), 7)
            with self.subTest(theme=key, on="accent"):
                self.assertGreaterEqual(contrast(theme.accent_text, theme.accent), 4.5)

    def test_the_accent_stands_out(self):
        for key, theme in THEMES.items():
            with self.subTest(theme=key):
                self.assertGreaterEqual(contrast(theme.accent, theme.surface), 3)

    def test_dark_themes_are_dark_and_light_ones_light(self):
        for key, theme in THEMES.items():
            with self.subTest(theme=key):
                self.assertEqual(theme.dark, luminance(theme.background) < luminance(theme.text))

    def test_both_forms_of_stylesheet(self):
        zone = THEMES["zone"]
        self.assertIn("--window-bg-color: #1d1f17;", stylesheet(zone, variables=True))
        self.assertIn("@define-color window_bg_color #1d1f17;", stylesheet(zone, variables=False))
        self.assertIn("@define-color accent_fg_color #1a1700;", stylesheet(zone, variables=False))
        self.assertNotIn("@define-color", stylesheet(zone, variables=True))
        self.assertNotIn("--", stylesheet(zone, variables=False))


if __name__ == "__main__":
    unittest.main()
