#!/usr/bin/env python3
"""
Set a password for the cutter review app.

Stores only a scrypt hash as a line "name:scrypt$..." in the hash file (default: htpasswd next to the app/ folder,
mode 600). The password itself is stored nowhere. The running app re-reads the file when it changes; no restart is
needed.

  python3 app/set_password.py <name>              asks for the password twice (needs a terminal, or two lines on stdin)
  python3 app/set_password.py --generate <name>   makes a password and prints it once (works without a terminal)
  python3 app/set_password.py --delete <name>
  python3 app/set_password.py --file /path/to/htpasswd <name>
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import sys

N, R, P = 2 ** 15, 8, 1
MIN_LENGTH = 10
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"       # no 0/o, 1/l/i: the password is read aloud and typed on phones


class PasswordError(Exception):
    """Nothing was changed; the text says why."""


def new_hash(pw):
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=N, r=R, p=P, maxmem=256 * 1024 * 1024, dklen=32)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def generate():
    """A random password that is easy to pass on: four groups of four characters, about 79 bits."""
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(4))


def check_name(name):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", name):
        raise PasswordError("Please use only letters, digits, dot, underscore and hyphen in the name.")


def names(file):
    """The user names in the hash file."""
    if not os.path.exists(file):
        return []
    with open(file, encoding="utf-8") as f:
        return [z.split(":", 1)[0] for z in f if ":" in z and not z.startswith("#")]


def ask(name):
    """The password typed twice: hidden in a terminal, otherwise two lines from stdin."""
    if sys.stdin.isatty():
        try:
            pw, again = getpass.getpass(f"New password for {name}: "), None
            if len(pw) >= MIN_LENGTH:
                again = getpass.getpass("Once more: ")
        except EOFError:
            raise PasswordError("No password entered, nothing changed.") from None
    else:
        pw, again = sys.stdin.readline(), sys.stdin.readline()
        if not pw:
            raise PasswordError("There is no terminal to type the password in. Use --generate to make one, "
                                "or run this in a terminal of your own.")
        pw, again = pw.rstrip("\r\n"), again.rstrip("\r\n")
    if len(pw) < MIN_LENGTH:
        raise PasswordError(f"Please use at least {MIN_LENGTH} characters.")
    if pw != again:
        raise PasswordError("The two entries differ, nothing changed.")
    return pw


def store(file, name, pw=None):
    """Write the hash of pw for name into the file (pw None: remove the name). Returns whether the name was there."""
    check_name(name)
    lines = []
    if os.path.exists(file):
        with open(file, encoding="utf-8") as f:
            lines = [z.rstrip("\n") for z in f if z.strip()]
    kept = [z for z in lines if not z.startswith(f"{name}:")]
    if pw is not None:
        kept.append(f"{name}:{new_hash(pw)}")
    os.makedirs(os.path.dirname(os.path.abspath(file)), exist_ok=True)
    tmp = file + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(z + "\n" for z in kept))
    os.chmod(tmp, 0o600)
    os.replace(tmp, file)
    return len(kept) - (pw is not None) < len(lines)


def run(file, name, delete=False, make=False):
    """Set, generate or delete; returns (result, text for people). The password is in both only when it was generated."""
    check_name(name)
    file = str(file)
    if delete:
        existed = store(file, name)
        return ({"name": name, "file": file, "deleted": existed},
                f"{name} removed." if existed else f"{name} was not in {file}, nothing changed.")
    pw = generate() if make else ask(name)
    existed = store(file, name, pw)
    r = {"name": name, "file": file, "replaced": existed, "generated": make}
    text = f"Password for {name} {'changed' if existed else 'set'} (only the hash is stored in {file})."
    if make:
        r["password"] = pw
        text += f"\n  name:     {name}\n  password: {pw}\nIt is shown only this once: pass it on to the reviewer now."
    return r, text


def main():
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "htpasswd")
    ap = argparse.ArgumentParser(description="Set a password for the cutter review app")
    ap.add_argument("name", help="user name for the login (letters, digits, . _ -)")
    ap.add_argument("--file", dest="file", default=os.path.abspath(default))
    ap.add_argument("--generate", action="store_true", help="make a random password and print it once")
    ap.add_argument("--delete", dest="delete", action="store_true", help="remove the user")
    ap.add_argument("--json", action="store_true", help="print the result as one JSON object")
    a = ap.parse_args()
    try:
        r, text = run(a.file, a.name, delete=a.delete, make=a.generate)
    except PasswordError as ex:
        sys.exit(str(ex))
    print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else text)


if __name__ == "__main__":
    main()
