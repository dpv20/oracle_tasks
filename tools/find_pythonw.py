"""Print the absolute path to pythonw.exe (or python.exe if pythonw isn't
available) for the current interpreter. Used by install.bat to build the
desktop shortcut target.
"""
import os
import sys


def main() -> int:
    pythonw = os.path.join(sys.prefix, "pythonw.exe")
    if os.path.isfile(pythonw):
        print(pythonw)
        return 0
    python = os.path.join(sys.prefix, "python.exe")
    if os.path.isfile(python):
        print(python)
        return 0
    print("", end="")
    return 1


if __name__ == "__main__":
    sys.exit(main())
