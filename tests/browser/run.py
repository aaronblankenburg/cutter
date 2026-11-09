#!/usr/bin/env python3
"""Run the browser tests of the review app against synthetic test data (standard library only).

  python tests/browser/run.py                  test data, server, all tests in English, then a German smoke run
  python tests/browser/run.py --keep           keep the temporary folder (test data, server log)
  python tests/browser/run.py --only test_app.js --only test_width.js
  python tests/browser/run.py --fixture DIR    reuse test data made earlier with make_fixture.py DIR
  python tests/browser/run.py --shots DIR      screenshots of the tests into DIR/en and DIR/de

Makes a temporary folder in $TMPDIR, builds the test data there with make_fixture.py, starts server.py on 127.0.0.1
with the first free port of 8295-8299 and runs every test file with a fresh, empty comment database (the server is
restarted for each file): all tests with the UI language "en", then the German smoke run with "de". Prints the number
of checks per file and language; the exit code is 1 if a check failed or a test aborted.
Needs ffmpeg, node, Chrome (CHROME, default /usr/bin/google-chrome) and playwright-core ("npm ci" in tests/browser, or
NODE_PATH pointing to a node_modules folder that contains it).
"""
import argparse
import http.client
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
SERVER = REPO / "src" / "cutter" / "review_app" / "server.py"
HOST, PORTS, LOGIN = "127.0.0.1", range(8295, 8300), "tester"
RUNS = [("test_app.js", "en"), ("test_comments.js", "en"), ("test_new_cut.js", "en"), ("test_decisions.js", "en"),
        ("test_width.js", "en"), ("test_smoke_de.js", "de"), ("test_width.js", "de")]
TEST_TIMEOUT = 900


def port_free(port):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)     # like http.server, which reuses the address
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def server_ready(port, proc, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            c = http.client.HTTPConnection(HOST, port, timeout=2)
            c.request("GET", "/")
            status = c.getresponse().status
            c.close()
            if status == 401:                                       # up, and asking for the login
                return True
        except OSError:
            pass
        time.sleep(0.1)
    return False


class Server:
    """server.py on 127.0.0.1, restarted with an empty comment database for every test file."""

    def __init__(self, data, htpasswd, log):
        self.data, self.htpasswd, self.log = data, htpasswd, log
        self.port = self.proc = None

    def start(self, ui_language):
        for p in self.data.glob("feedback.sqlite*"):
            p.unlink()
        project = self.data / "project.json"
        d = json.loads(project.read_text(encoding="utf-8"))
        d["ui_language"] = ui_language
        project.write_text(json.dumps(d, indent=1), encoding="utf-8")
        order = ([self.port] if self.port else []) + [p for p in PORTS if p != self.port]
        for port in order:
            if not port_free(port):
                continue
            with open(self.log, "a", encoding="utf-8") as log:
                log.write(f"\n---- server on port {port}, ui_language {ui_language}\n")
                log.flush()
                proc = subprocess.Popen([sys.executable, str(SERVER), "--host", HOST, "--port", str(port), "--data", str(self.data),
                                         "--htpasswd", str(self.htpasswd)], stdout=log, stderr=subprocess.STDOUT,
                                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            if server_ready(port, proc):
                self.port, self.proc = port, proc
                return port
            proc.kill()
            proc.wait()
        raise RuntimeError(f"the review server did not start on any free port of {PORTS.start}-{PORTS.stop - 1} (log: {self.log})")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def run_test(node, name, env, cwd):
    """Runs one test file, echoes its output and counts the "OK" and "FAIL" lines."""
    proc = subprocess.Popen([node, str(HERE / name)], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    timer = threading.Timer(TEST_TIMEOUT, proc.kill)
    timer.start()
    passed = failed = 0
    try:
        for line in proc.stdout:
            print("    " + line, end="", flush=True)
            passed += line.startswith("OK    ")
            failed += line.startswith("FAIL  ")
        code = proc.wait()
    finally:
        timer.cancel()
    return passed, failed, code


def tail(path, n=25):
    try:
        return "".join(path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-n:])
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser(description="Browser tests of the review app against synthetic test data")
    ap.add_argument("--fixture", help="existing test data folder (made with make_fixture.py) instead of a new one")
    ap.add_argument("--only", action="append", default=[], help="run only this test file (repeatable)")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    ap.add_argument("--shots", help="folder for screenshots (per language)")
    a = ap.parse_args()

    node = os.environ.get("NODE") or shutil.which("node")
    chrome = os.environ.get("CHROME") or "/usr/bin/google-chrome"
    missing = [x for x, ok in (("node", node), (f"Chrome ({chrome})", os.path.exists(chrome)),
                               ("ffmpeg", a.fixture or shutil.which("ffmpeg"))) if not ok]
    if missing:
        sys.exit(f"Missing: {', '.join(missing)}")
    env = dict(os.environ, USER_NAME=LOGIN, PYTHONDONTWRITEBYTECODE="1")
    if (HERE / "node_modules").is_dir():
        env["NODE_PATH"] = os.pathsep.join(x for x in (str(HERE / "node_modules"), env.get("NODE_PATH")) if x)
    if subprocess.run([node, "-e", "require.resolve('playwright-core')"], cwd=HERE, env=env, capture_output=True).returncode:
        sys.exit("playwright-core not found: run \"npm ci\" in tests/browser or set NODE_PATH to a node_modules folder with it")
    unknown = sorted(set(a.only) - {r[0] for r in RUNS})
    if unknown:
        sys.exit(f"Unknown test file(s): {', '.join(unknown)}; known: {', '.join(sorted({r[0] for r in RUNS}))}")
    runs = [r for r in RUNS if not a.only or r[0] in a.only]

    if a.fixture and not all((pathlib.Path(a.fixture) / x).is_file() for x in ("data/project.json", "htpasswd", "password.txt")):
        sys.exit(f"{a.fixture} is not a folder made by make_fixture.py (data/project.json, htpasswd, password.txt)")
    work = pathlib.Path(tempfile.mkdtemp(prefix="cutter_browser_")).resolve()
    fixture = pathlib.Path(a.fixture).resolve() if a.fixture else work / "fixture"
    server = Server(fixture / "data", fixture / "htpasswd", work / "server.log")
    results, started = [], time.time()
    try:
        if not a.fixture:
            print(f"Building the test data in {fixture} …", flush=True)
            r = subprocess.run([sys.executable, str(HERE / "make_fixture.py"), str(fixture), "--login", LOGIN], env=env)
            if r.returncode:
                sys.exit("make_fixture.py failed")
        (work / "tmp").mkdir()
        env.update(PWFILE=str(fixture / "password.txt"), TMPDIR=str(work / "tmp"))   # browser profiles go to the temp folder
        for name, lang in runs:
            port = server.start(lang)
            print(f"\n== {name} ({lang}) on http://{HOST}:{port}/", flush=True)
            test_env = dict(env, BASE=f"http://{HOST}:{port}/")
            if a.shots:
                shots = pathlib.Path(a.shots).resolve() / lang
                shots.mkdir(parents=True, exist_ok=True)
                test_env["SHOTS"] = str(shots)
            try:
                passed, failed, code = run_test(node, name, test_env, work / "tmp")
            finally:
                server.stop()
            results.append((name, lang, passed, failed, code))
            if code:
                print(f"    -- server log (last lines):\n{tail(work / 'server.log')}", flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        results.append(("(interrupted)", "", 0, 0, 130))
    except RuntimeError as e:                                       # the server did not start
        print(f"\n{e}\n{tail(work / 'server.log')}")
        results.append(("(server)", "", 0, 0, 1))
    finally:
        server.stop()
        if a.keep:
            print(f"\nKept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    print(f"\nSummary ({time.time() - started:.0f} s)")
    total = sum(p + f for _, _, p, f, _ in results)
    for name, lang, passed, failed, code in results:
        state = "ok" if code == 0 else ("aborted" if code == 2 else f"FAILED (exit {code})")
        print(f"  {name:<20} {lang:<3} {passed:>3}/{passed + failed:<3} checks passed  {state}")
    bad = [r for r in results if r[4]]
    print(f"{'FAILED' if bad else 'OK'}: {total - sum(f for _, _, _, f, _ in results)}/{total} checks passed in {len(results)} runs"
          + (f", {len(bad)} run(s) failed" if bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
