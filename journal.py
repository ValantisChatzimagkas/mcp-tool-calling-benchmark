"""Append-only per-run journal, so an interrupted sweep resumes instead of restarting.

One JSON line per completed run. No config and no network: the caller passes every
value in, which keeps this testable offline and keeps the single-config property.

A record counts as "done" only if its whole key matches the run being asked for —
backend, endpoint, model, quantization, case id, case hash and run index — so a run
recorded under other conditions (another host, a different quantization, an edited
case) is never merged into a row stamped with the current conditions.
"""

import hashlib
import json
import os


def case_fingerprint(case, tools):
    """Hash of a case plus the tool schemas that actually reach the model."""
    payload = json.dumps({"case": case, "tools": tools}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def run_key(backend, endpoint, model, quantization, case_id, case_hash, run_index):
    return (backend, endpoint, model, quantization, case_id, case_hash, run_index)


def record_key(record):
    return run_key(
        record["backend"],
        record["endpoint"],
        record["model"],
        record["quantization"],
        record["case_id"],
        record["case_hash"],
        record["run_index"],
    )


def append(path, record):
    """Write one record as a single line and flush it to disk."""
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())


def load(path):
    """All parseable records. Blank lines and a torn final line (killed mid-write) are
    skipped; a missing file is an empty journal."""
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def archive(path, now):
    """Rename a finished sweep's journal to `<name>.<UTC timestamp>.jsonl` beside it.

    Returns the new path, or "" if there was no journal. Never overwrites an earlier
    archive: two sweeps finishing within the same second get a numeric suffix.
    """
    if not os.path.exists(path):
        return ""
    root, ext = os.path.splitext(path)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    target = f"{root}.{stamp}{ext}"
    suffix = 0
    while os.path.exists(target):
        suffix += 1
        target = f"{root}.{stamp}-{suffix}{ext}"
    os.rename(path, target)
    return target
