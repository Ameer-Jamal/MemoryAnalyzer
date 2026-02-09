import sys
import time
import json
import csv
import logging
from logging.handlers import RotatingFileHandler
from collections import deque
from pathlib import Path

import psutil

# GUI imports
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QSpinBox,
    QFileDialog,
    QCheckBox,
    QLineEdit,
    QMessageBox,
    QGridLayout,
    QCompleter,
    QToolButton,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings, QCoreApplication, QStringListModel

# Charts
import pyqtgraph as pg
import pyqtgraph.exporters  # for chart export

try:
    from platformdirs import user_log_dir
except ImportError:  # lightweight fallback
    def user_log_dir(appname, appauthor=None):
        return str(Path.home() / f".{appname}" / "logs")

from core import (
    build_targets,
    aggregate_latest_by_name,
    parse_extra_pids,
    parse_extra_names,
    dedup_process_items,
    filter_process_items,
    parse_targets_text,
)


APP_NAME = "MemoryAnalyzer"
BUFFER_MAX = 10_000


def ensure_log_dir():
    log_dir = Path(user_log_dir(APP_NAME))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


LOG_DIR = ensure_log_dir()
LOG_PATH = LOG_DIR / "memory_analyzer.log"


def setup_logging():
    handler = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=3)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        handler.setFormatter(fmt)
        root.addHandler(handler)


class SamplingWorker(QThread):
    # ts, pid, name, cpu_pct, mem_mb
    sample_ready = pyqtSignal(float, int, str, float, float)
    host_ready = pyqtSignal(float, float)  # host cpu %, host mem %
    status = pyqtSignal(str)
    alert = pyqtSignal(str)
    finished_ok = pyqtSignal()

    def __init__(self, targets, interval_ms, cpu_threshold, mem_threshold, reconnect=True, csv_path=None):
        super().__init__()
        # targets: list of dicts with pid (int|None) and name (str)
        self.targets = [{
            "pid": t.get("pid"),
            "name": (t.get("name") or "").strip(),
            "proc": None,
            "active": True,
        } for t in targets]
        self.interval_ms = max(100, interval_ms)
        self.cpu_threshold = cpu_threshold
        self.mem_threshold = mem_threshold
        self.reconnect = reconnect
        self._running = True
        self.csv_path = csv_path
        self.csv_file = None
        self.csv_writer = None

    def stop(self):
        self._running = False

    def _open_csv(self):
        if not self.csv_path:
            return
        try:
            self.csv_file = open(self.csv_path, "w", newline="")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(["timestamp", "pid", "name", "cpu_percent", "memory_mb", "host_cpu", "host_mem"])
        except Exception as exc:
            logging.error("Failed to open CSV: %s", exc)

    def _close_csv(self):
        if self.csv_file:
            try:
                self.csv_file.close()
            except Exception:
                pass

    def _find_process_for(self, target):
        pid = target["pid"]
        name = target["name"]
        if pid:
            try:
                proc = psutil.Process(pid)
                if proc.is_running():
                    return proc
            except psutil.NoSuchProcess:
                return None
        if name:
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    if proc.info["name"] and proc.info["name"].lower() == name.lower():
                        return proc
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        return None

    def run(self):
        logging.info("Worker start for %d targets", len(self.targets))
        self._open_csv()
        last_host_sample = time.monotonic()
        host_cpu_val = 0.0
        host_mem_val = 0.0

        while self._running:
            any_active = False

            if time.monotonic() - last_host_sample >= 1:
                try:
                    host_cpu_val = psutil.cpu_percent(None)
                    host_mem_val = psutil.virtual_memory().percent
                    self.host_ready.emit(host_cpu_val, host_mem_val)
                except Exception:
                    pass
                last_host_sample = time.monotonic()

            for target in self.targets:
                if not target["active"]:
                    continue

                proc = target.get("proc")
                if not proc or not proc.is_running():
                    proc = self._find_process_for(target)
                    target["proc"] = proc
                    if proc:
                        try:
                            proc.cpu_percent(None)  # prime
                            self.status.emit(f"Monitoring PID {proc.pid} ({proc.name()})")
                        except Exception as exc:
                            logging.warning("Prime failed: %s", exc)
                    else:
                        self.status.emit(f"Waiting for '{target['name'] or target['pid']}'...")
                        if not self.reconnect and target["pid"]:
                            target["active"] = False
                        continue

                if not proc:
                    continue

                any_active = True
                try:
                    ts = time.time()
                    cpu = proc.cpu_percent(None) / max(psutil.cpu_count(logical=True) or 1, 1)
                    mem = proc.memory_info().rss / (1024 * 1024)
                    self.sample_ready.emit(ts, proc.pid, proc.name(), cpu, mem)

                    if self.csv_writer:
                        self.csv_writer.writerow([ts, proc.pid, proc.name(), f"{cpu:.3f}", f"{mem:.3f}",
                                                   f"{host_cpu_val:.2f}", f"{host_mem_val:.2f}"])

                    if self.cpu_threshold and cpu >= self.cpu_threshold:
                        self.alert.emit(f"[PID {proc.pid}] CPU {cpu:.1f}% >= {self.cpu_threshold}%")
                    if self.mem_threshold and mem >= self.mem_threshold:
                        self.alert.emit(f"[PID {proc.pid}] Memory {mem:.1f} MB >= {self.mem_threshold} MB")
                except psutil.NoSuchProcess:
                    self.status.emit(f"Process {target['pid'] or target['name']} ended")
                    target["proc"] = None
                    if not self.reconnect and target["pid"]:
                        target["active"] = False
                except Exception as exc:
                    logging.exception("Sampling error")
                    self.alert.emit(f"Error: {exc}")

            if not any_active and not self.reconnect:
                break

            self.msleep(self.interval_ms)

        self._close_csv()
        self.finished_ok.emit()
        logging.info("Worker stop")


class ProcessMonitorApp(QWidget):
    def __init__(self):
        super().__init__()
        setup_logging()
        self.setWindowTitle("Process Monitor")
        self.settings = QSettings(APP_NAME, "App")

        self.buffers = {}  # pid -> deque
        self.agg_buffers = {}  # name -> deque
        self.latest_by_pid = {}  # pid -> (ts, cpu, mem, name)
        self.host_cpu = 0
        self.host_mem = 0
        self.worker = None
        self.csv_path = None
        self.dark_mode = False
        self.curves_cpu = {}
        self.curves_mem = {}
        self.agg_curves_cpu = {}
        self.agg_curves_mem = {}
        self._all_items = []
        self._completer_model = QStringListModel()

        self._load_config()
        self._build_ui()
        self.group_dropdown_check.setChecked(self.group_dropdown)
        self.group_view_check.setChecked(self.group_view)
        self.reconnect_check.setChecked(self.reconnect)
        self._apply_theme()
        self._refresh_process_list()

    # UI -----------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout()

        # Tabs
        from PyQt5.QtWidgets import QTabWidget, QWidget as QtWidget
        tabs = QTabWidget()

        # Monitor tab ----------------------------------------------------
        monitor_tab = QtWidget()
        top = QGridLayout(monitor_tab)
        row = 0

        top.addWidget(QLabel("Primary process"), row, 0)
        self.process_combo = QComboBox()
        self.process_combo.setEditable(True)  # inline filtering
        self.process_combo.lineEdit().textEdited.connect(self._on_filter_change)
        self.process_completer = QCompleter()
        self.process_completer.setModel(self._completer_model)
        self.process_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.process_combo.setCompleter(self.process_completer)
        top.addWidget(self.process_combo, row, 1, 1, 2)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_process_list)
        top.addWidget(self.refresh_btn, row, 3)
        row += 1

        self.group_dropdown_check = QCheckBox("Group dropdown by name")
        self.group_dropdown_check.setChecked(False)
        self.group_dropdown_check.stateChanged.connect(self._refresh_process_list)
        self.group_dropdown_check.stateChanged.connect(self._on_setting_changed)
        top.addWidget(self.group_dropdown_check, row, 1, 1, 3)
        row += 1

        top.addWidget(QLabel("Extra targets"), row, 0)
        extra_row = QHBoxLayout()
        self.extra_combo_group = QCheckBox("Group by name")
        self.extra_combo_group.setChecked(False)
        self.extra_combo_group.stateChanged.connect(self._refresh_process_list)
        self.extra_combo_group.stateChanged.connect(self._on_setting_changed)
        self.extra_combo = QComboBox()
        self.extra_combo.setEditable(True)
        self.extra_combo.lineEdit().textEdited.connect(self._on_filter_change_extra)
        self.extra_combo.setCompleter(self.process_completer)
        self.extra_add_btn = QPushButton("Add")
        self.extra_add_btn.clicked.connect(self._add_extra_from_combo)
        extra_row.addWidget(self.extra_combo_group)
        extra_row.addWidget(self.extra_combo)
        extra_row.addWidget(self.extra_add_btn)
        top.addLayout(extra_row, row, 1, 1, 3)
        row += 1

        top.addWidget(QLabel("Extra targets (PIDs or names, comma-separated)"), row, 0)
        self.extra_targets_edit = QLineEdit(self.extra_targets_text)
        self.extra_targets_edit.editingFinished.connect(self._on_setting_changed)
        self.extra_completer = QCompleter()
        self.extra_completer.setModel(self._completer_model)
        self.extra_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.extra_targets_edit.setCompleter(self.extra_completer)
        top.addWidget(self.extra_targets_edit, row, 1, 1, 3)
        row += 1

        top.addWidget(QLabel("Interval (ms)"), row, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(100, 10_000)
        self.interval_spin.setSingleStep(100)
        self.interval_spin.setValue(self.interval_ms)
        self.interval_spin.valueChanged.connect(self._on_setting_changed)
        top.addWidget(self.interval_spin, row, 1)

        top.addWidget(QLabel("CPU alert (%)"), row, 0)
        self.cpu_thresh = QSpinBox()
        self.cpu_thresh.setRange(0, 1000)
        self.cpu_thresh.setValue(self.cpu_threshold)
        self.cpu_thresh.valueChanged.connect(self._on_setting_changed)
        top.addWidget(self.cpu_thresh, row, 1)

        top.addWidget(QLabel("Mem alert (MB)"), row, 2)
        self.mem_thresh = QSpinBox()
        self.mem_thresh.setRange(0, 100_000)
        self.mem_thresh.setValue(self.mem_threshold)
        self.mem_thresh.valueChanged.connect(self._on_setting_changed)
        top.addWidget(self.mem_thresh, row, 3)
        row += 1

        monitor_tab.setLayout(top)
        tabs.addTab(monitor_tab, "Monitor")

        # Settings tab ----------------------------------------------------
        settings_tab = QtWidget()
        set_layout = QVBoxLayout(settings_tab)

        self.reconnect_check = QCheckBox("Auto reconnect to processes")
        self.reconnect_check.setChecked(True)
        self.reconnect_check.stateChanged.connect(self._on_setting_changed)
        set_layout.addWidget(self.reconnect_check)

        self.dark_check = QCheckBox("Dark mode")
        self.dark_check.setChecked(self.dark_mode)
        self.dark_check.stateChanged.connect(self._apply_theme)
        self.dark_check.stateChanged.connect(self._on_setting_changed)
        set_layout.addWidget(self.dark_check)

        # Default process name
        default_row = QHBoxLayout()
        default_label = QLabel("Default process to monitor at startup")
        info_btn = QToolButton()
        info_btn.setText("i")
        info_btn.setToolTip("If no dropdown selection is made, this name will be monitored.\nGrouping expands to all PIDs with this name.")
        self.default_name_edit = QLineEdit(self.last_process_name)
        self.default_name_edit.editingFinished.connect(self._on_setting_changed)
        default_row.addWidget(default_label)
        default_row.addWidget(info_btn)
        default_row.addWidget(self.default_name_edit)
        set_layout.addLayout(default_row)

        set_layout.addStretch(1)
        tabs.addTab(settings_tab, "Settings")

        root.addWidget(tabs)

        # Buttons
        btns = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start_monitoring)
        btns.addWidget(self.start_btn)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.pause_monitoring)
        self.pause_btn.setEnabled(False)
        btns.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_monitoring)
        self.stop_btn.setEnabled(False)
        btns.addWidget(self.stop_btn)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.clicked.connect(self.reset_monitor)
        btns.addWidget(self.reset_btn)

        self.save_chart_btn = QPushButton("Save Chart")
        self.save_chart_btn.clicked.connect(self.save_chart)
        self.save_chart_btn.setEnabled(False)
        btns.addWidget(self.save_chart_btn)

        self.save_csv_btn = QPushButton("Save CSV As")
        self.save_csv_btn.clicked.connect(self.save_csv_as)
        btns.addWidget(self.save_csv_btn)

        root.addLayout(btns)

        # View toggles
        view_row = QHBoxLayout()
        self.group_view_check = QCheckBox("Show grouped averages only")
        self.group_view_check.stateChanged.connect(self._redraw)
        self.group_view_check.stateChanged.connect(self._on_setting_changed)
        view_row.addWidget(self.group_view_check)
        root.addLayout(view_row)

        # Charts
        pg.setConfigOptions(antialias=True)
        self.cpu_plot = pg.PlotWidget(title="CPU %")
        self.cpu_plot.showGrid(x=True, y=True)
        self.cpu_plot.addLegend(offset=(30, 30))
        self.cpu_plot.setLabel('bottom', 'Time', units='s')
        self.cpu_plot.setLabel('left', 'CPU', units='%')

        self.mem_plot = pg.PlotWidget(title="Memory MB")
        self.mem_plot.showGrid(x=True, y=True)
        self.mem_plot.addLegend(offset=(30, 30))
        self.mem_plot.setLabel('bottom', 'Time', units='s')
        self.mem_plot.setLabel('left', 'Memory', units='MB')

        root.addWidget(self.cpu_plot)
        root.addWidget(self.mem_plot)

        # Status
        status_row = QHBoxLayout()
        self.status_label = QLabel("Status: idle")
        status_row.addWidget(self.status_label)
        self.host_label = QLabel("Host: -- CPU / -- Mem")
        status_row.addWidget(self.host_label)
        root.addLayout(status_row)

        self.setLayout(root)

    # Config --------------------------------------------------------------
    def _load_config(self):
        self.last_process_name = self.settings.value("process_name", "", type=str)
        self.last_pid = self.settings.value("pid", None, type=int)
        self.interval_ms = self.settings.value("interval", 1000, type=int)
        self.cpu_threshold = self.settings.value("cpu_threshold", 0, type=int)
        self.mem_threshold = self.settings.value("mem_threshold", 0, type=int)
        self.dark_mode = self.settings.value("dark_mode", True, type=bool)
        self.extra_targets_text = self.settings.value("extra_targets_text", "", type=str)
        self.group_dropdown = self.settings.value("group_dropdown", False, type=bool)
        self.group_view = self.settings.value("group_view", False, type=bool)
        self.reconnect = self.settings.value("reconnect", True, type=bool)

    def _save_config(self, pid=None, name=None):
        self.last_process_name = name if name is not None else self.last_process_name
        self.last_pid = pid if pid is not None else self.last_pid
        self.settings.setValue("process_name", self.last_process_name)
        self.settings.setValue("pid", self.last_pid)
        self.settings.setValue("interval", self.interval_spin.value())
        self.settings.setValue("cpu_threshold", self.cpu_thresh.value())
        self.settings.setValue("mem_threshold", self.mem_thresh.value())
        self.settings.setValue("dark_mode", self.dark_check.isChecked())
        self.settings.setValue("extra_targets_text", self.extra_targets_edit.text())
        self.settings.setValue("group_dropdown", self.group_dropdown_check.isChecked())
        self.settings.setValue("group_view", self.group_view_check.isChecked())
        self.settings.setValue("reconnect", self.reconnect_check.isChecked())
        self.settings.setValue("process_name", self.default_name_edit.text().strip() or self.last_process_name)
        self.settings.sync()

    # Process list --------------------------------------------------------
    def _refresh_process_list(self):
        current_data = self.process_combo.currentData()
        self._all_items = []
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                self._all_items.append((proc.info["pid"], proc.info["name"] or "?"))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self._all_items = dedup_process_items(self._all_items)
        self._all_items.sort(key=lambda t: (t[1].lower(), t[0]))
        self._update_completer_sources()
        self._apply_filter(text=self.process_combo.lineEdit().text())
        self._apply_filter_extra(text=self.extra_combo.lineEdit().text())

        # restore selection if possible
        if current_data:
            for idx in range(self.process_combo.count()):
                if self.process_combo.itemData(idx) == current_data:
                    self.process_combo.setCurrentIndex(idx)
                    break
        elif self.last_pid:
            for idx in range(self.process_combo.count()):
                data = self.process_combo.itemData(idx)
                if isinstance(data, dict) and data.get("pid") == self.last_pid:
                    self.process_combo.setCurrentIndex(idx)
                    break
        self.status_label.setText("Status: pick a process and Start")

    def _apply_filter(self, text: str):
        if not self._all_items:
            return
        self.process_combo.blockSignals(True)
        current_text = text
        self.process_combo.clear()
        items = filter_process_items(self._all_items, text)
        if self.group_dropdown_check.isChecked():
            by_name = {}
            for pid, name in items:
                by_name.setdefault(name, []).append(pid)
            # apply text filter on grouped label
            for name, pids in sorted(by_name.items(), key=lambda kv: kv[0].lower()):
                label = f"{name} ({len(pids)} procs)"
                if current_text.lower() in label.lower():
                    self.process_combo.addItem(label, {"pid": None, "name": name})
        else:
            for pid, name in items:
                self.process_combo.addItem(f"{name} (PID {pid})", {"pid": pid, "name": name})
        # avoid auto-selecting first entry; keep user text
        self.process_combo.setCurrentIndex(-1)
        self.process_combo.lineEdit().setText(current_text)

        # update completer to filtered entries
        entries = []
        for pid, name in items:
            entries.append(name)
            entries.append(str(pid))
            entries.append(f"{name} (PID {pid})")
        self._completer_model.setStringList(entries)
        self.process_combo.blockSignals(False)

    def _on_filter_change(self, text):
        if not self._all_items:
            self._refresh_process_list()
        else:
            self._apply_filter(text)
        self._on_setting_changed()

    def _apply_filter_extra(self, text: str):
        if not self._all_items:
            return
        self.extra_combo.blockSignals(True)
        current_text = text
        self.extra_combo.clear()
        items = filter_process_items(self._all_items, text)
        if self.extra_combo_group.isChecked():
            by_name = {}
            for pid, name in items:
                by_name.setdefault(name, []).append(pid)
            for name, pids in sorted(by_name.items(), key=lambda kv: kv[0].lower()):
                label = f"{name} ({len(pids)} procs)"
                if current_text.lower() in label.lower():
                    self.extra_combo.addItem(label, {"pid": None, "name": name})
        else:
            for pid, name in items:
                # don't duplicate primary current selection
                self.extra_combo.addItem(f"{name} (PID {pid})", {"pid": pid, "name": name})
        self.extra_combo.setCurrentIndex(-1)
        self.extra_combo.lineEdit().setText(current_text)
        self.extra_combo.blockSignals(False)

    def _on_filter_change_extra(self, text):
        if not self._all_items:
            self._refresh_process_list()
        else:
            self._apply_filter_extra(text)

    def _add_extra_from_combo(self):
        data = self.extra_combo.currentData()
        if isinstance(data, dict):
            val = data.get("name") if data.get("pid") is None else str(data.get("pid"))
        else:
            val = self.extra_combo.currentText()
        val = val.strip()
        if not val:
            return
        existing = [v.strip() for v in self.extra_targets_edit.text().split(",") if v.strip()]
        if val not in existing:
            existing.append(val)
            self.extra_targets_edit.setText(", ".join(existing))
            self._on_setting_changed()

    def _all_pids_for_name(self, name: str):
        return [pid for pid, nm in self._all_items if nm.lower() == name.lower()]

    def _targets_from_text(self, data, text: str, grouped: bool):
        targets = []
        # When dropdown selection exists
        if isinstance(data, dict):
            if grouped and data.get("name"):
                for pid in self._all_pids_for_name(data["name"]):
                    targets.append({"pid": pid, "name": data["name"]})
            else:
                targets.append({"pid": data.get("pid"), "name": data.get("name") or ""})
        # Fallback: try parsing text
        if not targets and text:
            txt = text.strip()
            if grouped:
                for pid in self._all_pids_for_name(txt):
                    targets.append({"pid": pid, "name": txt})
            else:
                if txt.isdigit():
                    targets.append({"pid": int(txt), "name": ""})
                else:
                    # use first pid match for that name, otherwise name only
                    pids = self._all_pids_for_name(txt)
                    if pids:
                        for pid in pids:
                            targets.append({"pid": pid, "name": txt})
                    else:
                        targets.append({"pid": None, "name": txt})
        return targets

    def _update_completer_sources(self):
        # Offer name and "name (PID pid)" plus pid strings for convenience
        entries = []
        for pid, name in self._all_items:
            entries.append(name)
            entries.append(str(pid))
            entries.append(f"{name} (PID {pid})")
        self._completer_model.setStringList(entries)

    def _on_setting_changed(self, *args):
        self._save_config(pid=self.last_pid, name=self.last_process_name)

    def _selected_target(self):
        data = self.process_combo.currentData()
        text = self.process_combo.currentText().strip()
        grouped = self.group_dropdown_check.isChecked()
        return self._targets_from_text(data, text, grouped)

    # Monitoring ----------------------------------------------------------
    def start_monitoring(self):
        if self.worker:
            QMessageBox.information(self, "Already running", "Monitoring is already active")
            return

        primary_targets = self._selected_target()
        if not primary_targets:
            fallback = self.default_name_edit.text().strip()
            if fallback:
                primary_targets = self._targets_from_text(None, fallback, self.group_dropdown_check.isChecked())

        interval = self.interval_spin.value()
        cpu_th = self.cpu_thresh.value()
        mem_th = self.mem_thresh.value()

        # extras
        extra_pids, extra_names = parse_targets_text(self.extra_targets_edit.text())
        extra_targets = []
        for pid in extra_pids:
            extra_targets.append({"pid": pid, "name": ""})
        for name in extra_names:
            pids = self._all_pids_for_name(name)
            if pids:
                for pid in pids:
                    extra_targets.append({"pid": pid, "name": name})
            else:
                extra_targets.append({"pid": None, "name": name})

        # merge and dedupe
        targets = []
        seen = set()
        for t in (primary_targets + extra_targets):
            key = (t.get("pid"), (t.get("name") or "").lower())
            if key in seen:
                continue
            seen.add(key)
            targets.append(t)

        if not targets:
            QMessageBox.warning(self, "No targets", "Select a process or add PIDs/names to monitor.")
            return

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.csv_path = (LOG_DIR / f"session_{timestamp}.csv")

        self._reset_plots()
        self.worker = SamplingWorker(targets, interval, cpu_th, mem_th, reconnect=self.reconnect_check.isChecked(), csv_path=self.csv_path)
        self.worker.sample_ready.connect(self._handle_sample)
        self.worker.host_ready.connect(self._handle_host)
        self.worker.status.connect(self._set_status)
        self.worker.alert.connect(self._handle_alert)
        self.worker.finished_ok.connect(self._handle_finished)
        self.worker.start()

        # store primary as first target for persistence
        if primary_targets:
            primary_first = primary_targets[0]
            self.last_process_name = primary_first.get("name", "")
            self.last_pid = primary_first.get("pid")
        self._save_config(pid=self.last_pid, name=self.last_process_name)

        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.save_chart_btn.setEnabled(False)
        self.status_label.setText("Status: running")

    def pause_monitoring(self):
        if self.worker:
            self.worker.stop()
            self.worker.wait(1000)
            self.worker = None
            self.status_label.setText("Status: paused")
            self.start_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.save_chart_btn.setEnabled(True)

    def stop_monitoring(self):
        if self.worker:
            self.worker.stop()
            self.worker.wait(1000)
            self.worker = None
        self.status_label.setText("Status: stopped")
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.save_chart_btn.setEnabled(True)

    def reset_monitor(self):
        self.stop_monitoring()
        self._reset_plots()
        self.status_label.setText("Status: idle")
        self._refresh_process_list()

    # Handlers ------------------------------------------------------------
    def _handle_sample(self, ts, pid, name, cpu, mem):
        self.latest_by_pid[pid] = (ts, cpu, mem, name)
        buf = self.buffers.setdefault(pid, deque(maxlen=BUFFER_MAX))
        buf.append((ts, cpu, mem, name))
        if pid not in self.curves_cpu:
            color = pg.intColor(len(self.curves_cpu), hues=32, values=1.0, maxValue=255)
            self.curves_cpu[pid] = self.cpu_plot.plot(pen=pg.mkPen(color, width=2), name=f"{name} ({pid})")
            self.curves_mem[pid] = self.mem_plot.plot(pen=pg.mkPen(color, width=2), name=f"{name} ({pid})")

        # update aggregates for this name
        self._update_aggregates(ts)
        self._redraw()

    def _handle_host(self, cpu, mem):
        self.host_cpu, self.host_mem = cpu, mem
        self.host_label.setText(f"Host: {cpu:.1f}% CPU / {mem:.1f}% Mem")

    def _set_status(self, msg):
        self.status_label.setText(f"Status: {msg}")

    def _handle_alert(self, msg):
        self.status_label.setText(f"Alert: {msg}")
        QMessageBox.warning(self, "Threshold", msg)

    def _handle_finished(self):
        self.worker = None
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.save_chart_btn.setEnabled(True)

    # Drawing -------------------------------------------------------------
    def _update_aggregates(self, ts):
        aggregates = aggregate_latest_by_name(self.latest_by_pid)
        for name, (ts_val, cpu_avg, mem_avg) in aggregates.items():
            buf = self.agg_buffers.setdefault(name, deque(maxlen=BUFFER_MAX))
            buf.append((ts, cpu_avg, mem_avg))
            if name not in self.agg_curves_cpu:
                color = pg.intColor(len(self.agg_curves_cpu), hues=16, values=1.0, maxValue=255)
                self.agg_curves_cpu[name] = self.cpu_plot.plot(pen=pg.mkPen(color, style=Qt.DashLine, width=2), name=f"{name} avg")
                self.agg_curves_mem[name] = self.mem_plot.plot(pen=pg.mkPen(color, style=Qt.DashLine, width=2), name=f"{name} avg")

    def _redraw(self):
        show_group = self.group_view_check.isChecked()
        if show_group:
            if not self.agg_buffers:
                return
            base = min(buf[0][0] for buf in self.agg_buffers.values() if buf)
            for name, buf in self.agg_buffers.items():
                xs = [t - base for t, _, _ in buf]
                cpu = [c for _, c, _ in buf]
                mem = [m for _, _, m in buf]
                if name in self.agg_curves_cpu:
                    self.agg_curves_cpu[name].setVisible(True)
                    self.agg_curves_cpu[name].setData(xs, cpu)
                if name in self.agg_curves_mem:
                    self.agg_curves_mem[name].setVisible(True)
                    self.agg_curves_mem[name].setData(xs, mem)
            # hide individual
            for curve in self.curves_cpu.values():
                curve.setVisible(False)
            for curve in self.curves_mem.values():
                curve.setVisible(False)
        else:
            if not self.buffers:
                return
            base = min(buf[0][0] for buf in self.buffers.values() if buf)
            for pid, buf in self.buffers.items():
                xs = [t - base for t, _, _, _ in buf]
                cpu = [c for _, c, _, _ in buf]
                mem = [m for _, _, m, _ in buf]
                self.curves_cpu[pid].setVisible(True)
                self.curves_mem[pid].setVisible(True)
                self.curves_cpu[pid].setData(xs, cpu)
                self.curves_mem[pid].setData(xs, mem)
            # hide aggregates
            for curve in self.agg_curves_cpu.values():
                curve.setVisible(False)
            for curve in self.agg_curves_mem.values():
                curve.setVisible(False)

    # Helpers -------------------------------------------------------------
    def _reset_plots(self):
        self.buffers.clear()
        self.agg_buffers.clear()
        self.latest_by_pid.clear()
        self.curves_cpu.clear()
        self.curves_mem.clear()
        self.agg_curves_cpu.clear()
        self.agg_curves_mem.clear()
        self.cpu_plot.clear()
        self.cpu_plot.addLegend(offset=(30, 30))
        self.mem_plot.clear()
        self.mem_plot.addLegend(offset=(30, 30))

    # Saving --------------------------------------------------------------
    def save_chart(self):
        if not self.buffers and not self.agg_buffers:
            QMessageBox.information(self, "No data", "Start monitoring to generate a chart.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", str(Path.cwd() / "chart.png"), "PNG (*.png)")
        if not path:
            return
        exporter = pg.exporters.ImageExporter(self.cpu_plot.plotItem)
        exporter.parameters()["width"] = 1200
        exporter.export(path)
        QMessageBox.information(self, "Saved", f"Chart saved to {path}")

    def save_csv_as(self):
        if not self.csv_path or not Path(self.csv_path).exists():
            QMessageBox.information(self, "No data", "No CSV generated yet")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV As", str(Path.cwd() / "session.csv"), "CSV (*.csv)")
        if not path:
            return
        try:
            Path(path).write_bytes(Path(self.csv_path).read_bytes())
            QMessageBox.information(self, "Saved", f"CSV saved to {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # Theme ---------------------------------------------------------------
    def _apply_theme(self):
        dark = self.dark_check.isChecked()
        bg_dark = "#111"
        fg_dark = "#f5f5f5"
        bg_light = "#fafafa"
        fg_light = "#111"
        if dark:
            self.setStyleSheet("""
                QWidget { background-color: #111; color: #f5f5f5; }
                QLineEdit, QComboBox, QSpinBox { background: #1c1c1c; color: #f5f5f5; border: 1px solid #333; }
                QPushButton { background: #2a2a2a; color: #f5f5f5; border: 1px solid #444; padding: 4px 8px; }
                QPushButton:disabled { color: #777; }
                QTabWidget::pane { border: 1px solid #333; }
                QTabBar::tab { background: #1c1c1c; color: #f5f5f5; padding: 4px 8px; }
                QTabBar::tab:selected { background: #2a2a2a; }
            """)
            pg.setConfigOption('background', 'k')
            pg.setConfigOption('foreground', 'w')
            self.cpu_plot.setBackground(bg_dark)
            self.mem_plot.setBackground(bg_dark)
            for ax in ('left', 'bottom'):
                self.cpu_plot.getAxis(ax).setTextPen(fg_dark)
                self.cpu_plot.getAxis(ax).setPen(fg_dark)
                self.mem_plot.getAxis(ax).setTextPen(fg_dark)
                self.mem_plot.getAxis(ax).setPen(fg_dark)
        else:
            self.setStyleSheet("""
                QWidget { background-color: #fafafa; color: #111; }
                QLineEdit, QComboBox, QSpinBox { background: #ffffff; color: #111; border: 1px solid #ccc; }
                QPushButton { background: #f0f0f0; color: #111; border: 1px solid #ccc; padding: 4px 8px; }
                QPushButton:disabled { color: #888; }
                QTabWidget::pane { border: 1px solid #ccc; }
                QTabBar::tab { background: #ffffff; color: #111; padding: 4px 8px; }
                QTabBar::tab:selected { background: #e6e6e6; }
            """)
            pg.setConfigOption('background', 'w')
            pg.setConfigOption('foreground', 'k')
            self.cpu_plot.setBackground(bg_light)
            self.mem_plot.setBackground(bg_light)
            for ax in ('left', 'bottom'):
                self.cpu_plot.getAxis(ax).setTextPen(fg_light)
                self.cpu_plot.getAxis(ax).setPen(fg_light)
                self.mem_plot.getAxis(ax).setTextPen(fg_light)
                self.mem_plot.getAxis(ax).setPen(fg_light)
        self.dark_mode = dark

    # Close ---------------------------------------------------------------
    def closeEvent(self, event):
        self._save_config(pid=self.last_pid, name=self.last_process_name)
        if self.worker:
            self.worker.stop()
            self.worker.wait(500)
        event.accept()


def run_cli(args):
    pid = args.pid
    name = args.name
    interval = args.interval
    cpu_th = args.cpu_threshold
    mem_th = args.mem_threshold
    reconnect = args.reconnect

    def find_proc():
        if pid:
            try:
                return psutil.Process(pid)
            except psutil.NoSuchProcess:
                return None
        for proc in psutil.process_iter(["name", "pid"]):
            if proc.info["name"] and proc.info["name"].lower() == name.lower():
                return proc
        return None

    proc = find_proc()
    if not proc:
        print("Waiting for process...")
    if proc:
        proc.cpu_percent(None)
    while True:
        proc = proc if proc and proc.is_running() else find_proc()
        if not proc:
            if not reconnect:
                print("Process ended")
                break
            time.sleep(interval / 1000)
            continue
        ts = time.time()
        cpu = proc.cpu_percent(None) / max(psutil.cpu_count() or 1, 1)
        mem = proc.memory_info().rss / (1024 * 1024)
        host_cpu = psutil.cpu_percent(None)
        host_mem = psutil.virtual_memory().percent
        print(f"{ts:.3f}, cpu={cpu:.2f}%, mem={mem:.2f}MB, host={host_cpu:.1f}%/{host_mem:.1f}%")
        if cpu_th and cpu >= cpu_th:
            print(f"CPU alert: {cpu:.1f}% >= {cpu_th}%")
        if mem_th and mem >= mem_th:
            print(f"Mem alert: {mem:.1f}MB >= {mem_th}MB")
        time.sleep(interval / 1000)


def main():
    import argparse

    QCoreApplication.setOrganizationName(APP_NAME)
    QCoreApplication.setApplicationName("App")

    parser = argparse.ArgumentParser(description="Process memory/CPU monitor")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode (no GUI)")
    parser.add_argument("--pid", type=int, help="PID to monitor")
    parser.add_argument("--name", type=str, default="", help="Process name to monitor")
    parser.add_argument("--interval", type=int, default=1000, help="Interval ms")
    parser.add_argument("--cpu-threshold", type=int, default=0)
    parser.add_argument("--mem-threshold", type=int, default=0)
    parser.add_argument("--reconnect", action="store_true", help="Auto reconnect to process")
    args, _ = parser.parse_known_args()

    if args.cli:
        run_cli(args)
        return

    app = QApplication(sys.argv)
    window = ProcessMonitorApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
