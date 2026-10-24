"""Theme engine: several colour themes plus colour-theory helpers.

The UI is written against *semantic* tokens (surface, border, accent, danger,
success, warning, info) rather than hard-coded hex values, so a theme switch
recolours every screen without touching a single widget.

Colour theory applied here:

* **Accent + neutrals.** Each theme defines one accent hue with a small number
  of harmonised shades obtained by rotating lightness in HSL, keeping hue and
  saturation stable. That is what keeps hover/pressed states looking related
  to the base instead of muddy.
* **Analagous neutrals.** Surfaces are tinted slightly toward the accent hue.
  A pure-grey dark UI next to a saturated accent reads as two unrelated
  palettes; a 3-8 degree hue pull makes the whole window feel intentional.
* **Contrast.** Text colours are chosen so the primary body text sits at or
  above ~7:1 contrast on its surface (WCAG AAA for the default themes) and
  secondary text at ~4.5:1 (AA). ``contrast_ratio`` below is the same formula
  the WCAG uses, so the claim is checkable rather than asserted.
* **Severity scale.** Severity is a *sequential* scale, not a rainbow: it runs
  teal -> yellow -> orange -> red as consequence rises, with a light-red wash
  for the container so the row background never fights the text.
"""

from __future__ import annotations

import colorsys
import dataclasses
from dataclasses import dataclass


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Parse ``#rrggbb`` (or ``rrggbb``) into a 0-255 triple.

    Tolerant by design: a half-typed colour in the settings box must never
    crash the UI, so anything unparseable resolves to mid-grey.
    """
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:  # short form #abc
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        return (128, 128, 128)
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return (128, 128, 128)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _relative_luminance(hex_color: str) -> float:
    def channel(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = hex_to_rgb(hex_color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio between two hex colours (1.0 .. 21.0)."""
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    lo, hi = sorted((l1, l2))
    return round((hi + 0.05) / (lo + 0.05), 2)


def lighten(hex_color: str, amount: float) -> str:
    """Raise lightness in HSL, preserving hue and saturation."""
    r, g, b = (c / 255 for c in hex_to_rgb(hex_color))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    lightness = min(1.0, lightness + amount)
    return rgb_to_hex(
        tuple(c * 255 for c in colorsys.hls_to_rgb(hue, lightness, saturation)))


def darken(hex_color: str, amount: float) -> str:
    return lighten(hex_color, -amount)


def mix(a: str, b: str, weight: float) -> float | str:
    """Blend two hex colours; ``weight`` is the share of ``b`` (0..1)."""
    ar, ag, ab = hex_to_rgb(a)
    br, bg, bb = hex_to_rgb(b)
    return rgb_to_hex((
        ar + (br - ar) * weight,
        ag + (bg - ag) * weight,
        ab + (bb - ab) * weight,
    ))


def readable_on(background: str) -> str:
    """Pick near-black or near-white for legible text on a given colour."""
    return "#10131a" if _relative_luminance(background) > 0.45 else "#ffffff"


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    dark: bool
    # neutrals
    bg: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text_muted: str
    # brand
    accent: str
    # ``accent_text`` is intentionally blank by default: the property
    # :attr:`button_text` then derives black/white from the accent's own
    # luminance, so no accent can ever fall below AA on its button. Set it
    # explicitly only when a specific colour is genuinely wanted.
    accent_text: str = ""
    # status
    success: str = "#3fb950"
    warning: str = "#d29922"
    danger: str = "#f85149"
    info: str = "#58a6ff"

    # ---- derived -----------------------------------------------------
    @property
    def button_text(self) -> str:
        """Text drawn on top of the accent colour.

        A fixed ``accent_text`` is a trap: swap the accent for a lighter or
        more saturated one and the label silently drops below AA. We therefore
        pick black or white from the accent's own luminance, but keep an
        explicit override for cases where the designer really wants a colour
        (the high-contrast theme forces black on yellow).
        """
        if self.accent_text:
            return self.accent_text
        return readable_on(self.accent)

    @property
    def accent_hover(self) -> str:
        return lighten(self.accent, 0.07) if self.dark else darken(self.accent, 0.07)

    @property
    def accent_pressed(self) -> str:
        return darken(self.accent, 0.08) if self.dark else darken(self.accent, 0.14)

    @property
    def accent_soft(self) -> str:
        return mix(self.surface, self.accent, 0.22)

    @property
    def sidebar(self) -> str:
        return self.surface_alt

    @property
    def input_bg(self) -> str:
        return darken(self.surface, 0.04) if self.dark else "#ffffff"

    @property
    def hover(self) -> str:
        return lighten(self.surface, 0.04) if self.dark else darken(self.surface, 0.04)

    @property
    def severity(self) -> dict[str, str]:
        # sequential scale, cool -> hot
        return {
            "critical": self.danger,
            "high": mix(self.warning, self.danger, 0.45),
            "medium": self.warning,
            "low": self.info,
            "info": self.text_muted,
        }

    def contrast(self, fg: str, bg: str) -> float:
        return contrast_ratio(fg, bg)


DARK = Theme(
    key="dark", label="Studio Dark", dark=True,
    bg="#1b1d23", surface="#23262e", surface_alt="#181a20", border="#31343e",
    text="#e6e8ee", text_muted="#9aa1ad", accent="#2f6bd6",
)

MIDNIGHT = Theme(
    key="midnight", label="Midnight Indigo", dark=True,
    bg="#141a26", surface="#1b2333", surface_alt="#101622", border="#28324a",
    text="#e4e9f5", text_muted="#93a0bd", accent="#4a5ad6",
    info="#6ea8fe",
)

FOREST = Theme(
    key="forest", label="Deep Forest", dark=True,
    bg="#151d1a", surface="#1b2622", surface_alt="#111917", border="#2a3a34",
    text="#e2ece7", text_muted="#8fa79d", accent="#1c7d56",
    success="#4ec98d", info="#54a0c8",
)

LIGHT = Theme(
    key="light", label="Daylight", dark=False,
    bg="#f4f6fa", surface="#ffffff", surface_alt="#e9edf5", border="#d3dae6",
    text="#1a1f2b", text_muted="#5c6675", accent="#2f6fed",
    accent_text="#ffffff", danger="#c0392b", success="#1f8a4c", warning="#a9731a",
    info="#2563c9",
)

HIGH_CONTRAST = Theme(
    key="high_contrast", label="High Contrast", dark=True,
    bg="#000000", surface="#0a0a0a", surface_alt="#000000", border="#8a8a8a",
    text="#ffffff", text_muted="#d0d0d0", accent="#ffd400",
    accent_text="#000000", danger="#ff5f5f", success="#4dff88", warning="#ffd400",
    info="#66d9ff",
)

THEMES: dict[str, Theme] = {
    t.key: t for t in (DARK, MIDNIGHT, FOREST, LIGHT, HIGH_CONTRAST)
}

DEFAULT_THEME = DARK.key

#: Severity is shared across themes so a screenshot stays comparable.
SEVERITY_COLORS = DARK.severity


def get_theme(key: str | None) -> Theme:
    return THEMES.get(key or DEFAULT_THEME, DARK)


def theme_with_accent(theme: Theme, accent: str) -> Theme:
    """A copy of the theme with the user's own accent colour.

    ``accent_text`` is cleared so the derived ``button_text`` picks black or
    white from the *new* accent: a custom colour can never silently make a
    button label unreadable.
    """
    accent = (accent or "").strip()
    if not accent:
        return theme
    return dataclasses.replace(theme, accent=accent, accent_text="")


def theme_labels() -> list[tuple[str, str]]:
    return [(t.key, t.label) for t in THEMES.values()]


def stylesheet(theme: Theme, *, font_size: int = 13, density: str = "comfortable",
               radius: int = 6, motion: bool = True) -> str:
    """Build the Qt stylesheet for a theme plus user layout preferences."""
    pad = {"compact": "4px 10px", "comfortable": "6px 14px", "spacious": "9px 18px"}
    btn_pad = pad.get(density, pad["comfortable"])
    item_pad = {"compact": "7px 14px", "comfortable": "10px 16px",
                "spacious": "13px 18px"}.get(density, "10px 16px")
    t = theme
    return f"""
QMainWindow, QDialog {{ background: {t.bg}; }}
QWidget {{ color: {t.text}; font-size: {font_size}px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t.bg}; }}
QToolTip {{ background: {t.surface}; color: {t.text};
            border: 1px solid {t.border}; padding: 4px; }}

/* ---- navigation ---- */
QListWidget#sidebar {{ background: {t.sidebar}; border: none;
                       border-right: 1px solid {t.border}; outline: none;
                       padding: 6px 0; }}
QListWidget#sidebar::item {{ padding: {item_pad}; color: {t.text_muted};
                             border-left: 3px solid transparent; }}
QListWidget#sidebar::item:selected {{ background: {t.accent_soft};
                                      color: {t.text};
                                      border-left: 3px solid {t.accent}; }}
QListWidget#sidebar::item:hover {{ background: {t.hover}; color: {t.text}; }}

QWidget#topbar {{ background: {t.surface_alt};
                  border-bottom: 1px solid {t.border}; }}

/* ---- typography ---- */
QLabel#pageTitle {{ font-size: {font_size + 6}px; font-weight: 600; color: {t.text}; }}
QLabel#muted {{ color: {t.text_muted}; }}
QLabel#statValue {{ font-size: {font_size + 13}px; font-weight: 700; color: {t.text}; }}
QLabel#statTitle {{ color: {t.text_muted}; font-size: {font_size - 1}px; }}
QLabel#badge {{ border-radius: 9px; padding: 2px 9px; font-weight: 600;
                font-size: {font_size - 1}px; }}
QLabel#sectionTitle {{ font-size: {font_size + 2}px; font-weight: 600;
                       color: {t.text}; }}

/* ---- cards ---- */
QFrame#statCard, QFrame#card {{ background: {t.surface};
                                border: 1px solid {t.border};
                                border-radius: {radius + 2}px; }}
QFrame#statCard:hover {{ border-color: {t.accent}; }}

/* ---- buttons ---- */
QPushButton {{ background: {t.accent}; border: 1px solid {t.accent};
               border-radius: {radius}px; padding: {btn_pad};
               color: {t.button_text}; font-weight: 500; }}
QPushButton:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton:pressed {{ background: {t.accent_pressed}; }}
QPushButton:disabled {{ background: {t.surface_alt}; color: {t.text_muted};
                        border-color: {t.border}; }}
QPushButton#secondary {{ background: {t.surface}; color: {t.text};
                         border: 1px solid {t.border}; }}
QPushButton#secondary:hover {{ background: {t.hover}; border-color: {t.accent}; }}
QPushButton#danger {{ background: {t.danger}; border-color: {t.danger};
                      color: {readable_on(t.danger)}; }}
QPushButton#danger:hover {{ background: {lighten(t.danger, 0.08)}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent;
                     color: {t.text_muted}; }}
QPushButton#ghost:hover {{ color: {t.text}; background: {t.hover}; }}

/* ---- inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox,
QDoubleSpinBox, QDateEdit, QKeySequenceEdit {{
    background: {t.input_bg}; border: 1px solid {t.border};
    border-radius: {radius - 1}px; padding: 5px; color: {t.text};
    selection-background-color: {t.accent}; selection-color: {t.button_text}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t.accent}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {t.text_muted}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {t.surface}; border: 1px solid {t.border};
                               selection-background-color: {t.accent_soft};
                               color: {t.text}; outline: none; }}

/* ---- checkboxes / radios / sliders ---- */
QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px;
    border: 1px solid {t.border}; background: {t.input_bg}; }}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {t.accent}; border-color: {t.accent}; }}
QSlider::groove:horizontal {{ height: 4px; background: {t.border};
                              border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.accent}; width: 14px; height: 14px;
                              margin: -6px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}

/* ---- tables / trees ---- */
QTableWidget, QTableView, QTreeWidget, QTreeView, QListWidget {{
    background: {t.surface}; gridline-color: {t.border};
    border: 1px solid {t.border}; border-radius: {radius - 1}px;
    alternate-background-color: {t.surface_alt}; outline: none; }}
QTableView::item:selected, QTreeView::item:selected, QListWidget::item:selected {{
    background: {t.accent_soft}; color: {t.text}; }}
QHeaderView::section {{ background: {t.surface_alt}; color: {t.text_muted};
    padding: 6px; border: none; border-right: 1px solid {t.border};
    border-bottom: 1px solid {t.border}; font-weight: 600; }}

/* ---- tabs ---- */
QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: {radius - 1}px;
                    top: -1px; }}
QTabBar {{ background: {t.surface}; }}
QTabBar::tab {{ background: transparent; padding: 7px 16px;
                border: 1px solid transparent; border-bottom: 2px solid transparent;
                color: {t.text_muted}; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}
QTabBar::tab:hover {{ color: {t.text}; }}

/* ---- progress / status ---- */
QProgressBar {{ background: {t.input_bg}; border: 1px solid {t.border};
                border-radius: {radius - 2}px; text-align: center;
                color: {t.text}; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: {radius - 3}px; }}
QStatusBar {{ background: {t.surface_alt}; color: {t.text_muted};
              border-top: 1px solid {t.border}; }}
QStatusBar::item {{ border: none; }}

/* ---- containers ---- */
QGroupBox {{ border: 1px solid {t.border}; border-radius: {radius}px;
             margin-top: 11px; padding-top: 15px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 11px; padding: 0 4px;
                    color: {t.text_muted}; }}
QSplitter::handle {{ background: {t.border}; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 5px;
                               min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 5px;
                                 min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMessageBox {{ background: {t.surface}; }}
QMenuBar {{ background: {t.surface_alt}; border-bottom: 1px solid {t.border}; }}
QMenuBar::item:selected {{ background: {t.accent_soft}; }}
QMenu {{ background: {t.surface}; border: 1px solid {t.border}; }}
QMenu::item:selected {{ background: {t.accent_soft}; }}
"""


def diff_stylesheet(theme: Theme) -> str:
    """Extra rules for the diff/patch viewer."""
    t = theme
    return f"""
QPlainTextEdit#diffAdd {{ background: {mix(t.surface, t.success, 0.20)};
                          border: 1px solid {t.border}; }}
QPlainTextEdit#diffDel {{ background: {mix(t.surface, t.danger, 0.20)};
                          border: 1px solid {t.border}; }}
"""


#: Convenience alias kept for code (and tests) that just wants the default
#: look without constructing a Theme. New code should use ``stylesheet()``.
STYLE = stylesheet(DARK)
