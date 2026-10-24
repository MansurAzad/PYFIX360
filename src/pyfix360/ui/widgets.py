"""Reusable UI widgets and helpers.

Layouts here are built to stay usable from a small laptop window up to a
large monitor: every page puts its content in a vertical scroll area, header
rows wrap, and tables scroll horizontally rather than clipping. Nothing uses
fixed pixel widths for content (only the sidebar, which is user-adjustable).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyfix360.ui.theme import DARK, Theme

_CURRENT_THEME: Theme = DARK


def set_current_theme(theme: Theme) -> None:
    """Called by the main window when the user changes theme/mode."""
    global _CURRENT_THEME
    _CURRENT_THEME = theme


def current_theme() -> Theme:
    return _CURRENT_THEME


def page_header(title: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    lbl = QLabel(title)
    lbl.setObjectName("pageTitle")
    lay.addWidget(lbl)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
    return w


class StatCard(QFrame):
    def __init__(self, title: str, value: str = "—") -> None:
        super().__init__()
        self.setObjectName("statCard")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("statValue")
        title_label = QLabel(title)
        title_label.setObjectName("statTitle")
        title_label.setWordWrap(True)
        lay.addWidget(self.value_label)
        lay.addWidget(title_label)

    def set_value(self, value: str | int) -> None:
        self.value_label.setText(str(value))


class SeverityBadge(QLabel):
    """A coloured pill for a severity or status value."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setObjectName("badge")
        self.setAlignment(Qt.AlignCenter)
        if text:
            self.set_value(text)

    def set_value(self, value: str) -> None:
        self.setText(value)
        color = _CURRENT_THEME.severity.get(str(value).lower(),
                                            _CURRENT_THEME.text_muted)
        self.setStyleSheet(
            f"background:{color}22; color:{color}; border:1px solid {color}66;"
            "border-radius:9px; padding:2px 9px; font-weight:600;")


class Card(QFrame):
    """A titled content container with a border that follows the theme."""

    def __init__(self, title: str = "", subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        if title:
            lbl = QLabel(title)
            lbl.setObjectName("sectionTitle")
            self.body.addWidget(lbl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("muted")
            sub.setWordWrap(True)
            self.body.addWidget(sub)

    def add(self, widget: QWidget) -> None:
        self.body.addWidget(widget)


def scrollable(inner: QWidget) -> QScrollArea:
    """Wrap a page so it stays usable at any window size."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWidget(inner)
    return area


def make_page(*widgets: QWidget, spacing: int = 12) -> QScrollArea:
    """Standard page scaffold: a scrolling column of widgets."""
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(24, 20, 24, 20)
    lay.setSpacing(spacing)
    for widget in widgets:
        lay.addWidget(widget)
    lay.addStretch(1)
    return scrollable(inner)


def make_button(text: str, on_click: Callable, *, danger: bool = False,
                secondary: bool = False, ghost: bool = False) -> QPushButton:
    btn = QPushButton(text)
    if danger:
        btn.setObjectName("danger")
    elif secondary:
        btn.setObjectName("secondary")
    elif ghost:
        btn.setObjectName("ghost")
    btn.clicked.connect(on_click)
    return btn


def make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.setSortingEnabled(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setWordWrap(False)
    return table


def fill_table(table: QTableWidget, rows: list[list], *,
               color_col: int | None = None,
               colors: dict[str, str] | None = None,
               mono_cols: tuple[int, ...] = (),
               align_right: tuple[int, ...] = ()) -> None:
    table.setSortingEnabled(False)
    table.setRowCount(0)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            if color_col is not None and c == color_col and colors:
                key = str(value).lower()
                if key in colors:
                    item.setForeground(QColor(colors[key]))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
            if c in mono_cols:
                item.setFont(QFont("Consolas", 9))
            if c in align_right:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(r, c, item)
    table.resizeColumnsToContents()


def severity_colors() -> dict[str, str]:
    return _CURRENT_THEME.severity


def badge_row(title: str, value: str, color: str) -> QWidget:
    """A compact label/value row used in summary panels."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    name = QLabel(title)
    name.setObjectName("muted")
    val = QLabel(value)
    val.setStyleSheet(f"color:{color}; font-weight:600;")
    lay.addWidget(name)
    lay.addStretch(1)
    lay.addWidget(val)
    return w


def metric_grid(cards: list[tuple[str, str]], columns: int = 3) -> QWidget:
    """A responsive grid of stat cards that reflows as the window shrinks."""
    grid = ResponsiveGrid(columns=columns)
    for title, value in cards:
        grid.add_card(StatCard(title, value))
    return grid


class ResponsiveGrid(QWidget):
    """Grid of cards that changes column count with the available width."""

    def __init__(self, columns: int = 3, min_card_width: int = 190) -> None:
        super().__init__()
        self._cards: list[QWidget] = []
        self._max_columns = columns
        self._min_card_width = min_card_width
        self._layout = QGridLayout(self)
        self._layout.setSpacing(12)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def add_card(self, card: QWidget) -> None:
        self._cards.append(card)

    def _reflow(self) -> None:
        width = max(self.width(), 320)
        columns = max(1, min(self._max_columns, width // self._min_card_width))
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        for index, card in enumerate(self._cards):
            card.setParent(self)
            self._layout.addWidget(card, index // columns, index % columns)
        for index in range(len(self._cards)):
            card = self._cards[index]
            card.setVisible(True)
        self._columns = columns

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._reflow()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._reflow()


def hbox(*widgets: QWidget, spacing: int = 8) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for widget in widgets:
        lay.addWidget(widget)
    lay.addStretch(1)
    return w


def wrap_row(*widgets: QWidget, spacing: int = 8) -> QWidget:
    """A row of buttons that flows onto extra lines when space is tight."""

    class FlowWidget(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._widgets = list(widgets)
            self._layout = _FlowLayout(self, spacing=spacing)
            for widget in self._widgets:
                self._layout.addWidget(widget)

    return FlowWidget()


class _FlowLayout(QLayout):
    """Minimal flow layout: wraps children when the row runs out of width."""

    def __init__(self, parent=None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - Qt naming
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 - Qt naming
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):  # noqa: N802 - Qt naming
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802 - Qt naming
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt naming
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt naming
        return self._do_layout(width, apply=False)

    def setGeometry(self, rect) -> None:  # noqa: N802 - Qt naming
        super().setGeometry(rect)
        self._do_layout(rect.width(), apply=True)

    def sizeHint(self):  # noqa: N802 - Qt naming

        return self.minimumSize()

    def minimumSize(self):  # noqa: N802 - Qt naming
        from PySide6.QtCore import QSize

        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, width: int, *, apply: bool) -> int:
        x, y, line_height = 0, 0, 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > width and x > 0:
                x = 0
                y += line_height + spacing
                line_height = 0
            if apply:
                item.setGeometry(_rect(x, y, hint.width(), hint.height()))
            x += hint.width() + spacing
            line_height = max(line_height, hint.height())
        return y + line_height


def _rect(x: int, y: int, w: int, h: int):
    from PySide6.QtCore import QRect

    return QRect(x, y, w, h)
