"""Shared helpers for the tests (standard library only).

Run all tests:  python -m unittest discover -s tests -v       (from the repository root)
Tests that need ffmpeg, the tool packages (numpy, librosa, OpenTimelineIO), filmcraft-cli, Docker or Chrome skip
themselves when these are missing. Test servers listen only on 127.0.0.1, ports 8290-8299.
"""
import base64
import importlib.util
import os
import pathlib
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.dont_write_bytecode = True
REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
TESTS = REPO / "tests"
GOLDEN = TESTS / "golden"
LAUNCHER = REPO / "bin" / "cutter"
PORTS = range(8290, 8300)
os.environ["CUTTER_TEST"] = "1"


def have_modules(*names):
    return all(importlib.util.find_spec(n) is not None for n in names)


def have_programs(*names):
    return all(shutil.which(n) for n in names)


TOOLS = have_modules("numpy", "opentimelineio", "otio_fcp_adapter")
AUDIO = TOOLS and have_modules("librosa")
FFMPEG = have_programs("ffmpeg", "ffprobe")


def free_port():
    """A free port on 127.0.0.1 in 8290-8299 (the range the tests may use)."""
    for port in PORTS:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port in 8290-8299")


def wait_for_port(port, timeout=20.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def make_htpasswd(folder, user="tester"):
    """A random password and an htpasswd file with only its scrypt hash, created at run time in a temp folder."""
    sys.path.insert(0, str(SRC / "cutter" / "review_app"))
    import set_password
    password = secrets.token_urlsafe(18)
    path = pathlib.Path(folder) / "htpasswd"
    path.write_text(f"{user}:{set_password.new_hash(password)}\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return password, path


def basic_auth(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_HOME = None


def test_home():
    """An empty HOME for cutter subprocesses: no ~/.ssh, no agent configuration of the person running the tests."""
    global _HOME
    if _HOME is None:
        import atexit
        _HOME = pathlib.Path(tempfile.mkdtemp(prefix="cutter_home_")).resolve()
        atexit.register(shutil.rmtree, _HOME, True)
    return _HOME


def run_cutter(*args, cwd=None, env=None, check=True, input=None):
    """Run the cutter CLI of this checkout in a subprocess (python -m cutter), with an empty HOME, without an ssh
    agent, and with CUTTER_TEST=1 (cutter then refuses to connect to any server and ignores the checkout's deploy.env)."""
    e = {k: v for k, v in os.environ.items() if k not in ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "CUTTER_DEPLOY_ENV")}
    e.update(PYTHONPATH=str(SRC), PYTHONDONTWRITEBYTECODE="1", CUTTER_TEST="1", HOME=str(test_home()))
    e.update(env or {})
    r = subprocess.run([sys.executable, "-m", "cutter", *map(str, args)], cwd=cwd, env=e, capture_output=True, text=True,
                       input=input)
    if check and r.returncode:
        raise AssertionError(f"cutter {' '.join(map(str, args))} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


class TempDir:
    """Context manager: a temporary folder that is removed afterwards (CUTTER_KEEP_TMP=1 keeps it)."""

    def __enter__(self):
        self.path = pathlib.Path(tempfile.mkdtemp(prefix="cutter_test_")).resolve()
        return self.path

    def __exit__(self, *exc):
        if not os.environ.get("CUTTER_KEEP_TMP"):
            shutil.rmtree(self.path, ignore_errors=True)
