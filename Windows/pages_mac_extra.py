"""macOS Startup Items / Installed Software pages. Mirrors App/Pages/MacLongTailPages.swift."""
from __future__ import annotations

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                                 QWidget, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
                                 QAbstractItemView)

from page_base import SessionPage
from widgets_common import preserve_scroll


class MacStartupPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.refresh_startup()

    def _on_updated(self, topic):
        if topic == "startup":
            self._refresh()

    def _refresh(self):
        with preserve_scroll(self.list):
            self.list.clear()
            if not self.session.startup_loaded:
                self.list.addItem("Loading startup items…")
                return
            if not self.session.startup_items:
                self.list.addItem("No startup items found")
                return
            for item in self.session.startup_items:
                row = QListWidgetItem()
                w = QWidget()
                h = QHBoxLayout(w)
                dot = QLabel("⏸" if item.disabled else "✅")
                h.addWidget(dot)
                scope = "System" if item.domain == "system" else "User"
                h.addWidget(QLabel(f"{item.label}\n{scope} · {item.scope}"), 1)
                btn = QPushButton("Enable" if item.disabled else "Disable")
                btn.clicked.connect(lambda _, it=item: self.session.set_startup_enabled(it, it.disabled))
                h.addWidget(btn)
                self.list.addItem(row)
                self.list.setItemWidget(row, w)
                _hint = w.sizeHint()
                _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
                row.setSizeHint(_hint)


class MacSoftwarePage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter…")
        self.filter_edit.textChanged.connect(self._refresh)
        lay.addWidget(self.filter_edit)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Version", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)
        session.refresh_software()

    def _on_updated(self, topic):
        if topic == "software":
            self._refresh()

    def _refresh(self):
        filt = self.filter_edit.text().lower()
        rows = [s for s in self.session.software if not filt or filt in s.name.lower()]
        self.table.setRowCount(len(rows))
        for i, s in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(s.name))
            self.table.setItem(i, 1, QTableWidgetItem(s.version))
            self.table.setItem(i, 2, QTableWidgetItem(s.publisher))
