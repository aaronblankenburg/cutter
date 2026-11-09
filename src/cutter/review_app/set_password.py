#!/usr/bin/env python3
"""
Set a password for the cutter review app (run it in a terminal of your own).

Asks for the password twice and stores only a scrypt hash as a line "name:scrypt$..." in the hash file (default:
htpasswd next to the app/ folder, mode 600). The password itself is stored nowhere. The running app re-reads the file
when it changes; no restart is needed.

  python3 app/set_password.py <name>
  python3 app/set_password.py --file /path/to/htpasswd <name>
  python3 app/set_password.py --delete <name>
"""
import argparse
import base64
import getpass
import hashlib
import os
import re
import sys

N, R, P = 2 ** 15, 8, 1


def new_hash(pw):
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=N, r=R, p=P, maxmem=256 * 1024 * 1024, dklen=32)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def main():
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "htpasswd")
    ap = argparse.ArgumentParser(description="Set a password for the cutter review app")
    ap.add_argument("name", help="user name for the login (letters, digits, . _ -)")
    ap.add_argument("--file", dest="file", default=os.path.abspath(default))
    ap.add_argument("--delete", dest="delete", action="store_true", help="remove the user")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", a.name):
        sys.exit("Please use only letters, digits, dot, underscore and hyphen in the name.")
    lines = []
    if os.path.exists(a.file):
        with open(a.file, encoding="utf-8") as f:
            lines = [z.rstrip("\n") for z in f if z.strip() and not z.startswith(f"{a.name}:")]
    if a.delete:
        new = lines
        msg = f"{a.name} removed."
    else:
        pw = getpass.getpass(f"New password for {a.name}: ")
        if len(pw) < 10:
            sys.exit("Please use at least 10 characters.")
        if pw != getpass.getpass("Once more: "):
            sys.exit("The two entries differ, nothing changed.")
        new = lines + [f"{a.name}:{new_hash(pw)}"]
        msg = f"Password for {a.name} set (only the hash is stored in {a.file})."
    os.makedirs(os.path.dirname(os.path.abspath(a.file)), exist_ok=True)
    tmp = a.file + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(z + "\n" for z in new))
    os.chmod(tmp, 0o600)
    os.replace(tmp, a.file)
    print(msg)


if __name__ == "__main__":
    main()
