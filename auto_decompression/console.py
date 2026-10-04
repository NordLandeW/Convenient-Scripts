"""Shared terminal output for extraction, housekeeping and the command-line entry."""

from rich.console import Console

console = Console()


def print_info(message):
    console.out(message, style="blue")


def print_error(message):
    console.out(message, style="bold red")


def print_success(message):
    console.out(message, style="green")


def print_warning(message):
    console.out(message, style="bold yellow underline")
