#!/usr/bin/env python3
"""OID Browser — SNMP MIB Explorer. Entry point."""

import tkinter as tk
from gui import OIDBrowserApp


def main():
    root = tk.Tk()
    OIDBrowserApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
