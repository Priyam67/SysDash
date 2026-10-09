
import json
import queue
import subprocess
import threading
import time
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Input, DataTable, Button

from hardware_telemetry import HardwareTelemetryScreen

ROOT = Path(__file__).resolve().parent
RUST_ENGINE = ROOT / "engine/rust-engine/target/debug/rust-engine"

SPARK_CHARS = "▁▂▃▄▅▆▇█"
HISTORY_SIZE = 60
PROCESS_LIMIT = 30
UPDATE_INTERVAL = 1.0
STALE_AFTER_SECONDS = 5


class SysDash(App):
    TITLE = "SYSDASH"
    SUB_TITLE = "LIVE SYSTEM MONITOR"

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    CSS = """
    Screen {
        background: #11111b;
        color: #cdd6f4;
    }

    Header {
        height: 1;
        background: #1e1e2e;
        color: #cba6f7;
        text-style: bold;
    }

    Footer {
        height: 1;
        background: #1e1e2e;
        color: #cdd6f4;
    }

    #dashboard-toolbar {
        height: 3;
        width: 100%;
        padding: 0 1;
        align: center middle;
    }

    #hardware-telemetry-button {
        width: auto;
        min-width: 24;
        height: 3;
        margin: 0 1 0 0;
        padding: 0 2;
        background: #0078d4;
        color: #ffffff;
        text-style: bold;
        border: none;
    }

    #hardware-telemetry-button:hover {
        background: #108de0;
        color: #ffffff;
    }

    #hardware-telemetry-button:focus {
        border: none;
    }

    #top {
        height: 24%;
        min-height: 8;
        width: 100%;
    }

    #middle {
        height: 21%;
        min-height: 8;
        width: 100%;
    }

    #diagnostics {
        height: 15%;
        min-height: 6;
        width: 100%;
        margin: 0 1;
        padding: 1;
        border: round #45475a;
        overflow-y: auto;
    }

    #process-toolbar {
        height: 3;
        width: 100%;
        padding: 0 1;
        align: center middle;
    }

    #process-filter {
        width: 100%;
        border: round #89b4fa;
        background: #181825;
    }

    #process-area {
        height: 1fr;
        min-height: 8;
        width: 100%;
    }

    .panel {
        width: 1fr;
        height: 100%;
        min-width: 0;
        margin: 0 1;
        padding: 1;
        overflow-y: auto;
    }

    #cpu {
        border: round #89b4fa;
    }

    #memory {
        border: round #a6e3a1;
    }

    #network {
        border: round #cba6f7;
    }

    #disk {
        border: round #f9e2af;
    }

    .process-panel {
        width: 1fr;
        height: 100%;
        min-width: 0;
        margin: 0 1;
        border: round #45475a;
    }

    #cpu-process-panel {
        border: round #f38ba8;
    }

    #memory-process-panel {
        border: round #fab387;
    }

    .process-title {
        height: 1;
        padding: 0 1;
        text-style: bold;
    }

    #cpu-process-title {
        color: #f38ba8;
    }

    #memory-process-title {
        color: #fab387;
    }

    DataTable {
        height: 1fr;
        width: 100%;
        background: #11111b;
        color: #cdd6f4;
    }

    DataTable > .datatable--header {
        background: #1e1e2e;
        color: #cba6f7;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #45475a;
        color: #ffffff;
    }

    DataTable > .datatable--hover {
        background: #313244;
    }

    Input {
        background: #181825;
        color: #cdd6f4;
    }

    Button {
        background: #45475a;
        color: #cdd6f4;
    }

    Button:hover {
        background: #585b70;
    }

    #status {
        height: 1;
        margin: 0 1;
        padding: 0 1;
        color: #a6adc8;
        background: #181825;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()

        yield Horizontal(
            Button(
                "Hardware Telemetry",
                id="hardware-telemetry-button",
                variant="primary",
            ),
            id="dashboard-toolbar",
        )

        yield Horizontal(
            Static(
                "Connecting to Rust engine...",
                id="cpu",
                classes="panel",
            ),
            Static(
                "Loading memory...",
                id="memory",
                classes="panel",
            ),
            id="top",
        )

        yield Horizontal(
            Static(
                "Loading network...",
                id="network",
                classes="panel",
            ),
            Static(
                "Loading disk...",
                id="disk",
                classes="panel",
            ),
            id="middle",
        )

        yield Static(
            "POWER / DIAGNOSTICS  |  Waiting for macOS metrics...",
            id="diagnostics",
        )

        yield Horizontal(
            Input(
                placeholder="Filter by process name, PID or path...",
                id="process-filter",
            ),
            id="process-toolbar",
        )

        yield Horizontal(
            Vertical(
                Static(
                    "TOP CPU PROCESSES",
                    classes="process-title",
                    id="cpu-process-title",
                ),
                DataTable(
                    id="process-cpu",
                    cursor_type="row",
                    zebra_stripes=True,
                ),
                classes="process-panel",
                id="cpu-process-panel",
            ),
            Vertical(
                Static(
                    "TOP MEMORY PROCESSES",
                    classes="process-title",
                    id="memory-process-title",
                ),
                DataTable(
                    id="process-memory",
                    cursor_type="row",
                    zebra_stripes=True,
                ),
                classes="process-panel",
                id="memory-process-panel",
            ),
            id="process-area",
        )

        yield Static(
            "Waiting for system information...",
            id="status",
        )

        yield Footer()

    def on_mount(self) -> None:
        self.data_queue = queue.Queue(maxsize=2)

        self.cpu_history = []
        self.memory_history = []
        self.network_down_history = []
        self.network_up_history = []

        self.core_history = {}
        self.disk_io_history = {}

        self.rust_process = None
        self.latest_data = None
        self.last_data_received_at = None
        self.engine_error = None

        self.sort_config = {
            "process-cpu": ("cpu", True),
            "process-memory": ("memory_mb", True),
        }

        for table_id in ("process-cpu", "process-memory"):
            table = self.query_one(f"#{table_id}", DataTable)
            table.add_column("PID", key="pid", width=8)
            table.add_column("PROCESS", key="name", width=22)
            table.add_column("CPU %", key="cpu", width=8)
            table.add_column("RAM MB", key="memory_mb", width=10)
            table.add_column("RAM %", key="memory_percent", width=8)

        if not RUST_ENGINE.is_file():
            self.engine_error = (
                f"Rust engine not found at:\n{RUST_ENGINE}\n\n"
                "Build it with cargo build."
            )
            self.query_one("#cpu", Static).update(
                "[bold red]RUST ENGINE NOT FOUND[/bold red]\n\n"
                f"{escape(self.engine_error)}"
            )
            self.query_one("#status", Static).update(
                "[bold red]Engine unavailable[/bold red]"
            )
            return

        try:
            self.rust_process = subprocess.Popen(
                [str(RUST_ENGINE)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.engine_error = str(exc)
            self.query_one("#cpu", Static).update(
                "[bold red]RUST ENGINE ERROR[/bold red]\n\n"
                f"{escape(str(exc))}\n\n"
                "Build the engine with cargo build."
            )
            self.query_one("#status", Static).update(
                "[bold red]Engine failed to start[/bold red]"
            )
            return

        threading.Thread(
            target=self.read_engine,
            daemon=True,
        ).start()

        threading.Thread(
            target=self.read_engine_errors,
            daemon=True,
        ).start()

        self.set_interval(UPDATE_INTERVAL, self.update_dashboard)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "hardware-telemetry-button":
            self.push_screen(HardwareTelemetryScreen())

    def read_engine(self) -> None:
        """Read JSON snapshots without blocking the Textual UI."""
        process = self.rust_process

        if process is None or process.stdout is None:
            return

        try:
            for line in process.stdout:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if self.data_queue.full():
                    try:
                        self.data_queue.get_nowait()
                    except queue.Empty:
                        pass

                try:
                    self.data_queue.put_nowait(data)
                    self.last_data_received_at = time.monotonic()
                except queue.Full:
                    pass

        except (OSError, ValueError):
            pass

    def read_engine_errors(self) -> None:
        """Capture engine errors separately so stderr cannot block."""
        process = self.rust_process

        if process is None or process.stderr is None:
            return

        try:
            for line in process.stderr:
                message = line.strip()
                if message:
                    self.engine_error = message[-500:]
        except (OSError, ValueError):
            pass

    def update_dashboard(self) -> None:
        if self.rust_process is None:
            return

        if self.rust_process.poll() is not None:
            message = "RUST ENGINE STOPPED"
            if self.engine_error:
                message += f"\n\n{escape(self.engine_error)}"

            self.query_one("#cpu", Static).update(
                f"[bold red]{message}[/bold red]"
            )
            self.query_one("#status", Static).update(
                "[bold red]Engine stopped; restart SysDash to reconnect[/bold red]"
            )
            return

        newest = None

        while True:
            try:
                newest = self.data_queue.get_nowait()
            except queue.Empty:
                break

        if newest is not None:
            self.latest_data = newest
            try:
                self.render_dashboard(newest)
            except (KeyError, TypeError, ValueError) as exc:
                self.query_one("#status", Static).update(
                    "[bold red]Invalid metrics received from Rust engine[/bold red] "
                    f"({escape(str(exc))})"
                )
            return

        if self.last_data_received_at is not None:
            age = time.monotonic() - self.last_data_received_at

            if age > STALE_AFTER_SECONDS:
                self.query_one("#status", Static).update(
                    f"[bold yellow]STALE DATA[/bold yellow]  "
                    f"No new Rust metrics for {age:.0f}s"
                )

    def on_input_changed(self, event: Input.Changed) -> None:
        if (
            event.input.id == "process-filter"
            and self.latest_data is not None
        ):
            self.render_process_tables(self.latest_data)

    def on_data_table_header_selected(
        self,
        event: DataTable.HeaderSelected,
    ) -> None:
        table_id = event.data_table.id

        if table_id not in self.sort_config:
            return

        field = getattr(
            event.column_key,
            "value",
            str(event.column_key),
        )

        valid_fields = {
            "pid",
            "name",
            "cpu",
            "memory_mb",
            "memory_percent",
        }

        if field not in valid_fields:
            return

        current_field, current_reverse = self.sort_config[table_id]

        if current_field == field:
            reverse = not current_reverse
        else:
            reverse = field != "name"

        self.sort_config[table_id] = (field, reverse)

        if self.latest_data is not None:
            self.render_process_tables(self.latest_data)

    def render_dashboard(self, data: dict) -> None:
        cpu = float(data["cpu"])
        cores = data["cpu_cores"]
        memory = data["memory"]
        swap = data["swap"]

        self.remember(self.cpu_history, cpu)
        self.remember(self.memory_history, memory["percentage"])

        for index, usage in enumerate(cores):
            history = self.core_history.setdefault(index, [])
            self.remember(history, usage)

        cpu_text = (
            "[bold #89b4fa]CPU[/bold #89b4fa]  "
            f"[bold white]{cpu:5.1f}%[/bold white]\n"
            f"[#89b4fa]{self.graph(self.cpu_history, 36, 100)}"
            "[/#89b4fa]\n"
            f"[dim]{len(cores)} logical cores · 60-sample history[/dim]\n\n"
        )

        for index, usage in enumerate(cores):
            cpu_text += (
                f"[#89b4fa]C{index:02d}[/#89b4fa] "
                f"{self.bar(usage, 10, '#89b4fa')} "
                f"{usage:5.1f}% "
                f"[dim]{self.graph(self.core_history[index], 10, 100)}[/dim]\n"
            )

        self.query_one("#cpu", Static).update(cpu_text)

        memory_text = (
            "[bold #a6e3a1]MEMORY[/bold #a6e3a1]  "
            f"[bold white]{memory['percentage']:.1f}%[/bold white]\n"
            f"[#a6e3a1]{self.graph(self.memory_history, 36, 100)}"
            "[/#a6e3a1]\n\n"
            "[bold]RAM[/bold]\n"
            f"{self.bar(memory['percentage'], 22, '#a6e3a1')}\n"
            f"Used       {memory['used_gb']:.2f} GB\n"
            f"Available  "
            f"{max(0, memory['total_gb'] - memory['used_gb']):.2f} GB\n"
            f"Total      {memory['total_gb']:.2f} GB\n\n"
            "[bold]SWAP[/bold]\n"
            f"{self.bar(swap['percentage'], 22, '#cba6f7')}\n"
            f"Used       {swap['used_gb']:.2f} GB\n"
            f"Total      {swap['total_gb']:.2f} GB\n"
            f"Usage      {swap['percentage']:.1f}%"
        )

        self.query_one("#memory", Static).update(memory_text)

        interfaces = data["network"]

        download = sum(
            item["download_bytes_per_sec"]
            for item in interfaces
        )
        upload = sum(
            item["upload_bytes_per_sec"]
            for item in interfaces
        )

        self.remember(self.network_down_history, download)
        self.remember(self.network_up_history, upload)

        network_scale = self.adaptive_scale(
            self.network_down_history + self.network_up_history
        )

        network_text = (
            "[bold #cba6f7]NETWORK[/bold #cba6f7]\n\n"
            f"[green]↓ {self.format_rate(download)}[/green]\n"
            f"[blue]↑ {self.format_rate(upload)}[/blue]\n\n"
            f"[green]{self.graph(self.network_down_history, 36, network_scale)}"
            "[/green]\n"
            f"[blue]{self.graph(self.network_up_history, 36, network_scale)}"
            "[/blue]\n"
            f"[dim]Adaptive shared scale: {self.format_rate(network_scale)}"
            "[/dim]\n\n"
            "[bold]INTERFACES[/bold]\n"
        )

        for item in interfaces:
            network_text += (
                f"\n[bold]{escape(item['interface'])}[/bold]\n"
                f"[green]↓ {self.format_rate(item['download_bytes_per_sec'])}"
                "[/green]  "
                f"[blue]↑ {self.format_rate(item['upload_bytes_per_sec'])}"
                "[/blue]\n"
            )

        self.query_one("#network", Static).update(network_text)

        disk_text = "[bold #f9e2af]DISK / STORAGE[/bold #f9e2af]\n"

        for disk in data["disks"]:
            mount = disk["mount"]

            history = self.disk_io_history.setdefault(
                mount,
                {"read": [], "write": []},
            )

            self.remember(history["read"], disk["read_bytes_per_sec"])
            self.remember(history["write"], disk["write_bytes_per_sec"])

            read_scale = self.adaptive_scale(history["read"])
            write_scale = self.adaptive_scale(history["write"])

            disk_text += (
                f"\n[bold]{escape(mount)}[/bold]\n"
                f"{self.bar(disk['percentage'], 22, '#f9e2af')}\n"
                f"{disk['percentage']:.1f}% used\n"
                f"{disk['used_gb']:.1f} / {disk['total_gb']:.1f} GB\n"
                f"[green]R {self.format_rate(disk['read_bytes_per_sec'])}"
                "[/green]  "
                f"[blue]W {self.format_rate(disk['write_bytes_per_sec'])}"
                "[/blue]\n"
                f"[green]{self.graph(history['read'], 22, read_scale)}"
                "[/green]\n"
                f"[blue]{self.graph(history['write'], 22, write_scale)}"
                "[/blue]\n"
            )

        self.query_one("#disk", Static).update(disk_text)

        self.render_diagnostics(data)
        self.render_process_tables(data)

        system = data["system"]

        self.query_one("#status", Static).update(
            f"UPTIME {self.format_uptime(system['uptime_seconds'])}"
            f"   |   PROCESSES {system['processes']}"
            f"   |   LOAD {system['load_one']:.2f} / "
            f"{system['load_five']:.2f} / "
            f"{system['load_fifteen']:.2f}"
            f"   |   REFRESH {UPDATE_INTERVAL:.0f}s"
        )

    def render_diagnostics(self, data: dict) -> None:
        battery = data.get("battery", {})
        thermal = data.get("thermal", {})
        diagnostics = data.get("memory_diagnostics", {})

        if battery.get("available"):
            battery_percent = battery.get("percentage")

            battery_text = (
                f"{battery_percent}%"
                if battery_percent is not None
                else "Level unavailable"
            )

            power_text = (
                f"[bold #a6e3a1]BATTERY[/bold #a6e3a1]  "
                f"{battery_text}  |  "
                f"{escape(battery.get('status', 'Unknown'))}  |  "
                f"{escape(battery.get('power_source', 'Unknown'))}  |  "
                f"Remaining: "
                f"{escape(battery.get('time_remaining', 'Unknown'))}"
            )
        else:
            power_text = (
                "[bold #a6e3a1]BATTERY[/bold #a6e3a1]  "
                "Data unavailable or no battery detected"
            )

        thermal_text = (
            f"[bold #f9e2af]THERMAL[/bold #f9e2af]  "
            f"{escape(thermal.get('status', 'Unavailable'))}"
        )

        swap_text = (
            f"[bold #cba6f7]SWAP[/bold #cba6f7]  "
            f"{diagnostics.get('swap_percentage', 0.0):.1f}% used  |  "
            f"{escape(diagnostics.get('pressure_estimate', 'Unknown'))}"
        )

        pressure_text = (
            f"[bold #89b4fa]MACOS MEMORY[/bold #89b4fa]  "
            f"{escape(diagnostics.get('memory_pressure', 'Unavailable'))}"
        )

        note = diagnostics.get("note", "")

        self.query_one("#diagnostics", Static).update(
            "[bold]POWER / DIAGNOSTICS[/bold]\n"
            f"{power_text}\n"
            f"{thermal_text}\n"
            f"{swap_text}\n"
            f"{pressure_text}\n"
            f"[dim]{escape(note)}[/dim]"
        )

    def render_process_tables(self, data: dict) -> None:
        query = (
            self.query_one("#process-filter", Input)
            .value.strip()
            .casefold()
        )

        processes = data.get("processes", [])

        if query:
            processes = [
                process
                for process in processes
                if query in process.get("name", "").casefold()
                or query in str(process.get("pid", ""))
                or query in process.get("executable", "").casefold()
            ]

        for table_id in ("process-cpu", "process-memory"):
            table = self.query_one(f"#{table_id}", DataTable)
            field, reverse = self.sort_config[table_id]

            sorted_processes = sorted(
                processes,
                key=lambda process: (
                    process.get(field, 0)
                    if field != "name"
                    else process.get("name", "").casefold()
                ),
                reverse=reverse,
            )[:PROCESS_LIMIT]

            table.clear(columns=False)

            for process in sorted_processes:
                name = (
                    process.get("name", "?")
                    .replace("\n", " ")
                    .replace("\r", " ")[:22]
                )

                table.add_row(
                    str(process["pid"]),
                    name,
                    f"{process['cpu']:.1f}",
                    f"{process['memory_mb']:.1f}",
                    f"{process['memory_percent']:.1f}",
                    key=str(process["pid"]),
                )

            if not sorted_processes:
                table.add_row(
                    "—",
                    "No matches",
                    "—",
                    "—",
                    "—",
                    key="no-matches",
                )

    @staticmethod
    def remember(history: list, value: float) -> None:
        history.append(float(value))
        del history[:-HISTORY_SIZE]

    @staticmethod
    def adaptive_scale(
        values: list,
        percentile: float = 0.95,
    ) -> float:
        """Choose a robust graph scale without letting one spike dominate."""
        clean = sorted(max(0.0, float(value)) for value in values)

        if not clean:
            return 1.0

        index = min(
            len(clean) - 1,
            int((len(clean) - 1) * percentile),
        )

        return max(clean[index], 1.0)

    @staticmethod
    def graph(
        values: list,
        width: int = 36,
        maximum: float | None = None,
    ) -> str:
        if not values:
            return "─" * width

        samples = values[-width:]
        scale = max(float(maximum or max(samples)), 1.0)

        bars = "".join(
            SPARK_CHARS[
                round(
                    max(0.0, min(1.0, value / scale))
                    * (len(SPARK_CHARS) - 1)
                )
            ]
            for value in samples
        )

        return bars.rjust(width)

    @staticmethod
    def bar(
        value: float,
        width: int = 18,
        color: str = "#89b4fa",
    ) -> str:
        value = max(0.0, min(100.0, value))
        filled = round(width * value / 100)
        empty = width - filled

        return (
            f"[{color}]" + "█" * filled + "[/]"
            + "[#45475a]" + "░" * empty + "[/]"
        )

    @staticmethod
    def format_rate(byte_rate: float) -> str:
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        rate = max(0.0, float(byte_rate))
        index = 0

        while rate >= 1000 and index < len(units) - 1:
            rate /= 1000
            index += 1

        return f"{rate:.1f} {units[index]}"

    @staticmethod
    def format_uptime(seconds: int) -> str:
        days, remaining = divmod(seconds, 86400)
        hours, remaining = divmod(remaining, 3600)
        minutes, _ = divmod(remaining, 60)

        if days:
            return f"{days}d {hours}h {minutes}m"

        return f"{hours}h {minutes}m"

    def on_unmount(self) -> None:
        """Stop the Rust telemetry process cleanly when SysDash exits."""
        process = getattr(self, "rust_process", None)

        if process is not None and process.poll() is None:
            process.terminate()

            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass


if __name__ == "__main__":
    SysDash().run()
