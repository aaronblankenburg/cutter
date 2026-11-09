"""Addresses under which the review app can be reached: localhost, the LAN IP and, on macOS, <hostname>.local."""
import platform
import socket
import subprocess


def lan_ip():
    """The address of the interface that leads to the internet (no packet is sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def addresses(port, bind="0.0.0.0"):
    if bind not in ("0.0.0.0", "::", ""):
        return [f"http://{bind}:{port}"]
    out = [f"http://localhost:{port}"]
    ip = lan_ip()
    if ip and not ip.startswith("127."):
        out.append(f"http://{ip}:{port}")
    if platform.system() == "Darwin":
        try:
            name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=3).stdout.strip()
            if name:
                out.append(f"http://{name}.local:{port}")
        except (OSError, subprocess.TimeoutExpired):
            pass
    return out
