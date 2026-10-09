
use serde::Serialize;
use std::io::{self, Write};
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};
use sysinfo::{Disks, Networks, System};

#[derive(Serialize, Clone)]
struct MemoryInfo {
    used_gb: f64,
    total_gb: f64,
    percentage: f64,
}

#[derive(Serialize, Clone)]
struct DiskInfo {
    mount: String,
    used_gb: f64,
    total_gb: f64,
    percentage: f64,
    read_bytes_per_sec: u64,
    write_bytes_per_sec: u64,
}

#[derive(Serialize, Clone)]
struct NetworkInfo {
    interface: String,
    download_bytes_per_sec: u64,
    upload_bytes_per_sec: u64,
}

#[derive(Serialize, Clone)]
struct ProcessInfo {
    pid: u32,
    name: String,
    executable: String,
    cpu: f32,
    memory_mb: f64,
    memory_percent: f64,
    status: String,
    parent_pid: Option<u32>,
}

#[derive(Serialize, Clone, Default)]
struct BatteryInfo {
    available: bool,
    percentage: Option<u8>,
    power_source: String,
    status: String,
    time_remaining: String,
}

#[derive(Serialize, Clone, Default)]
struct ThermalInfo {
    available: bool,
    status: String,
}

#[derive(Serialize, Clone, Default)]
struct MemoryDiagnostics {
    swap_used_gb: f64,
    swap_total_gb: f64,
    swap_percentage: f64,
    pressure_estimate: String,
    memory_pressure: String,
    note: String,
}

#[derive(Serialize, Clone)]
struct SystemInfo {
    uptime_seconds: u64,
    processes: usize,
    load_one: f64,
    load_five: f64,
    load_fifteen: f64,
}

#[derive(Serialize)]
struct DashboardData {
    cpu: f32,
    cpu_cores: Vec<f32>,
    memory: MemoryInfo,
    swap: MemoryInfo,
    memory_diagnostics: MemoryDiagnostics,
    battery: BatteryInfo,
    thermal: ThermalInfo,
    disks: Vec<DiskInfo>,
    network: Vec<NetworkInfo>,
    processes: Vec<ProcessInfo>,
    system: SystemInfo,
}

fn bytes_to_gb(bytes: u64) -> f64 {
    bytes as f64 / 1_073_741_824.0
}

fn percentage(used: u64, total: u64) -> f64 {
    if total == 0 {
        0.0
    } else {
        used as f64 / total as f64 * 100.0
    }
}

fn rate(bytes: u64, seconds: f64) -> u64 {
    if seconds <= 0.0 {
        0
    } else {
        (bytes as f64 / seconds).min(u64::MAX as f64) as u64
    }
}

fn command_output(program: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(program).args(args).output().ok()?;

    if !output.status.success() {
        return None;
    }

    Some(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn read_battery() -> BatteryInfo {
    let Some(output) = command_output("/usr/bin/pmset", &["-g", "batt"]) else {
        return BatteryInfo::default();
    };

    let mut info = BatteryInfo {
        available: false,
        percentage: None,
        power_source: "Unknown".to_string(),
        status: "Unavailable".to_string(),
        time_remaining: "Unknown".to_string(),
    };

    for line in output.lines() {
        if line.contains("AC Power") {
            info.power_source = "AC Power".to_string();
        } else if line.contains("Battery Power") {
            info.power_source = "Battery Power".to_string();
        }

        if let Some(percent_end) = line.find('%') {
            let start = line[..percent_end]
                .rfind(|c: char| !c.is_ascii_digit())
                .map(|index| index + 1)
                .unwrap_or(0);

            if let Ok(value) = line[start..percent_end].parse::<u8>() {
                if value <= 100 {
                    info.percentage = Some(value);
                    info.available = true;
                }
            }

            if line.contains("charging") {
                info.status = "Charging".to_string();
            } else if line.contains("discharging") {
                info.status = "Discharging".to_string();
            } else if line.contains("charged") {
                info.status = "Charged".to_string();
            }

            if let Some((_, rest)) = line.split_once("; ") {
                let fields: Vec<&str> = rest.split(';').collect();

                if let Some(time) = fields.get(1) {
                    let time = time.trim();

                    if time.contains(':') {
                        info.time_remaining = time.to_string();
                    }
                }
            }
        }
    }

    info
}

fn read_thermal() -> ThermalInfo {
    let Some(output) = command_output("/usr/bin/pmset", &["-g", "therm"]) else {
        return ThermalInfo {
            available: false,
            status: "Not exposed by this Mac".to_string(),
        };
    };

    let text = output.trim();

    if text.is_empty() {
        return ThermalInfo {
            available: false,
            status: "Not exposed by this Mac".to_string(),
        };
    }

    let lower = text.to_lowercase();

    let status = if lower.contains("no thermal warning")
        || lower.contains("nominal")
    {
        "Nominal".to_string()
    } else if lower.contains("thermal warning") {
        "Thermal warning reported".to_string()
    } else {
        text.lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .collect::<Vec<_>>()
            .join(" | ")
    };

    ThermalInfo {
        available: true,
        status,
    }
}

fn read_memory_pressure() -> String {
    let Some(output) = command_output("/usr/bin/memory_pressure", &["-Q"]) else {
        return "Unavailable".to_string();
    };

    let output = output
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join(" | ");

    if output.is_empty() {
        "No output from macOS".to_string()
    } else {
        output
    }
}

fn read_swap_diagnostics(
    swap: &MemoryInfo,
    memory_pressure: &str,
) -> MemoryDiagnostics {
    let swap_estimate = if swap.percentage >= 75.0 {
        "High swap usage"
    } else if swap.percentage >= 40.0 {
        "Elevated swap usage"
    } else {
        "Low swap usage"
    };

    MemoryDiagnostics {
        swap_used_gb: swap.used_gb,
        swap_total_gb: swap.total_gb,
        swap_percentage: swap.percentage,
        pressure_estimate: swap_estimate.to_string(),
        memory_pressure: memory_pressure.to_string(),
        note: "macOS memory pressure and swap usage are different metrics."
            .to_string(),
    }
}

fn main() {
    let mut system = System::new_all();
    let mut networks = Networks::new_with_refreshed_list();
    let mut disks = Disks::new_with_refreshed_list();

    system.refresh_cpu_all();
    networks.refresh(true);
    disks.refresh(true);

    thread::sleep(Duration::from_millis(700));

    let mut previous_sample = Instant::now();

    // These measurements change relatively slowly, so poll every 15 seconds.
    let mut last_diagnostics_refresh = Instant::now();
    let mut battery = read_battery();
    let mut thermal = read_thermal();
    let mut memory_pressure = read_memory_pressure();

    loop {
        system.refresh_cpu_all();
        system.refresh_memory();
        system.refresh_processes(
            sysinfo::ProcessesToUpdate::All,
            true,
        );

        networks.refresh(true);
        disks.refresh(true);

        let now = Instant::now();
        let elapsed = now
            .duration_since(previous_sample)
            .as_secs_f64()
            .max(0.001);

        previous_sample = now;

        if now.duration_since(last_diagnostics_refresh)
            >= Duration::from_secs(15)
        {
            battery = read_battery();
            thermal = read_thermal();
            memory_pressure = read_memory_pressure();
            last_diagnostics_refresh = now;
        }

        let cpu_cores: Vec<f32> = system
            .cpus()
            .iter()
            .map(|core| core.cpu_usage())
            .collect();

        let cpu = if cpu_cores.is_empty() {
            0.0
        } else {
            cpu_cores.iter().sum::<f32>() / cpu_cores.len() as f32
        };

        let total_memory = system.total_memory();
        let used_memory = system.used_memory();

        let memory = MemoryInfo {
            used_gb: bytes_to_gb(used_memory),
            total_gb: bytes_to_gb(total_memory),
            percentage: percentage(used_memory, total_memory),
        };

        let total_swap = system.total_swap();
        let used_swap = system.used_swap();

        let swap = MemoryInfo {
            used_gb: bytes_to_gb(used_swap),
            total_gb: bytes_to_gb(total_swap),
            percentage: percentage(used_swap, total_swap),
        };

        let memory_diagnostics =
            read_swap_diagnostics(&swap, &memory_pressure);

        let network_info: Vec<NetworkInfo> = networks
            .iter()
            .map(|(name, network)| NetworkInfo {
                interface: name.clone(),
                download_bytes_per_sec: rate(
                    network.received(),
                    elapsed,
                ),
                upload_bytes_per_sec: rate(
                    network.transmitted(),
                    elapsed,
                ),
            })
            .collect();

        let disk_info: Vec<DiskInfo> = disks
            .iter()
            .map(|disk| {
                let total = disk.total_space();
                let available = disk.available_space();
                let used = total.saturating_sub(available);
                let usage = disk.usage();

                DiskInfo {
                    mount: disk.mount_point().display().to_string(),
                    used_gb: bytes_to_gb(used),
                    total_gb: bytes_to_gb(total),
                    percentage: percentage(used, total),
                    read_bytes_per_sec: rate(
                        usage.read_bytes,
                        elapsed,
                    ),
                    write_bytes_per_sec: rate(
                        usage.written_bytes,
                        elapsed,
                    ),
                }
            })
            .collect();

        let processes: Vec<ProcessInfo> = system
            .processes()
            .values()
            .map(|process| ProcessInfo {
                pid: process.pid().as_u32(),
                name: process.name().to_string_lossy().to_string(),
                executable: process
                    .exe()
                    .map(|path| path.display().to_string())
                    .unwrap_or_default(),
                cpu: process.cpu_usage(),
                memory_mb: process.memory() as f64 / 1_048_576.0,
                memory_percent: percentage(
                    process.memory(),
                    total_memory,
                ),
                status: format!("{:?}", process.status()),
                parent_pid: process.parent().map(|pid| pid.as_u32()),
            })
            .collect();

        let load = System::load_average();

        let system_info = SystemInfo {
            uptime_seconds: System::uptime(),
            processes: processes.len(),
            load_one: load.one,
            load_five: load.five,
            load_fifteen: load.fifteen,
        };

        let dashboard = DashboardData {
            cpu,
            cpu_cores,
            memory,
            swap,
            memory_diagnostics,
            battery: battery.clone(),
            thermal: thermal.clone(),
            disks: disk_info,
            network: network_info,
            processes,
            system: system_info,
        };

        if let Ok(json) = serde_json::to_string(&dashboard) {
            println!("{json}");
            let _ = io::stdout().flush();
        }

        thread::sleep(Duration::from_secs(1));
    }
}
