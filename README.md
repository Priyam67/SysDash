
# SysDash

A terminal-based system monitoring dashboard built with Python, Textual, and Rust for macOS.

SysDash combines a live system overview with a dedicated Apple Silicon hardware telemetry screen, bringing resource monitoring and low-level hardware measurements into a single terminal interface.

## Features

### System Dashboard
- Live CPU utilization and per-core usage
- Memory and swap utilization
- Network throughput and interface statistics
- Disk storage and read/write activity
- Top CPU-consuming and memory-consuming processes
- Process filtering and sortable tables
- Historical resource graphs
- Uptime, process count, and load averages

### Hardware Telemetry
- CPU power consumption
- GPU power consumption
- Apple Neural Engine (ANE) power, when reported
- Combined power readings, when reported
- Active frequency, when reported
- macOS thermal pressure
- Scrolling power-history graphs

Hardware telemetry uses Apple's `powermetrics` utility and may require administrator authentication. Available measurements depend on the Mac model and macOS version.

## Tech Stack

- **Python** — application logic and UI integration
- **Textual** — terminal user interface
- **Rich** — terminal formatting
- **Rust** — system metrics collection
- **sysinfo** — system information collection in the Rust engine
- **macOS powermetrics** — hardware power and performance telemetry

## Requirements

- macOS
- Python 3
- Rust and Cargo
- Xcode Command Line Tools
- Homebrew (recommended)

Some hardware telemetry features require administrator privileges.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/sysdash.git
cd sysdash
```

Replace `YOUR_USERNAME` with your GitHub username.

### 2. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
python -m pip install textual psutil rich
```

### 4. Build the Rust engine

```bash
cd engine/rust-engine
cargo build
cd ../..
```

If your macOS installation requires a specific SDK configuration, follow the build instructions for your local environment.

### 5. Launch SysDash

```bash
python main.py
```

Use `Ctrl+C` to exit the dashboard.

## Project Structure

```text
sysdash/
├── main.py
├── hardware_telemetry.py
├── engine/
│   └── rust-engine/
│       ├── Cargo.toml
│       ├── Cargo.lock
│       └── src/
│           └── main.rs
├── .gitignore
└── README.md
```

## Design

SysDash uses a dark terminal interface with live metrics, compact panels, and historical graphs. Python handles the interface, while the Rust engine collects system statistics.

Hardware telemetry is handled separately through macOS utilities.

## Limitations

- Hardware metrics vary by device and macOS version.
- Some telemetry requires elevated privileges.
- Hardware power readings do not necessarily represent total battery or wall power consumption.
- Metrics unavailable from the operating system are not guaranteed to appear.

## License

This project is currently unlicensed. Add a license before allowing others to reuse or redistribute the code.
