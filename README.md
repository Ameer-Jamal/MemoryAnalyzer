# MemoryAnalyzer

MemoryAnalyzer is a Python-based GUI tool that allows you to monitor and log CPU and memory usage for a specific process in real-time. It uses `psutil` to track system resources and `PyQt5` for the graphical user interface (GUI). The tool also generates and saves charts showing CPU and memory usage over time.

![image](https://github.com/user-attachments/assets/d5159922-0de2-44af-8453-b65503464bda)
![image](https://github.com/user-attachments/assets/d1a644e0-a003-42fa-bf5f-28945d8f60e2)

## Features
- Monitor CPU and memory usage of a specific process in real-time.
- Customize monitoring interval in milliseconds.
- Pause, resume, and reset monitoring with ease.
- Automatically saves CPU and memory usage charts as images.
- User-friendly interface built with `PyQt5`.
- Configurations are saved between sessions (process name and interval).

## Requirements

Before running the application, ensure you have the following installed:

- Python 3.7+
- `psutil`
- `matplotlib`
- `PyQt5`

### Install Requirements
You can install the dependencies via `pip`:

```bash
pip install psutil matplotlib pyqt5
```
# Usage
Clone the Repository
```bash
git clone https://github.com/Ameer-Jamal/MemoryAnalyzer.git
cd MemoryAnalyzer
```

# Run the Application

Execute the Python script to launch the GUI:

```bash
python main.py
```
# Application Workflow

- Enter the process name to monitor in the "Process Name to Monitor" input field (e.g., chrome, python).
- Set the desired interval (in milliseconds) to determine how often the application checks the process’s resource usage.
- Click "Play" to start monitoring.
- Click "Stop & Generate Report" to stop monitoring and generate a chart showing CPU and memory usage over time.
- Click "Reset" to clear the current data and reset the interface.

- The application will also save the chart with a timestamp in the current working directory, such as process_monitor_chart_20241001-152047.png.

# Saved Configurations

The application automatically saves the process name and interval in process_monitor_config.json, allowing it to remember these settings between runs.

# Generated Charts

Once monitoring is stopped, the application generates and saves a chart in the current directory with a timestamp (e.g., process_monitor_chart_YYYYMMDD-HHMMSS.png).
The chart displays CPU usage (%) and memory usage (in MB) over time.


