"""Windows CPU / GPU / Sensors pages. Mirrors App/Pages/WindowsExtrasPages.swift."""
from __future__ import annotations

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QScrollArea, QWidget,
                                 QListWidget)

from core import Fmt
from page_base import SessionPage
from widgets_common import Card, MeterCard, Sparkline, StatRow, preserve_scroll


class WindowsCPUPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)

        self.title = QLabel()
        self.title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.subtitle = QLabel()
        self.subtitle.setStyleSheet("color: palette(mid);")
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)

        row = QHBoxLayout()
        self.util_card = MeterCard("Utilization", None)
        self.cache_card = Card("Cache")
        self.l2_row = StatRow("L2")
        self.l3_row = StatRow("L3")
        self.cache_card.add(self.l2_row)
        self.cache_card.add(self.l3_row)
        self.virt_card = Card("Virtualization")
        self.virt_value = QLabel("–")
        self.virt_card.add(self.virt_value)
        row.addWidget(self.util_card)
        row.addWidget(self.cache_card)
        row.addWidget(self.virt_card)
        lay.addLayout(row)

        self.history_card = Card("Utilization history")
        self.history_spark = Sparkline(max_value=100, height=100)
        self.history_card.add(self.history_spark)
        lay.addWidget(self.history_card)

        self.cores_card = Card("Logical processors")
        self.cores_grid = QGridLayout()
        self.cores_widget = QWidget()
        self.cores_widget.setLayout(self.cores_grid)
        self.cores_card.add(self.cores_widget)
        lay.addWidget(self.cores_card)
        lay.addStretch(1)
        self._refresh()

    def _on_updated(self, topic):
        if topic == "poll":
            self._refresh()

    def _refresh(self):
        s = self.session
        info = s.cpu_info
        self.title.setText(info.name or "Processor")
        parts = []
        if info.cores:
            parts.append(f"{info.cores} cores, {info.logical} threads")
        if info.maxMHz:
            parts.append(f"base {Fmt.mhz(info.maxMHz)}")
        self.subtitle.setText(" · ".join(parts))
        self.util_card.set_value(s.cpu_percent, "")
        self.l2_row.set_value(Fmt.bytes(info.l2KB * 1024) if info.l2KB else "–")
        self.l3_row.set_value(Fmt.bytes(info.l3KB * 1024) if info.l3KB else "–")
        self.virt_value.setText("Enabled" if info.virtualization else ("Disabled" if info.virtualization is False else "–"))
        self.history_spark.set_values(s.cpu_history)

        while self.cores_grid.count():
            item = self.cores_grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        cols = 4
        for i, c in enumerate(s.cores):
            card = Card()
            head = QHBoxLayout()
            head.addWidget(QLabel(f"CPU {i}"))
            head.addStretch(1)
            head.addWidget(QLabel(Fmt.percent(c.util)))
            head_w = QWidget()
            head_w.setLayout(head)
            card.add(head_w)
            spark = Sparkline(max_value=100, height=28)
            spark.set_values(s.core_history.get(c.id, []))
            card.add(spark)
            freq_lbl = QLabel(Fmt.mhz(c.freqMHz))
            freq_lbl.setStyleSheet("color: palette(mid); font-size: 12px;")
            card.add(freq_lbl)
            self.cores_grid.addWidget(card, i // cols, i % cols)
        if not s.cores:
            self.cores_grid.addWidget(QLabel("No data yet"), 0, 0)


class WindowsGPUPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        self.inner = QWidget()
        scroll.setWidget(self.inner)
        self.lay = QVBoxLayout(self.inner)
        self.lay.addStretch(1)
        self.start_polling(3000)

    def _tick(self):
        self.session.refresh_gpu_detail()

    def _on_updated(self, topic):
        if topic == "gpu":
            self._refresh()

    def _refresh(self):
        while self.lay.count() > 1:
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        s = self.session
        if not s.gpus:
            self.lay.insertWidget(0, QLabel("No GPU found — Windows didn't report a display adapter, or the "
                                             "GPU performance counters aren't available."))
            return
        for g in s.gpus:
            self.lay.insertWidget(self.lay.count() - 1, self._adapter_widget(g))
        if s.gpu_processes:
            card = Card("Top GPU processes")
            lst = QListWidget()
            for p in s.gpu_processes:
                lst.addItem(f"{p.name} (pid {p.pid})  —  GPU {Fmt.percent(p.util)}  ·  VRAM {Fmt.bytes(p.vram)}")
            card.add(lst)
            self.lay.insertWidget(self.lay.count() - 1, card)

    def _adapter_widget(self, g) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        title = QLabel(g.name)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(title)
        sub_parts = []
        if g.vram_total:
            sub_parts.append(f"{Fmt.bytes(g.vram_total)} VRAM")
        if g.driver:
            sub_parts.append(f"driver {g.driver}")
        if g.driver_date:
            sub_parts.append(g.driver_date)
        sub = QLabel(" · ".join(sub_parts))
        sub.setStyleSheet("color: palette(mid);")
        lay.addWidget(sub)

        row = QHBoxLayout()
        util_card = MeterCard("Utilization", g.util, (f"pstate {g.nvidia.pstate}" if g.nvidia else "busiest engine"))
        vram_card = MeterCard("Video memory", (g.vram_fraction * 100) if g.vram_fraction is not None else None,
                               (Fmt.bytes(g.vram_used) + (f" of {Fmt.bytes(g.vram_total)}" if g.vram_total else "")) if g.vram_used is not None else "–")
        temp_card = Card("Temperature")
        temp_card.add(QLabel(Fmt.temp(g.temp)))
        power_card = Card("Power")
        power_card.add(QLabel(f"{g.power:.0f} W" if g.power is not None else "–"))
        for c in (util_card, vram_card, temp_card, power_card):
            row.addWidget(c)
        lay.addLayout(row)

        hist = self.session.gpu_history.get(g.id)
        spark_row = QHBoxLayout()
        util_hist_card = Card("Utilization")
        util_spark = Sparkline(max_value=100, height=110)
        if hist:
            util_spark.set_values(hist.util)
        util_hist_card.add(util_spark)
        vram_hist_card = Card("Video memory")
        vram_spark = Sparkline(max_value=100, height=110)
        if hist:
            vram_spark.set_values(hist.vram)
        vram_hist_card.add(vram_spark)
        spark_row.addWidget(util_hist_card)
        spark_row.addWidget(vram_hist_card)
        lay.addLayout(spark_row)

        detail_row = QHBoxLayout()
        engines_card = Card("Engines")
        if not g.engines:
            engines_card.add(QLabel("Idle"))
        for name, v in sorted(g.engines.items(), key=lambda kv: -kv[1]):
            engines_card.add(QLabel(f"{name}: {Fmt.percent(v)}"))
        details_card = Card("Details")
        if g.nvidia:
            n = g.nvidia
            details_card.add(QLabel(f"Core clock: {Fmt.mhz(n.clock_gr)}"))
            details_card.add(QLabel(f"Memory clock: {Fmt.mhz(n.clock_mem)}"))
            details_card.add(QLabel(f"Fan: {int(n.fan)}%" if n.fan is not None else "Fan: –"))
        if g.processor:
            details_card.add(QLabel(f"Chip: {g.processor}"))
        if g.status:
            details_card.add(QLabel(f"Status: {g.status}"))
        detail_row.addWidget(engines_card)
        detail_row.addWidget(details_card)
        lay.addLayout(detail_row)
        return w


class WindowsSensorsPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet("color: orange;")
        lay.addWidget(self.note_label)
        self.list = QListWidget()
        lay.addWidget(self.list)
        self._refresh()

    def _on_updated(self, topic):
        if topic == "poll":
            self._refresh()

    def _refresh(self):
        s = self.session
        with preserve_scroll(self.list):
            if not s.sensors:
                self.note_label.setText(self._empty_description())
                self.list.clear()
                return
            self.note_label.setText("")
            self.list.clear()
            groups: dict[str, list] = {}
            for sensor in s.sensors:
                groups.setdefault(sensor.type, []).append(sensor)
            for g in sorted(groups):
                self.list.addItem(f"— {g} —")
                for sensor in sorted(groups[g], key=lambda x: x.name):
                    self.list.addItem(f"{sensor.name}: {sensor.formatted}")

    def _empty_description(self) -> str:
        s = self.session
        if s.machine.lhmPort > 0:
            if s.lhm_http_working:
                return "LibreHardwareMonitor is reachable but isn't reporting any sensors yet."
            return (f"Couldn't reach LibreHardwareMonitor's Remote Web Server at port {s.machine.lhmPort}. "
                    "Make sure it's checked in LibreHardwareMonitor's Options menu and the app is still "
                    "running, then check this machine's Edit sheet for the right port.")
        return ("Windows doesn't expose temperatures, fan speeds or voltages on its own. In "
                "LibreHardwareMonitor, check “Remote Web Server” under Options and set its port in "
                "this machine's Edit sheet.")
