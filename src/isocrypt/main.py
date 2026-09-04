#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shlex
import subprocess
import typing
from pathlib import Path
from random import choice
from shutil import which
from string import ascii_letters

import typer  # https://typer.tiangolo.com/
from humanfriendly import format_size, parse_size  # https://humanfriendly.readthedocs.io/en/latest/api.html

from isocrypt.libcryptsetup import Cryptsetup

app = typer.Typer(help="ISO Crypt - create and mount encrypted disk images.", no_args_is_help=True)
state = {"verbose": False}
required_binary_list = [
    "cryptsetup",
    "losetup",
    "mkdir",
    "mkudffs",
    "mount",
    "rm",
    "truncate",
    "umount",
]


def check_command(command: "subprocess.CompletedProcess[bytes]") -> bool:
    ret = True

    if not command.returncode == 0:
        typer.echo(
            f"Command failed:\n{' '.join(command.args)}\n\nError:\n"
            + typer.style(f"{command.stderr.decode()}", fg=typer.colors.WHITE, bg=typer.colors.RED)
        )

        ret = False

    return ret


def save_config(tmpdir: Path, ldevice: str, iso: Path, num_kilobytes: int) -> None:
    with open(f"{tmpdir}/.config", "w") as fh:
        fh.write(f"{ldevice}\n")
        fh.write(f"{str(iso)}\n")
        fh.write(f"{str(num_kilobytes)}\n")


def read_config(tmpdir: Path) -> typing.Tuple[str, str, int]:
    ldevice = ""
    iso = ""
    num_kilobytes = 0

    with open(f"{tmpdir}/.config") as fh:
        ldevice = fh.readline()
        iso = fh.readline()
        num_kilobytes = int(fh.readline())

    return (ldevice, iso, num_kilobytes)


def execute_command(
    args: str, exit_code: int, method=subprocess.run, com_args: list = []
) -> typing.Tuple[int, "subprocess.CompletedProcess[bytes]"]:  # type: ignore

    if state.get("verbose", False):
        print(f"Running command: {args}")

    if method == subprocess.run:
        command = method(args, capture_output=True, shell=True, stdin=subprocess.PIPE)

    if not check_command(command):
        return (exit_code, command)

    return (0, command)


@app.command("create")
def create(
    size: str,
    iso: Path = typer.Option(
        default="./isocrypt.iso",
        exists=False,
        file_okay=True,
        dir_okay=False,
        writable=True,
        readable=True,
        resolve_path=True,
    ),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """
    Create a new ISO file
    """

    if iso.exists() and not force:
        typer.echo(
            typer.style(
                f"{iso} exists and --force was not used - aborting ISO creation!", fg=typer.colors.WHITE, bg=typer.colors.RED
            )
        )
        raise typer.Exit(code=3)

    num_kilobytes = parse_size(size, binary=True) / 1024
    volname = "".join(choice(ascii_letters) for _ in range(16))
    tmpdir = Path(f"./{volname}")

    if num_kilobytes < 1024 * 20:
        typer.echo(typer.style("SIZE must be more than 20MB", fg=typer.colors.WHITE, bg=typer.colors.RED))
        raise typer.Exit(code=32)

    if tmpdir.exists():
        typer.echo(
            typer.style(
                f"{tmpdir} exists - you won the lottery - aborting ISO creation!", fg=typer.colors.WHITE, bg=typer.colors.RED
            )
        )
        raise typer.Exit(code=5)

    tmpdir.mkdir(parents=True, exist_ok=False)

    typer.echo(f"Creating {iso} with a size of: {format_size(num_kilobytes * 1024, keep_width=True)}")
    exit_code = 0
    ldevice = ""

    while True:
        with open(iso, "wb") as fp:
            fp.truncate(int(num_kilobytes * 1024))

        exit_code, command = execute_command(f"losetup --show -fP {iso}", 6)
        if exit_code != 0:
            break

        print(command)

        ldevice = command.stdout.decode("utf8", errors="replace").strip()

        password = typer.prompt(
            "Password",
            default=False,
            show_default=False,
            type=str,
            confirmation_prompt=True,
            hide_input=True,
        )

        cs = Cryptsetup(ldevice)

        if not cs.format(bytes(password, "utf8")):
            exit_code = -22
            break

        exit_code, _ = execute_command(f"echo -n '{password}' | cryptsetup --force-password -d - luksOpen {ldevice} {volname}", 8)
        if exit_code != 0:
            break

        exit_code, _ = execute_command(f"mkudffs --label='Luks ISO' --blocksize=512 /dev/mapper/{volname}", 9)
        if exit_code != 0:
            break

        exit_code, _ = execute_command(f"mount -t udf /dev/mapper/{volname} {tmpdir}", 10)
        if exit_code != 0:
            break

        break  # not a while loop - but a cheap goto xD

    if exit_code != 0:
        tmpdir.rmdir()

        if ldevice != "":
            command = subprocess.run(shlex.split(f"losetup -d {ldevice}"), capture_output=True)
            if not check_command(command):
                exit_code = 11
                typer.echo(
                    typer.style(
                        f"Unable to cleanup loop device {ldevice} - something went wrong...",
                        fg=typer.colors.WHITE,
                        bg=typer.colors.RED,
                    )
                )

        iso.unlink(missing_ok=True)

        typer.Exit(code=exit_code)
    else:
        save_config(tmpdir, ldevice, iso, num_kilobytes)
        typer.echo(f"ISO Created and populate with files under ./{tmpdir} - once done, issue isocrypt close")


def validate_iso_exists(iso: Path) -> bool:
    message = ""

    if iso is None:
        message = "No iso file"
    elif iso.is_dir():
        message = "ISO to mount should be a file not a directory..."
    elif not iso.exists():
        message = "The ISO doesn't exist..."
    elif not iso.is_file():
        message = "The ISO provided is not a file or is not accessible..."

    if message != "":
        typer.echo(typer.style(message, fg=typer.colors.WHITE, bg=typer.colors.RED))
        raise typer.Exit(code=2)

    return True


def validate_dir_exists(dir: Path) -> typing.Union[bool, str]:
    message = ""

    if dir is None:
        message = "No directory provided"
    elif not dir.is_dir():
        message = "The path provided is not a directory or is not accessible..."
    elif not dir.exists():
        message = "The directory doesn't exist..."
    elif dir.is_file():
        message = "The directory to finalize not be a file..."

    if message != "":
        typer.echo(typer.style(message, fg=typer.colors.WHITE, bg=typer.colors.RED))
        raise typer.Exit(code=2)

    return True


@app.command("mount")
def mount(
    iso: Path = typer.Option(
        default="./isocrypt.iso",
        exists=True,
        file_okay=True,
        dir_okay=False,
        writable=False,
        readable=True,
        resolve_path=True,
    ),
) -> None:
    """
    Mount an ISO file
    """

    validate_iso_exists(iso)

    typer.echo(f"Mount not implemented - just double click on the iso file in an explorer: {iso}")


@app.command("close")
def close(
    dir: Path = typer.Option(
        default="/tmp/does_not_exist",
        exists=False,
        file_okay=False,
        dir_okay=True,
        writable=True,
        readable=True,
        resolve_path=True,
    ),
) -> None:
    """
    Finalize an ISO file
    """

    validate_dir_exists(dir)

    ldevice, iso, num_kilobytes = read_config(dir)

    exit_code = 0

    while True:
        command = subprocess.run(shlex.split(f"umount /dev/mapper/{dir.name}"), capture_output=True)
        if not check_command(command):
            exit_code = 65
            break

        command = subprocess.run(shlex.split(f"cryptsetup luksClose {dir.name}"), capture_output=False)
        command = subprocess.run(shlex.split(f"losetup -d {ldevice}"), capture_output=True)
        if not check_command(command):
            exit_code = 67
            break

        # TODO: assumes we are in the same directory...
        command = subprocess.run(shlex.split(f"rm -rf ./{dir.name}"), capture_output=True)
        if not check_command(command):
            exit_code = 68
            break

        break  # not a while loop - but a cheap goto xD

    if exit_code != 0:
        typer.echo("Something went wrong during command execution...")
        typer.Exit(code=exit_code)

    typer.echo(f"{dir} removed, related {iso} finished!")


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose")) -> None:
    """
    ISO Crypt - create and mount encrypted disk images.

    Requires the following on PATH:
     - cryptsetup
     - losetup
     - mkdir
     - mkudffs
     - mount
     - rm
     - umount
    """

    if verbose:
        typer.echo("Will write verbose output")
        state["verbose"] = True

    if os.geteuid() != 0:
        typer.echo(typer.style("This script must be run with root like access...", fg=typer.colors.WHITE, bg=typer.colors.RED))
        raise typer.Exit(code=-1)

    missing_required_binary = []

    for required_binary in required_binary_list:
        if not which(required_binary):
            print("missing:", required_binary)
            missing_required_binary.append(required_binary)

    if missing_required_binary != []:
        typer.echo(
            "The following required binaries are missing from PATH: "
            + typer.style(f"{', '.join(missing_required_binary)}", fg=typer.colors.WHITE, bg=typer.colors.RED)
        )

        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
