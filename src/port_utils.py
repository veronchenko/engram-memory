"""Free-port resolution shared by server.py and dashboard/__main__.py."""

from __future__ import annotations

import socket


def find_free_port(host: str, start_port: int, max_attempts: int = 100) -> int:
    """
    Find a free TCP port, scanning upward from start_port.

    Args:
        host: Address to probe (0.0.0.0 probes all interfaces).
        start_port: First port to try.
        max_attempts: How many consecutive ports to try before giving up.

    Returns:
        The first free port found, starting at start_port.

    Raises:
        RuntimeError: No free port found within max_attempts.
    """

    probe_host = "" if host == "0.0.0.0" else host

    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((probe_host, port))
            except OSError:
                continue
            return port

    raise RuntimeError(
        f"No free port found in range [{start_port}, {start_port + max_attempts})"
    )
