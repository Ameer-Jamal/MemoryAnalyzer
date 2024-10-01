import sys
import time
import matplotlib.dates as mdates
from datetime import datetime
import psutil
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout
from PyQt5.QtCore import QTimer, Qt
import json
import os

CONFIG_FILE = "process_monitor_config.json"


class ProcessMonitorApp(QWidget):
    def __init__(self):
        super().__init__()
        self.process_name = ""
        self.interval = 1000  # Default 1 second interval
        self.monitoring = False
        self.process = None
        self.cpu_usage = []
        self.memory_usage = []
        self.times = []
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_process)

        # Load saved config
        self.load_config()

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Process input
        self.process_label = QLabel("Process Name to Monitor:")
        self.process_input = QLineEdit()
        self.process_input.setText(self.process_name)
        layout.addWidget(self.process_label)
        layout.addWidget(self.process_input)

        # Interval input
        self.interval_label = QLabel("Interval (in ms):")
        self.interval_input = QLineEdit()
        self.interval_input.setText(str(self.interval))
        layout.addWidget(self.interval_label)
        layout.addWidget(self.interval_input)

        # Status label
        self.status_label = QLabel("Status: Not monitoring")
        layout.addWidget(self.status_label)

        # Play/Pause button
        button_layout = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_monitoring)
        button_layout.addWidget(self.play_button)

        # Stop button
        self.stop_button = QPushButton("Stop & Generate Report")
        self.stop_button.clicked.connect(self.stop_monitoring)
        button_layout.addWidget(self.stop_button)

        # Reset button
        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(self.reset_monitor)
        button_layout.addWidget(self.reset_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)
        self.setWindowTitle("Process Monitor")

    def toggle_monitoring(self):
        if self.monitoring:
            self.monitoring = False
            self.play_button.setText("Play")
            self.status_label.setText("Monitoring paused")
            self.timer.stop()
        else:
            self.start_monitoring()

    def start_monitoring(self):
        try:
            self.interval = int(self.interval_input.text())
        except ValueError:
            self.status_label.setText("Error: Invalid interval value.")
            return

        self.process_name = self.process_input.text()
        if not self.process_name:
            self.status_label.setText("Error: Please enter a process name.")
            return

        # Save the config
        self.save_config()

        self.monitoring = True
        self.play_button.setText("Pause")
        self.status_label.setText(f"Monitoring process '{self.process_name}'...")
        self.timer.start(self.interval)

    def stop_monitoring(self):
        self.monitoring = False
        self.play_button.setText("Play")
        self.status_label.setText("Monitoring stopped")
        self.timer.stop()
        self.open_chart()

    def reset_monitor(self):
        self.monitoring = False
        self.play_button.setText("Play")
        self.status_label.setText("Status: Not monitoring")
        self.timer.stop()

        # Clear all data
        self.cpu_usage = []
        self.memory_usage = []
        self.times = []

        # Reload Configs
        self.load_config()

    def check_process(self):
        if not self.process or not self.process.is_running():
            self.find_process()

        if self.process:
            self.log_usage()
        else:
            self.status_label.setText(f"Waiting for process '{self.process_name}' to start...")

    def find_process(self):
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] == self.process_name:
                self.process = proc
                self.status_label.setText(f"Monitoring process '{self.process_name}'...")
                break

    def log_usage(self):
        try:
            if self.process and self.process.is_running():
                cpu = self.process.cpu_percent() / psutil.cpu_count()
                memory = self.process.memory_info().rss / (1024 * 1024)  # Memory in MB
                current_time = time.time()

                self.times.append(current_time)
                self.cpu_usage.append(cpu)
                self.memory_usage.append(memory)

                if len(self.times) > 1000:
                    self.times = self.times[-1000:]
                    self.cpu_usage = self.cpu_usage[-1000:]
                    self.memory_usage = self.memory_usage[-1000:]
            else:
                self.status_label.setText(f"Process '{self.process_name}' stopped.")
                self.stop_monitoring()

        except psutil.NoSuchProcess:
            self.status_label.setText(f"Process '{self.process_name}' no longer exists.")
            self.stop_monitoring()
        except Exception as e:
            self.status_label.setText(f"An error occurred: {str(e)}")
            self.stop_monitoring()

    def open_chart(self):
        if not self.times:
            return  # No data to display

        # Convert Unix timestamps to human-readable time format
        formatted_times = [datetime.fromtimestamp(t) for t in self.times]

        # Get the current range of times to adjust the x-axis
        time_min, time_max = min(formatted_times), max(formatted_times)

        # Subplot for CPU Usage
        plt.figure(figsize=(10, 6))

        # CPU Usage Plot
        plt.subplot(2, 1, 1)
        plt.plot(formatted_times, self.cpu_usage, label="CPU Usage (%)")
        plt.ylim(0, max(self.cpu_usage) + 5)  # Ensure no negative values on y-axis
        plt.xlim(time_min, time_max)  # Dynamic x-axis based on time
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))  # Format x-axis
        plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())  # Auto format the time ticks
        plt.legend()
        plt.title('CPU Usage Over Time')

        # Memory Usage Plot
        plt.subplot(2, 1, 2)
        plt.plot(formatted_times, self.memory_usage, label="Memory Usage (MB)", color='red')
        plt.ylim(0, max(self.memory_usage) + 5)  # Dynamic y-axis, no negative values
        plt.xlim(time_min, time_max)  # Dynamic x-axis based on time
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))  # Format x-axis
        plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())  # Auto format the time ticks
        plt.legend()
        plt.title('Memory Usage Over Time')

        plt.tight_layout()

        # Save the chart with a timestamp in the filename
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"process_monitor_chart_{timestamp}.png"
        plt.savefig(filename)  # Save the chart with the timestamp

        # Notify the user where the file is saved
        self.status_label.setText(f"Chart saved as {filename}")

        # Show the chart
        plt.show()

    def save_config(self):
        config = {
            "process_name": self.process_name,
            "interval": self.interval
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                self.process_name = config.get("process_name", "")
                self.interval = config.get("interval", 1000)
        else:
            self.process_name = ""
            self.interval = 1000


def main():
    app = QApplication(sys.argv)
    monitor_app = ProcessMonitorApp()
    monitor_app.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
