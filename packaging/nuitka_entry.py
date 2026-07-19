"""Frozen entry point shared by the experimental Nuitka package."""

from __future__ import annotations

import sys


# The application deliberately stores portable user data beside the executable.
# Match PyInstaller's frozen contract before importing modules that resolve paths.
sys.frozen = True

from PyQt5.QtWidgets import QApplication  # noqa: E402

from gui import MS41FlashGUI, configure_application, install_exception_handler  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    configure_application(app)
    install_exception_handler()
    window = MS41FlashGUI()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
