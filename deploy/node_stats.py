#!/usr/bin/env python3
"""远端系统采集，仅用标准库，打印 JSON。"""
from __future__ import annotations

import json
import os
import platform
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

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


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _os_pretty() -> str:
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return f"{platform.system()} {platform.release()}".strip()


def _cpu_model() -> str:
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.machine() or "未知"


def _parse_stat() -> tuple[list[int], list[list[int]]]:
    total: list[int] = []
    cores: list[list[int]] = []
    for line in _read("/proc/stat").splitlines():
        if line.startswith("cpu "):
            total = [int(x) for x in line.split()[1:]]
        elif line.startswith("cpu") and line[3:4].isdigit():
            cores.append([int(x) for x in line.split()[1:]])
    return total, cores


def _busy_idle(parts: list[int]) -> tuple[int, int]:
    if len(parts) < 4:
        return 0, 0
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    return total - idle, idle


def _cpu_usage() -> tuple[float, list[float]]:
    first_total, first_cores = _parse_stat()
    time.sleep(0.2)
    second_total, second_cores = _parse_stat()
    per_core: list[float] = []
    for a, b in zip(first_cores, second_cores):
        busy1, idle1 = _busy_idle(a)
        busy2, idle2 = _busy_idle(b)
        delta = (busy2 + idle2) - (busy1 + idle1)
        per_core.append(round(100.0 * (busy2 - busy1) / delta, 1) if delta > 0 else 0.0)
    busy1, idle1 = _busy_idle(first_total)
    busy2, idle2 = _busy_idle(second_total)
    delta = (busy2 + idle2) - (busy1 + idle1)
    percent = round(100.0 * (busy2 - busy1) / delta, 1) if delta > 0 else 0.0
    return percent, per_core


def _meminfo() -> dict[str, int]:
    result: dict[str, int] = {}
    for line in _read("/proc/meminfo").splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        num = "".join(ch for ch in raw if ch.isdigit())
        if num:
            result[key] = int(num) * 1024
    return result


def _ipv4s() -> list[str]:
    result: list[str] = []
    for line in _read("/proc/net/fib_trie").splitlines():
        pass
    try:
        for _name, addrs in socket.if_nameindex():
            pass
    except OSError:
        pass
    # 用 hostname -I 风格：遍历 getaddrinfo 不够，改读 ip -4
    try:
        import subprocess

        out = subprocess.check_output(["hostname", "-I"], text=True, timeout=3)
        for ip in out.split():
            if ip.startswith("127.") or ip.startswith("169.254.") or ":" in ip:
                continue
            if ip not in result:
                result.append(ip)
    except Exception:
        pass
    return result


def _disks() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mount, fstype = parts[0], parts[1], parts[2]
        if fstype in SKIP_FS:
            continue
        if mount in seen:
            continue
        try:
            usage = os.statvfs(mount)
        except OSError:
            continue
        total = int(usage.f_frsize * usage.f_blocks)
        free = int(usage.f_frsize * usage.f_bavail)
        used = max(0, total - int(usage.f_frsize * usage.f_bfree))
        if total <= 0:
            continue
        seen.add(mount)
        rows.append(
            {
                "device": device,
                "mountpoint": mount,
                "fstype": fstype,
                "total": total,
                "used": used,
                "free": free,
                "percent": round(100.0 * used / total, 1) if total else 0.0,
            }
        )
    rows.sort(key=lambda item: (0 if item["mountpoint"] == "/" else 1, item["mountpoint"]))
    return rows


def _load() -> tuple[float | None, float | None, float | None]:
    try:
        one, five, fifteen = os.getloadavg()
        return float(one), float(five), float(fifteen)
    except OSError:
        return None, None, None


def _btime() -> int:
    for line in _read("/proc/stat").splitlines():
        if line.startswith("btime "):
            return int(line.split()[1])
    return int(time.time())


def _process_count() -> int:
    count = 0
    try:
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                count += 1
    except OSError:
        pass
    return count


def _timezone() -> str:
    text = _read("/etc/timezone").strip()
    if text:
        return text
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "Asia/Shanghai"


def main() -> None:
    percent, per_core = _cpu_usage()
    mem = _meminfo()
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable", mem.get("MemFree", 0))
    free = mem.get("MemFree", 0)
    cached = mem.get("Cached", 0)
    buffers = mem.get("Buffers", 0)
    used = max(0, total - available)
    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    load_1, load_5, load_15 = _load()
    boot = _btime()
    now = datetime.now(timezone.utc).astimezone().replace(microsecond=0)
    boot_dt = datetime.fromtimestamp(boot).astimezone().replace(microsecond=0)
    payload = {
        "hostname": platform.node() or socket.gethostname(),
        "os_name": _os_pretty(),
        "kernel": platform.release(),
        "arch": platform.machine() or "",
        "timezone": _timezone(),
        "boot_time": boot_dt.isoformat(),
        "uptime_seconds": int(max(0, time.time() - boot)),
        "collected_at": now.isoformat(),
        "process_count": _process_count(),
        "ips": _ipv4s(),
        "cpu": {
            "percent": percent,
            "per_core": per_core,
            "model": _cpu_model(),
            "cores_physical": None,
            "cores_logical": len(per_core) or (os.cpu_count() or 1),
            "load_1": load_1,
            "load_5": load_5,
            "load_15": load_15,
        },
        "memory": {
            "total": total,
            "used": used,
            "available": available,
            "free": free,
            "cached": cached,
            "buffers": buffers,
            "percent": round(100.0 * used / total, 1) if total else 0.0,
            "swap_total": swap_total,
            "swap_used": swap_used,
            "swap_percent": round(100.0 * swap_used / swap_total, 1) if swap_total else 0.0,
        },
        "disks": _disks(),
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
