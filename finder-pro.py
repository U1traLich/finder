#!/usr/bin/env python3
"""GigaFinder — поиск стендов Giga Cowork и управление пространствами."""

from giga_finder.cli import _handle_keyboard_interrupt, main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _handle_keyboard_interrupt()
