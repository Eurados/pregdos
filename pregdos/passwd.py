"""``pregdos-passwd`` -- manage the accounts for ``[auth] method = "file"``.

The supported way to edit the password file.  Hand-editing is possible (it is one
``username:hash`` per line) but there is nothing to hand-edit *into*: the file stores scrypt
hashes, and this is what produces them.

    pregdos-passwd add nbassler        # prompts twice, no echo
    pregdos-passwd list
    pregdos-passwd delete nbassler

Run it as the account the web service runs as -- ``sudo -u pregdos`` under the shipped
systemd unit -- so the file it creates is owned by the process that has to read it.  Where
that file lives comes from the same config the server reads, so ``--config`` and
``$PREGDOS_CONFIG`` work here exactly as they do for ``pregdos-web``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from . import auth, config


def _prompt_for_password(username: str) -> str:
    """Ask twice, never echo.  Returns the password, or exits with a message."""
    first = getpass.getpass(f"New PregDos password for {username}: ")
    if not first:
        raise SystemExit("pregdos-passwd: empty password refused")
    if first != getpass.getpass("Retype it: "):
        raise SystemExit("pregdos-passwd: the two entries did not match; nothing was changed")
    return first


def _load(path: Path) -> dict[str, str]:
    """The current accounts, treating a missing file as an empty set.

    `add` must work on a site that has never had one, which is every site the first time.  A
    file that exists but is too readable still raises -- that is a real problem to report,
    not an empty starting point.
    """
    if not path.exists():
        return {}
    return auth.read_password_file(path)


def _resolve_password_file(cfg: config.Config, parser: argparse.ArgumentParser) -> Path:
    """Where to write, or a startup error explaining why we will not guess.

    The trap this exists for, found the hard way on the first real deployment: the default
    location is ``$STATE_DIRECTORY/users``, and systemd sets ``$STATE_DIRECTORY`` for the
    *service* only.  Run from a plain shell -- which is the only way this command is ever run
    -- that variable is unset, so the path quietly fell back to ``$HOME/.local/state/...``.
    The account was created successfully, in a location the service does not read; the service
    then refused to start, several minutes and one restart later, with a message about a
    different path.

    A path the service will not read is never what the operator meant, so refuse rather than
    guess.  The message has to name the fix, because the person reading it is mid-deployment.
    """
    if cfg.auth.password_file or os.environ.get("STATE_DIRECTORY"):
        return auth.password_file_path(cfg)
    parser.error(
        f"cannot tell where the password file belongs, and will not guess.\n\n"
        f"  [auth] password_file is not set, and $STATE_DIRECTORY is unset -- which it is in\n"
        f"  any ordinary shell, because systemd exports it only to the service itself.\n"
        f"  Guessing would write to\n\n"
        f"      {auth.password_file_path(cfg)}\n\n"
        f"  which pregdos-web will not read, so the account would appear to be created and\n"
        f"  the service would then refuse to start.\n\n"
        f"  Set the path explicitly in the config file, then run this again:\n\n"
        f"      [auth]\n"
        f'      password_file = "/var/lib/pregdos/users"\n'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pregdos-passwd",
        description='Manage accounts for [auth] method = "file".',
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="TOML config file, to locate [auth] password_file. Replaces the /etc/pregdos and "
             f"~/.config/pregdos stack rather than merging onto it. See also ${config.CONFIG_ENV}.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("add", "Add an account, or change an existing one's password."),
        ("delete", "Remove an account."),
    ):
        one = sub.add_parser(name, help=help_text)
        one.add_argument("username")
    sub.add_parser("list", help="List account names. Never prints hashes.")

    args = parser.parse_args(argv)
    if args.config:
        config.set_config_path(args.config)

    try:
        cfg = config.load()
    except config.ConfigError as exc:
        parser.error(str(exc))

    path = _resolve_password_file(cfg, parser)

    try:
        users = _load(path)
    except auth.AuthError as exc:
        parser.error(str(exc))

    if args.command == "list":
        if not users:
            print(f"{path}: no accounts")
            return 0
        for username in sorted(users):
            print(username)
        return 0

    if args.command == "delete":
        if args.username not in users:
            parser.error(f"{args.username!r} is not in {path}")
        # Refuse to empty the file rather than silently locking everyone out of a running
        # server.  Removing the last account is a deliberate act: turn [auth] off instead.
        if len(users) == 1:
            parser.error(
                f"{args.username!r} is the only account in {path}. Removing it would leave "
                f'nobody able to sign in. Set [auth] method = "none" instead, or add another '
                f"account first."
            )
        del users[args.username]
        auth.write_password_file(path, users)
        print(f"Removed {args.username} from {path}")
        return 0

    # add, which doubles as "change the password of"
    existing = args.username in users
    users[args.username] = auth.hash_password(_prompt_for_password(args.username))
    auth.write_password_file(path, users)
    print(f"{'Updated' if existing else 'Added'} {args.username} in {path}")
    if not existing and len(users) == 1:
        print('Remember to set [auth] method = "file" in the config, and restart pregdos-web.')
    return 0


if __name__ == "__main__":
    sys.exit(main())
