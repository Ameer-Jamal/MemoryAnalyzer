from typing import Dict, List, Tuple


def parse_extra_pids(text: str) -> List[int]:
    """Parse comma-separated PIDs, keep order, drop non-digits and duplicates."""
    seen = set()
    pids: List[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        pid = int(part)
        if pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
    return pids


def parse_extra_names(text: str) -> List[str]:
    """Parse comma-separated names, keep order, dedup case-insensitively."""
    seen = set()
    names: List[str] = []
    for part in text.split(","):
        name = part.strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def parse_targets_text(text: str) -> Tuple[List[int], List[str]]:
    """Split a comma string into pids and names in one pass."""
    pids: List[int] = []
    names: List[str] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if token.isdigit():
            pid = int(token)
            if pid not in pids:
                pids.append(pid)
        else:
            key = token.lower()
            if key not in [n.lower() for n in names]:
                names.append(token)
    return pids, names


def build_targets(primary_pid, primary_name: str, extra_targets_text: str, extra_pids_text: str = "", extra_names_text: str = ""):
    """Construct target list dicts for monitoring."""
    targets = []
    seen = set()

    def add(pid, name):
        key = (pid if pid is not None else None, name.lower() if name else "")
        # skip duplicate name-only entries if name already monitored by a pid
        if pid is None and name and any(t["name"].lower() == name.lower() for t in targets):
            return
        if key in seen:
            return
        seen.add(key)
        targets.append({"pid": pid, "name": name or ""})

    if primary_pid or primary_name:
        add(primary_pid, primary_name or "")

    # unified extra targets (names or pids)
    extra_pids, extra_names = parse_targets_text(extra_targets_text)

    # keep backward compatibility with separate fields
    extra_pids += [p for p in parse_extra_pids(extra_pids_text) if p not in extra_pids]
    extra_names += [n for n in parse_extra_names(extra_names_text) if n.lower() not in [x.lower() for x in extra_names]]

    for pid in extra_pids:
        add(pid, "")

    for name in extra_names:
        add(None, name)

    return targets


def aggregate_latest_by_name(latest_by_pid: Dict[int, tuple]):
    """
    Given latest_by_pid mapping pid -> (ts, cpu, mem, name),
    return dict name -> (ts, avg_cpu, avg_mem) using latest timestamp across that name.
    """
    grouped: Dict[str, List[tuple]] = {}
    for pid, (ts, cpu, mem, name) in latest_by_pid.items():
        grouped.setdefault(name, []).append((ts, cpu, mem))

    result: Dict[str, tuple] = {}
    for name, rows in grouped.items():
        if not rows:
            continue
        ts = max(r[0] for r in rows)  # most recent sample among the group
        cpu_avg = sum(r[1] for r in rows) / len(rows)
        mem_avg = sum(r[2] for r in rows) / len(rows)
        result[name] = (ts, cpu_avg, mem_avg)
    return result


def dedup_process_items(items: List[tuple]) -> List[tuple]:
    """Remove duplicate PIDs, preserving first occurrence."""
    seen = set()
    out = []
    for pid, name in items:
        if pid in seen:
            continue
        seen.add(pid)
        out.append((pid, name))
    return out


def filter_process_items(items: List[tuple], query: str) -> List[tuple]:
    """Filter (pid, name) tuples by substring in name or pid string."""
    q = (query or "").strip().lower()
    if not q:
        return items
    result = []
    for pid, name in items:
        pid_str = str(pid)
        nm = name or ""
        if q in nm.lower() or q in pid_str:
            result.append((pid, name))
    return result
