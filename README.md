# MemoryAnalyzer

MemoryAnalyzer is a Python-based GUI tool that lets you monitor and log CPU and memory usage for one **or many** processes in real-time. It uses `psutil` for sampling, `PyQt5` for the GUI, and `pyqtgraph` for live charts. Data can be streamed to CSV while you monitor.

![image](https://github.com/user-attachments/assets/d5159922-0de2-44af-8453-b65503464bda)
![image](https://github.com/user-attachments/assets/d1a644e0-a003-42fa-bf5f-28945d8f60e2)

## Features
- Live charts for CPU (%) and Memory (MB) using pyqtgraph.
- Monitor multiple processes at once (select one + add extra PIDs or names).
- Adjustable sampling interval (100 ms – 10 s) with auto-reconnect.
- Threshold alerts for CPU and Memory.
- System context: host CPU% and RAM% overlay.
- Stream samples to CSV automatically; export chart to PNG.
- Dark/light theme toggle; settings persist between sessions (processes, thresholds, interval, theme).
- Optional dropdown grouping by process name, plus “grouped averages” view to see averaged CPU/mem per name instead of per-PID lines.
- Headless CLI mode for servers/CI: `python main.py --cli --pid 1234`.

## Requirements

- Python 3.8+
- psutil
- pyqtgraph
- PyQt5
- platformdirs

### Install Dependencies
```bash
pip install -r requirements.txt
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

- Pick one primary process from the dropdown; optionally add extra PIDs or process names (comma separated).
- Toggle “Group dropdown by name” if you prefer selecting by name instead of individual PID entries.
- Set the interval in milliseconds and toggle auto-reconnect if you want it to reattach when the process restarts.
- (Optional) enter CPU% / Memory MB thresholds for alerts.
- Click **Start** to begin live monitoring; charts update in real time.
- Click **Stop** to end monitoring. Data is streamed to a timestamped CSV in the app log directory; you can also “Save CSV As…” to copy it elsewhere.
- Use **Save Chart** to export the current CPU plot as PNG.
- **Reset** clears buffers and UI state.
- Use “Show grouped averages only” to collapse traces by process name (averaged CPU/mem).

# Saved Configurations

Settings (processes, interval, thresholds, theme) persist in `~/.MemoryAnalyzer/process_monitor_config.json`.

# Generated Charts

Use **Save Chart** to export the live CPU chart as PNG. CSV logs contain per-sample CPU%, memory MB, PID, name, plus host CPU/mem snapshots.

# Tests

Install dev deps and run:
```bash
pip install -r requirements-dev.txt
pytest
```

# CLI Mode (headless)

You can run sampling without the GUI (useful for servers/CI):

```bash
python main.py --cli --pid 1234 --interval 500 --cpu-threshold 200 --mem-threshold 1024 --reconnect
```

If no PID is found it will wait and reconnect when the process appears (unless `--reconnect` is omitted).
