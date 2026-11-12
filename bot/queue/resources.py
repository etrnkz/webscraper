"""Runtime resource probing for crawl admission control.

Reads cgroup CPU quota and available memory to derive the maximum
concurrent crawls and per-crawl page-worker count.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResourceReading:
    """A single resource measurement.

    Attributes:
        cpu_quota_cores: CPU cores from cgroup quota, or None if unlimited/unknown.
        cpu_affinity_cores: CPU cores from sched_getaffinity (host-visible cores).
        cpu_source: Description of the CPU measurement source.
        memory_available_mb: Host MemAvailable in megabytes.
        memory_current_mb: Current memory usage in megabytes (from cgroup if available).
        memory_limit_mb: Cgroup memory limit in megabytes, or None if unlimited.
        memory_source: Description of the memory measurement source.
        measured_at: Monotonic timestamp of the measurement.
    """
    cpu_quota_cores: Optional[float]
    cpu_affinity_cores: int
    cpu_source: str
    memory_available_mb: int
    memory_current_mb: int
    memory_limit_mb: Optional[int]
    memory_source: str
    measured_at: float


def _read_cpu_quota_v2(cgroup_path: Path) -> Optional[float]:
    """Read CPU quota from cgroup v2 cpu.max file.

    Returns cores as float, or None if unlimited/unreadable.
    """
    cpu_max = cgroup_path / "cpu.max"
    if not cpu_max.exists():
        return None
    try:
        content = cpu_max.read_text().strip()
        # Format: "200000 100000" or "max 100000"
        parts = content.split()
        if len(parts) != 2:
            return None
        quota_str, period_str = parts
        if quota_str == "max":
            return None
        quota = int(quota_str)
        period = int(period_str)
        if quota <= 0 or period <= 0:
            return None
        return quota / period
    except (ValueError, OSError):
        return None


def _read_cpu_quota_v1(cgroup_path: Path) -> Optional[float]:
    """Read CPU quota from cgroup v1 cpu.cfs_quota_us / cpu.cfs_period_us.

    Returns cores as float, or None if unlimited/unreadable.
    """
    quota_file = cgroup_path / "cpu.cfs_quota_us"
    period_file = cgroup_path / "cpu.cfs_period_us"
    if not (quota_file.exists() and period_file.exists()):
        return None
    try:
        quota = int(quota_file.read_text().strip())
        period = int(period_file.read_text().strip())
        if quota <= 0 or period <= 0:
            return None
        return quota / period
    except (ValueError, OSError):
        return None


def _read_memory_v2(cgroup_path: Path) -> tuple[Optional[int], Optional[int]]:
    """Read memory from cgroup v2 memory.max / memory.current.

    Returns (limit_mb, current_mb) or (None, None) if unreadable.
    """
    max_file = cgroup_path / "memory.max"
    current_file = cgroup_path / "memory.current"
    if not (max_file.exists() and current_file.exists()):
        return (None, None)
    try:
        limit_str = max_file.read_text().strip()
        current = int(current_file.read_text().strip())
        if limit_str == "max":
            return (None, current // (1024 * 1024))
        limit = int(limit_str)
        # Treat very large limits as unlimited
        if limit >= (1 << 60):
            return (None, current // (1024 * 1024))
        return (limit // (1024 * 1024), current // (1024 * 1024))
    except (ValueError, OSError):
        return (None, None)


def _read_memory_v1(cgroup_path: Path) -> tuple[Optional[int], Optional[int]]:
    """Read memory from cgroup v1 memory.limit_in_bytes / memory.usage_in_bytes.

    Returns (limit_mb, current_mb) or (None, None) if unreadable.
    """
    limit_file = cgroup_path / "memory.limit_in_bytes"
    usage_file = cgroup_path / "memory.usage_in_bytes"
    if not (limit_file.exists() and usage_file.exists()):
        return (None, None)
    try:
        limit = int(limit_file.read_text().strip())
        usage = int(usage_file.read_text().strip())
        if limit >= (1 << 60):
            return (None, usage // (1024 * 1024))
        return (limit // (1024 * 1024), usage // (1024 * 1024))
    except (ValueError, OSError):
        return (None, None)


def _find_cgroup_mount(controller: str) -> Optional[Path]:
    """Find the cgroup mount point for a given controller (cpu or memory)."""
    try:
        with open("/proc/self/mountinfo", "r") as f:
            for line in f:
                parts = line.strip().split()
                # mountinfo format: mount_id parent_id major:minor root mount_point mount_options ...
                if len(parts) >= 5:
                    mount_point = Path(parts[4])
                    opts = parts[5] if len(parts) > 5 else ""
                    if controller in opts:
                        return mount_point
    except OSError:
        pass
    return None


def _get_process_cgroup_path(controller: str) -> Optional[Path]:
    """Get this process's cgroup path for a specific controller."""
    try:
        with open("/proc/self/cgroup", "r") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 3:
                    controllers = parts[1].split(",")
                    if controller in controllers:
                        rel_path = parts[2].lstrip("/")
                        return Path(rel_path)
    except OSError:
        pass
    return None


def read_resources() -> ResourceReading:
    """Read current CPU and memory resources.

    Resolves the process's own cgroup (handles nested cgroups via /proc/self/cgroup
    and mountinfo). Falls back to sched_getaffinity and /proc/meminfo.

    Never raises - returns a reading with best-effort values.
    """
    import time

    measured_at = time.monotonic()

    # CPU: try cgroup v2, then v1, then affinity, then cpu_count
    cpu_quota_cores = None
    cpu_source = "cpu_count"
    cpu_affinity_cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)

    # Find our cgroup path for cpu controller
    cgroup_rel = _get_process_cgroup_path("cpu")
    if cgroup_rel:
        # Try v2 first
        cgroup_mount = _find_cgroup_mount("cpu")
        if cgroup_mount:
            cgroup_path = cgroup_mount / cgroup_rel
            cpu_quota_cores = _read_cpu_quota_v2(cgroup_path)
            if cpu_quota_cores is not None:
                cpu_source = "cgroup_v2"
            else:
                cpu_quota_cores = _read_cpu_quota_v1(cgroup_path)
                if cpu_quota_cores is not None:
                    cpu_source = "cgroup_v1"

    if cpu_quota_cores is None and cpu_affinity_cores > 0:
        cpu_quota_cores = float(cpu_affinity_cores)
        cpu_source = "affinity"

    # Memory: try cgroup v2, then v1, then meminfo
    memory_available_mb = 0
    memory_current_mb = 0
    memory_limit_mb = None
    memory_source = "meminfo"

    # Read MemAvailable from /proc/meminfo
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    memory_available_mb = int(line.split()[1]) // 1024
                    break
    except OSError:
        memory_available_mb = 0

    # Try cgroup memory
    cgroup_rel_mem = _get_process_cgroup_path("memory")
    if cgroup_rel_mem:
        cgroup_mount = _find_cgroup_mount("memory")
        if cgroup_mount:
            cgroup_path = cgroup_mount / cgroup_rel_mem
            limit_mb, current_mb = _read_memory_v2(cgroup_path)
            if limit_mb is not None or current_mb > 0:
                memory_limit_mb = limit_mb
                memory_current_mb = current_mb
                memory_source = "cgroup_v2"
            else:
                limit_mb, current_mb = _read_memory_v1(cgroup_path)
                if limit_mb is not None or current_mb > 0:
                    memory_limit_mb = limit_mb
                    memory_current_mb = current_mb
                    memory_source = "cgroup_v1"

    # If cgroup didn't give us current usage, fall back to estimating from MemAvailable
    if memory_current_mb == 0:
        # We can't easily get total - available without more parsing; leave as 0
        pass

    reading = ResourceReading(
        cpu_quota_cores=cpu_quota_cores,
        cpu_affinity_cores=cpu_affinity_cores,
        cpu_source=cpu_source,
        memory_available_mb=memory_available_mb,
        memory_current_mb=memory_current_mb,
        memory_limit_mb=memory_limit_mb,
        memory_source=memory_source,
        measured_at=measured_at,
    )

    logger.debug(
        "Resource reading: cpu_quota=%s (%s), affinity=%d, "
        "mem_avail=%d MB, mem_current=%d MB, mem_limit=%s (%s)",
        reading.cpu_quota_cores,
        reading.cpu_source,
        reading.cpu_affinity_cores,
        reading.memory_available_mb,
        reading.memory_current_mb,
        reading.memory_limit_mb,
        reading.memory_source,
    )

    if cpu_source in ("affinity", "cpu_count"):
        logger.warning(
            "CPU quota detection fell back to %s (cgroup read failed or unlimited). "
            "This may over-report capacity on shared hosts.",
            cpu_source,
        )

    return reading


def derive_capacity(
    reading: ResourceReading,
    max_concurrent: int,
    memory_budget_mb: int,
    memory_reserve_mb: int,
) -> tuple[int, int]:
    """Derive (capacity, workers_per_crawl) from a resource reading.

    Args:
        reading: ResourceReading from read_resources().
        max_concurrent: Hard ceiling on concurrent crawls (CRAWL_MAX_CONCURRENT).
        memory_budget_mb: Assumed RAM per crawl (CRAWL_MEMORY_BUDGET_MB).
        memory_reserve_mb: RAM held back for sibling apps (CRAWL_MEMORY_RESERVE_MB).

    Returns:
        (capacity, workers_per_crawl) where:
        - capacity: max concurrent crawls (0 means queue-only, no admission)
        - workers_per_crawl: page workers per crawl (>= 1)
    """
    # CPU slots: at least 1, from cgroup quota if available, else affinity
    cpu_slots = max(1, int(reading.cpu_quota_cores or reading.cpu_affinity_cores or 1))

    # Memory headroom: host available minus reserve, bounded by cgroup limit if present
    if reading.memory_limit_mb is not None:
        # cgroup-limited: remaining = limit - current
        headroom_mb = max(0, reading.memory_limit_mb - reading.memory_current_mb - memory_reserve_mb)
    else:
        # Host-limited: use MemAvailable minus reserve
        headroom_mb = max(0, reading.memory_available_mb - memory_reserve_mb)

    memory_slots = headroom_mb // max(1, memory_budget_mb)

    # Capacity is the minimum of CPU, memory, and the hard ceiling
    # Allow capacity to reach 0 (queue-only mode)
    capacity = min(cpu_slots, memory_slots, max_concurrent)
    capacity = max(0, capacity)

    # Workers per crawl: divide CPU slots among concurrent crawls
    # Ensures capacity * workers <= cpu_slots
    workers_per_crawl = max(1, cpu_slots // max(1, capacity))

    logger.info(
        "Derived capacity: cpu_slots=%d, memory_headroom=%d MB, "
        "memory_slots=%d, max_concurrent=%d => capacity=%d, workers=%d",
        cpu_slots,
        headroom_mb,
        memory_slots,
        max_concurrent,
        capacity,
        workers_per_crawl,
    )

    return (capacity, workers_per_crawl)