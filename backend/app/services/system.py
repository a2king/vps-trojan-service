from __future__ import annotations

import os
import platform
import socket
import time
from datetime import datetime
from pathlib import Path

import psutil

from app.timeutil import TZ, to_iso

SKIP_FS = {
    "autofs",
    "bpf",
    "cgroup",
    "cgroup2",
    "configfs",
    "debugfs",
    "devpts",
    "devtmpfs",
    "efivarfs",
    "fusectl",
    "hugetlbfs",
    "mqueue",
    "overlay",
    "proc",
    "pstore",
    "ramfs",
    "securityfs",
    "squashfs",
    "sysfs",
    "tmpfs",
    "tracefs",
}


def _os_pretty() -> str:
    if platform.system() == "Linux":
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return f"{platform.system()} {platform.release()}".strip()


def _cpu_model() -> str:
    if platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine() or "未知"


def _load_avg() -> tuple[float | None, float | None, float | None]:
    try:
        one, five, fifteen = os.getloadavg()
        return float(one), float(five), float(fifteen)
    except (OSError, AttributeError):
        return None, None, None


def _ipv4s() -> list[str]:
    result: list[str] = []
    for name, addrs in psutil.net_if_addrs().items():
        lowered = name.lower()
        if lowered.startswith("lo") or lowered.startswith("docker") or lowered.startswith("br-"):
            continue
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            ip = addr.address
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if ip not in result:
                result.append(ip)
    return result


def _disks() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        fstype = (part.fstype or "").lower()
        if fstype in SKIP_FS:
            continue
        mount = part.mountpoint
        if mount in seen:
            continue
        try:
            usage = psutil.disk_usage(mount)
        except OSError:
            continue
        if int(usage.total) <= 0:
            continue
        seen.add(mount)
        rows.append(
            {
                "device": part.device,
                "mountpoint": mount,
                "fstype": part.fstype or "",
                "total": int(usage.total),
                "used": int(usage.used),
                "free": int(usage.free),
                "percent": round(float(usage.percent), 1),
            }
        )
    rows.sort(key=lambda item: (0 if item["mountpoint"] in {"/", "C:\\", "C:"} else 1, item["mountpoint"]))
    return rows


def collect_system() -> dict:
    per_core = [round(float(v), 1) for v in psutil.cpu_percent(interval=0.2, percpu=True)]
    cpu_percent = round(sum(per_core) / len(per_core), 1) if per_core else 0.0
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    load_1, load_5, load_15 = _load_avg()
    boot = datetime.fromtimestamp(psutil.boot_time(), tz=TZ).replace(tzinfo=None)
    now = datetime.now(TZ).replace(tzinfo=None)
    return {
        "hostname": platform.node() or socket.gethostname(),
        "os_name": _os_pretty(),
        "kernel": platform.version() if platform.system() == "Windows" else platform.release(),
        "arch": platform.machine() or platform.architecture()[0],
        "timezone": str(TZ),
        "boot_time": to_iso(boot),
        "uptime_seconds": int(max(0, time.time() - psutil.boot_time())),
        "collected_at": to_iso(now),
        "process_count": len(psutil.pids()),
        "ips": _ipv4s(),
        "cpu": {
            "percent": cpu_percent,
            "per_core": per_core,
            "model": _cpu_model(),
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True) or len(per_core),
            "load_1": load_1,
            "load_5": load_5,
            "load_15": load_15,
        },
        "memory": {
            "total": int(vm.total),
            "used": int(vm.used),
            "available": int(vm.available),
            "free": int(vm.free),
            "cached": int(getattr(vm, "cached", 0) or 0),
            "buffers": int(getattr(vm, "buffers", 0) or 0),
            "percent": round(float(vm.percent), 1),
            "swap_total": int(swap.total),
            "swap_used": int(swap.used),
            "swap_percent": round(float(swap.percent), 1),
        },
        "disks": _disks(),
    }
