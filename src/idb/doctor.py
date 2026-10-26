"""Environment probe for the direct IDA Nexus CLI.

Verifies what idb actually needs WITHOUT importing ida_* into the CLI process.
IDA discovery and worker-backend selection are delegated to ida_nexus so doctor
reports exactly what an `idb open` would do: with the ida-nexus plugin installed
for IDA, new workers run in `idat`; otherwise (or with IDA_NEXUS_IDALIB_ONLY)
they run idalib in this interpreter, which needs an activated idapro package —
probed via a throwaway subprocess that really initializes the kernel.
"""

import importlib
import importlib.util
import subprocess
import sys


def _find_spec(name):
    # find_spec on a dotted name raises when the parent package is absent —
    # precisely the situation doctor exists to report, not crash on.
    try:
        return importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None


def _check_python():
    return ("python", "OK", f"{sys.version.split()[0]} @ {sys.executable}")


def _check_module(name):
    if _find_spec(name) is None:
        return (name, "MISSING", "install ida-nexus")
    try:
        importlib.import_module(name)
        return (name, "OK", "installed")
    except Exception as exc:
        return (name, "ERROR", str(exc))


def _check_idapro():
    if _find_spec("idapro") is None:
        return ("idapro", "MISSING",
                "pip install idapro, then run py-activate-idalib.py to activate it")
    code = "import idapro,sys; sys.stdout.write('%d.%d.%d' % idapro.get_library_version())"
    try:
        res = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return ("idapro", "ERROR", "kernel init timed out")
    if res.returncode != 0:
        tail = (res.stderr.strip().splitlines() or [""])[-1]
        return ("idapro", "ERROR", tail)
    return ("idapro", "OK", f"IDA library {res.stdout.strip()}")


def _check_ida_install():
    try:
        from ida_nexus.paths import find_ida_install_dir

        root = find_ida_install_dir()
    except Exception as exc:
        return ("ida-install", "ERROR", str(exc))
    if root is None:
        return ("ida-install", "MISSING",
                "set IDADIR or run py-activate-idalib.py (writes ida-config.json)")
    if not root.is_dir():
        return ("ida-install", "ERROR", f"{root} is not a directory")
    return ("ida-install", "OK", str(root))


def _worker_backend():
    """Return ``(row, backend)``; backend is None when no worker can start."""
    try:
        from ida_nexus._launch import select_backend

        backend, executable = select_backend(gui=False)
    except Exception as exc:
        return ("worker", "ERROR", str(exc)), None
    detail = executable if executable else "idalib in this interpreter"
    return ("worker", "OK", f"{backend}: {detail}"), backend


def _check_nexus_registry():
    try:
        from ida_nexus import discover_databases, get_state_dir

        instances = discover_databases()
        ready = sum(item.state.value == "ready" for item in instances)
        registry_dir = get_state_dir() / "instances"
        return (
            "nexus",
            "OK",
            f"{ready} ready / {len(instances)} registered in {registry_dir}",
        )
    except Exception as exc:
        return ("nexus", "ERROR", str(exc))


def run():
    """Return ``(rows, ok)`` where rows are ``(check, status, detail)``."""
    worker_row, backend = _worker_backend()
    rows = [
        _check_python(),
        _check_module("ida_nexus"),
        _check_ida_install(),
        worker_row,
    ]
    # idapro is only loaded by idalib workers; idat workers use IDA's Python.
    if backend == "idalib":
        rows.append(_check_idapro())
    rows.append(_check_nexus_registry())
    ok = all(status not in {"MISSING", "ERROR"} for _, status, _ in rows)
    return rows, ok
