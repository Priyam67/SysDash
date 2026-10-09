
from __future__ import annotations

import re
import subprocess
import threading
from collections import deque
from typing import Optional

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


POWERMETRICS = "/usr/bin/powermetrics"
HISTORY_SIZE = 48
SPARK_CHARS = "▁▂▃▄▅▆▇█"

POWER_LABELS = {
    "cpu_power": "CPU POWER",
    "gpu_power": "GPU POWER",
    "ane_power": "ANE POWER",
    "combined_power": "COMBINED POWER",
}

POWER_COLORS = {
    "cpu_power": "#89b4fa",
    "gpu_power": "#cba6f7",
    "ane_power": "#a6e3a1",
    "combined_power": "#f9e2af",
}


def parse_power_metrics(sample: str) -> dict:
    """Extract only metrics actually reported by powermetrics."""
    result = {}

    patterns = {
        "cpu_power": r"\bCPU Power:\s*([\d.]+)\s*(mW|W)\b",
        "gpu_power": r"\bGPU Power:\s*([\d.]+)\s*(mW|W)\b",
        "ane_power": r"\bANE Power:\s*([\d.]+)\s*(mW|W)\b",
        "combined_power": (
            r"\bCombined Power(?:\s*\([^)]*\))?:\s*"
            r"([\d.]+)\s*(mW|W)\b"
        ),
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, sample, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if match.group(2).lower() == "w":
                value *= 1000
            result[key] = value

    frequency = re.search(
        r"HW active frequency:\s*([\d.]+)\s*MHz",
        sample,
        re.IGNORECASE,
    )
    if frequency:
        result["active_frequency"] = float(frequency.group(1))

    thermal = re.search(
        r"Current pressure level:\s*([A-Za-z_-]+)",
        sample,
        re.IGNORECASE,
    )
    if thermal:
        result["thermal_pressure"] = (
            thermal.group(1).replace("_", " ").title()
        )

    return result


class HardwareTelemetryScreen(ModalScreen[None]):
    CSS = """
    HardwareTelemetryScreen {
        align: center middle;
        background: #0b0f17 90%;
    }

    #telemetry-frame {
        width: 96%;
        max-width: 120;
        height: 94%;
        border: round #45475a;
        background: #111827;
        padding: 1 2;
    }

    #telemetry-title {
        height: 2;
        color: #89b4fa;
        text-style: bold;
        content-align: center middle;
    }

    #telemetry-subtitle {
        height: 2;
        color: #a6adc8;
        padding: 0 1;
    }

    #auth-area {
        height: auto;
        padding: 1;
        border: round #45475a;
    }

    #password-input {
        margin: 1 0;
    }

    #auth-buttons {
        height: 3;
        align-horizontal: left;
        align-vertical: middle;
    }

    #metrics-area {
        height: 1fr;
        overflow-y: auto;
    }

    .metric-row {
        height: 7;
        width: 100%;
        margin-bottom: 1;
    }

    .metric-card {
        width: 1fr;
        height: 7;
        padding: 1 2;
        margin-right: 1;
        border: round #313b4c;
        background: #161f2e;
    }

    .metric-label {
        height: 1;
        color: #a6adc8;
        text-style: bold;
    }

    .metric-value {
        height: 2;
        color: #f1f5f9;
        text-style: bold;
    }

    .metric-note {
        height: 1;
        color: #7f8ba3;
    }

    #card-cpu {
        border: round #89b4fa;
    }

    #card-gpu {
        border: round #cba6f7;
    }

    #card-ane {
        border: round #a6e3a1;
    }

    #card-combined {
        border: round #f9e2af;
    }

    #card-frequency {
        border: round #94e2d5;
    }

    #card-thermal {
        border: round #f38ba8;
    }

    #power-history {
        height: 1fr;
        min-height: 12;
        border: round #45475a;
        background: #111827;
        padding: 1 2;
    }

    #history-title {
        height: 1;
        color: #cba6f7;
        text-style: bold;
    }

    #history-graph {
        height: 1fr;
        min-height: 5;
        color: #89b4fa;
    }

    #history-legend {
        height: 1;
        color: #a6adc8;
    }

    #telemetry-status {
        height: 2;
        color: #a6adc8;
        padding: 0 1;
    }

    #telemetry-buttons {
        height: 3;
        align-horizontal: left;
        align-vertical: middle;
    }

    Button {
        width: auto;
        min-width: 12;
        height: 3;
        padding: 0 2;
        margin: 0 1 0 0;
        border: none;
        background: #313244;
        color: #cdd6f4;
        text-style: bold;
    }

    Button:hover {
        background: #45475a;
        color: #ffffff;
    }

    #authenticate-button {
        width: 20;
        min-width: 20;
        height: 3;
        padding: 0 2;
        background: #0078d4;
        color: #ffffff;
        text-style: bold;
        border: none;
    }

    #authenticate-button:hover {
        background: #108de0;
        color: #ffffff;
    }

    #cancel-auth-button,
    #stop-button,
    #close-button {
        width: auto;
        min-width: 12;
        height: 3;
        background: #313244;
        color: #cdd6f4;
        border: none;
    }

    #cancel-auth-button:hover,
    #stop-button:hover,
    #close-button:hover {
        background: #45475a;
        color: #ffffff;
    }
    """

    BINDINGS = [
        ("escape", "close_screen", "Close"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._process: Optional[subprocess.Popen] = None
        self._stopping = threading.Event()
        self._authenticated = False
        self._history = {
            key: deque(maxlen=HISTORY_SIZE)
            for key in POWER_LABELS
        }

    def compose(self) -> ComposeResult:
        with Vertical(id="telemetry-frame"):
            yield Label(
                "SYSDASH  /  HARDWARE TELEMETRY",
                id="telemetry-title",
            )
            yield Static(
                "APPLE SILICON  ·  LIVE POWER & PERFORMANCE",
                id="telemetry-subtitle",
            )

            with Vertical(id="auth-area"):
                yield Label("Administrator authentication")
                yield Input(
                    placeholder="Enter your macOS password",
                    password=True,
                    id="password-input",
                )
                with Horizontal(id="auth-buttons"):
                    yield Button(
                        "Authenticate",
                        id="authenticate-button",
                    )
                    yield Button(
                        "Cancel",
                        id="cancel-auth-button",
                    )

            with Vertical(id="metrics-area"):
                with Horizontal(classes="metric-row"):
                    with Vertical(
                        id="card-cpu",
                        classes="metric-card",
                    ):
                        yield Static("CPU POWER", classes="metric-label")
                        yield Static("Waiting…", id="cpu-value",
                                     classes="metric-value")
                        yield Static("Processor", classes="metric-note")

                    with Vertical(
                        id="card-gpu",
                        classes="metric-card",
                    ):
                        yield Static("GPU POWER", classes="metric-label")
                        yield Static("Waiting…", id="gpu-value",
                                     classes="metric-value")
                        yield Static("Graphics", classes="metric-note")

                with Horizontal(classes="metric-row"):
                    with Vertical(
                        id="card-ane",
                        classes="metric-card",
                    ):
                        yield Static("ANE POWER", classes="metric-label")
                        yield Static("Waiting…", id="ane-value",
                                     classes="metric-value")
                        yield Static("Neural engine", classes="metric-note")

                    with Vertical(
                        id="card-combined",
                        classes="metric-card",
                    ):
                        yield Static(
                            "COMBINED POWER",
                            classes="metric-label",
                        )
                        yield Static(
                            "Waiting…",
                            id="combined-value",
                            classes="metric-value",
                        )
                        yield Static(
                            "Reported power domain",
                            classes="metric-note",
                        )

                with Horizontal(classes="metric-row"):
                    with Vertical(
                        id="card-frequency",
                        classes="metric-card",
                    ):
                        yield Static(
                            "ACTIVE FREQUENCY",
                            classes="metric-label",
                        )
                        yield Static(
                            "Waiting…",
                            id="frequency-value",
                            classes="metric-value",
                        )
                        yield Static(
                            "Reported active frequency",
                            classes="metric-note",
                        )

                    with Vertical(
                        id="card-thermal",
                        classes="metric-card",
                    ):
                        yield Static(
                            "THERMAL PRESSURE",
                            classes="metric-label",
                        )
                        yield Static(
                            "Waiting…",
                            id="thermal-value",
                            classes="metric-value",
                        )
                        yield Static(
                            "macOS pressure state",
                            classes="metric-note",
                        )

                with Vertical(id="power-history"):
                    yield Static("POWER HISTORY", id="history-title")
                    yield Static(
                        "Waiting for measurements…",
                        id="history-graph",
                    )
                    yield Static(
                        "CPU  ·  GPU  ·  ANE  ·  COMBINED",
                        id="history-legend",
                    )

            yield Static(
                "Waiting for authentication.",
                id="telemetry-status",
            )

            with Horizontal(id="telemetry-buttons"):
                yield Button(
                    "Stop telemetry",
                    id="stop-button",
                    disabled=True,
                )
                yield Button("Close", id="close-button")

    def on_mount(self) -> None:
        self.query_one("#metrics-area").display = False
        self.query_one("#stop-button").display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "authenticate-button":
            self.authenticate()
        elif event.button.id == "cancel-auth-button":
            self.action_close_screen()
        elif event.button.id == "close-button":
            self.action_close_screen()
        elif event.button.id == "stop-button":
            self.stop_telemetry()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "password-input":
            self.authenticate()

    def set_status(self, message: str) -> None:
        self.query_one("#telemetry-status", Static).update(message)

    def authenticate(self) -> None:
        if self._authenticated:
            return

        password_input = self.query_one("#password-input", Input)
        password = password_input.value
        password_input.value = ""

        if not password:
            self.set_status("Enter your macOS password to continue.")
            return

        self.query_one("#authenticate-button", Button).disabled = True
        self.set_status("Authenticating…")
        self.authenticate_worker(password)

    @work(thread=True, exclusive=True)
    def authenticate_worker(self, password: str) -> None:
        try:
            result = subprocess.run(
                ["/usr/bin/sudo", "-S", "-v"],
                input=password + "\n",
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.app.call_from_thread(
                self.authentication_finished,
                False,
                "Authentication timed out.",
            )
            return
        except Exception:
            self.app.call_from_thread(
                self.authentication_finished,
                False,
                "Could not run sudo authentication.",
            )
            return
        finally:
            password = ""

        if result.returncode != 0:
            self.app.call_from_thread(
                self.authentication_finished,
                False,
                "Authentication failed. Check your password and try again.",
            )
            return

        self.app.call_from_thread(
            self.authentication_finished,
            True,
            "Authenticated. Starting telemetry…",
        )

    def authentication_finished(
        self,
        success: bool,
        message: str,
    ) -> None:
        self.query_one("#authenticate-button", Button).disabled = False
        self.set_status(message)

        if not success:
            return

        self._authenticated = True
        self.query_one("#auth-area").display = False
        self.query_one("#metrics-area").display = True
        self.query_one("#stop-button").display = True
        self.query_one("#stop-button").disabled = False
        self.start_telemetry_worker()

    @work(thread=True, exclusive=True)
    def start_telemetry_worker(self) -> None:
        self._stopping.clear()

        try:
            self._process = subprocess.Popen(
                [
                    "/usr/bin/sudo",
                    "-n",
                    POWERMETRICS,
                    "--samplers",
                    "cpu_power,thermal",
                    "-i",
                    "1000",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            if self._process.stdout is None:
                self.app.call_from_thread(
                    self.set_status,
                    "Could not read powermetrics output.",
                )
                return

            self.app.call_from_thread(
                self.set_status,
                "Telemetry running · waiting for supported metrics…",
            )

            for line in self._process.stdout:
                if self._stopping.is_set():
                    break

                parsed = parse_power_metrics(line)
                if parsed:
                    self.app.call_from_thread(
                        self.update_metrics,
                        parsed,
                    )

            if not self._stopping.is_set():
                self.app.call_from_thread(
                    self.set_status,
                    "Telemetry process ended.",
                )

        except Exception as exc:
            if not self._stopping.is_set():
                self.app.call_from_thread(
                    self.set_status,
                    f"Telemetry error: {exc}",
                )
        finally:
            self._terminate_process()

    def update_metrics(self, metrics: dict) -> None:
        widget_map = {
            "cpu_power": ("#cpu-value", "mW"),
            "gpu_power": ("#gpu-value", "mW"),
            "ane_power": ("#ane-value", "mW"),
            "combined_power": ("#combined-value", "mW"),
            "active_frequency": ("#frequency-value", "MHz"),
        }

        for key, value in metrics.items():
            if key in widget_map:
                widget_id, unit = widget_map[key]
                self.query_one(widget_id, Static).update(
                    f"{value:,.0f} {unit}"
                )
                if key in self._history:
                    self._history[key].append(float(value))

            elif key == "thermal_pressure":
                self.query_one("#thermal-value", Static).update(
                    str(value)
                )

        self.update_power_history()

        available = [
            POWER_LABELS[key]
            for key in POWER_LABELS
            if self._history[key]
        ]
        if available:
            self.set_status("LIVE  ·  " + "  /  ".join(available))

    def update_power_history(self) -> None:
        lines = []

        for key, label in POWER_LABELS.items():
            values = list(self._history[key])
            if not values:
                lines.append(
                    f"[{POWER_COLORS[key]}]{label:<15}[/] "
                    "Waiting for samples…"
                )
                continue

            scale = max(max(values), 1.0)
            graph = self.sparkline(values, 28, scale)
            lines.append(
                f"[{POWER_COLORS[key]}]{label:<15}[/] "
                f"{graph}  {values[-1]:,.0f} mW"
            )

        self.query_one("#history-graph", Static).update(
            "\n".join(lines)
        )
        self.query_one("#history-legend", Static).update(
            "[#89b4fa]CPU[/]  "
            "[#cba6f7]GPU[/]  "
            "[#a6e3a1]ANE[/]  "
            "[#f9e2af]COMBINED[/]"
        )

    @staticmethod
    def sparkline(
        values: list[float],
        width: int,
        maximum: float,
    ) -> str:
        if not values:
            return "─" * width

        samples = values[-width:]
        scale = max(float(maximum), 1.0)
        graph = "".join(
            SPARK_CHARS[
                round(
                    max(0.0, min(1.0, value / scale))
                    * (len(SPARK_CHARS) - 1)
                )
            ]
            for value in samples
        )
        return graph.rjust(width)

    def stop_telemetry(self) -> None:
        self._stopping.set()
        self._terminate_process()
        self.set_status("Telemetry stopped.")
        self.query_one("#stop-button", Button).disabled = True

    def _terminate_process(self) -> None:
        process = self._process
        if process is None:
            return

        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        except Exception:
            pass
        finally:
            self._process = None

    def action_close_screen(self) -> None:
        self._stopping.set()
        self._terminate_process()
        self.dismiss(None)

    def on_unmount(self) -> None:
        self._stopping.set()
        self._terminate_process()
