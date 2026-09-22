# Third-Party Notices

This file records the licenses, copyright attribution, and redistribution
conditions for the components used by BimmerStein ECU Tool 0.1.0b17. Runtime
and license names are retained here to identify the corresponding components
and their terms. The release owner must confirm the applicable redistribution
conditions before publication.

Exact license texts collected for the frozen runtime are shipped in
`THIRD_PARTY_LICENSES/`. Each file is copied from the stated installed runtime
or wheel unless its provenance note says otherwise. The public beta selects the
free GPLv3 PyQt5 license path.

## Frozen runtime

### CPython 3.14.6 and native runtime libraries

- Project: Python Software Foundation CPython
- Use: embedded Python runtime and standard-library extension modules
- Frozen files include: `python314.dll`, `python3.dll`, `libcrypto-3.dll`,
  `libssl-3.dll`, `libffi-8.dll`, `_zstd.pyd`, and other standard-library
  modules collected from the installed Python runtime
- License material:
  `THIRD_PARTY_LICENSES/Python-3.14.6-LICENSE.txt` and
  `THIRD_PARTY_LICENSES/Python-3.14.6-INCORPORATED-SOFTWARE-NOTICES.rst.txt`

The first tracked file is a byte-identical copy of the installed CPython 3.14.6
Windows `LICENSE.txt`. The second is a byte-identical copy of CPython's installed
license-documentation source; it provides the explicit incorporated-software
headings and notices for OpenSSL, libffi, Zstandard, and pyzstd that the compact
Windows file does not label individually. The inspected build runtime reports
OpenSSL 3.5.7 and Zstandard 1.5.7. The exact upstream libffi version is not
exposed by the local runtime; `libffi-8.dll` identifies its ABI only.

### PyQt5 5.15.11

- Project: Riverbank Computing PyQt5
- Installed package metadata: GPL version 3 or a commercial Riverbank license
- Use: desktop user interface; bundled into the compiled Windows packages

The public beta uses the free GPLv3 edition of PyQt5 and the application is
distributed under GNU GPL version 3 (`GPL-3.0-only`). The complete application
license is shipped as `LICENSE.txt`; the Qt wheel's tracked license material also
contains the GPLv3 text. A future proprietary application build would require an
appropriate commercial PyQt license and a separate dependency-license review.

### Qt 5.15.2 from the PyQt5-Qt5 wheel

- Project: Qt runtime distributed with the installed PyQt5 wheel
- Installed package metadata: LGPL version 3
- Use: Qt libraries and plugins bundled into the Windows application
- License material: `THIRD_PARTY_LICENSES/Qt-5.15.2-LICENSE.txt`

The tracked wheel license contains the GPLv3 and LGPLv3 texts supplied by the
installed PyQt5-Qt5 5.15.2 distribution. The wheel does not provide a separate
tree of Qt component-attribution files. Confirm the selected Qt/PyQt
distribution path and any additional component notices or corresponding-source
obligations before publication.

### PyQt5-sip 12.18.0

- Project: PyQt5 SIP runtime module
- Installed package metadata: BSD-2-Clause
- Use: Python/Qt binding support bundled into the Windows application
- License material:
  `THIRD_PARTY_LICENSES/PyQt5-sip-12.18.0-BSD-2-Clause.txt`

### pyserial 3.5

- Project: pySerial
- Installed source declaration: BSD-3-Clause, copyright 2001-2020 Chris Liechti
- Use: normal serial fallback paths
- License material:
  `THIRD_PARTY_LICENSES/pyserial-3.5-BSD-3-Clause.txt`

The installed pyserial 3.5 wheel does not carry a standalone license file. The
tracked BSD-3-Clause text uses the copyright and SPDX license identity declared
by the installed `serial/__init__.py`; this provenance limitation is recorded
instead of presenting the text as a byte-identical wheel artifact.

### PyUSB 1.3.1

- Project: PyUSB
- Installed package metadata: BSD-3-Clause
- Use: cross-platform USB API for the native CH341A backend
- License material:
  `THIRD_PARTY_LICENSES/PyUSB-1.3.1-BSD-3-Clause.txt`

The tracked license is a byte-identical copy from the PyUSB 1.3.1 wheel.

### libusb1 3.4.0 and libusb 1.0.29

- Project: python-libusb1 and its bundled libusb shared library
- Installed package metadata: LGPL-2.1-or-later
- Use: supplies the replaceable `usb1/libusb-1.0.dll` used by PyUSB in the
  Windows source and frozen applications
- License material:
  `THIRD_PARTY_LICENSES/libusb1-3.4.0-COPYING.txt` and
  `THIRD_PARTY_LICENSES/libusb1-3.4.0-COPYING.LESSER.txt`

The tracked texts are byte-identical copies from the libusb1 3.4.0 Windows x64 and x86
wheel. The package does not install or replace a device driver. A non-HID USB
device still needs a libusb-compatible Windows binding such as WinUSB or
libusbK before PyUSB can open it.

### Nuitka 4.1.3 runtime

- Project: Nuitka by Kay Hayen
- Installed compiler metadata: GNU Affero General Public License version 3
- Use: build-time compiler and runtime code linked into the Nuitka executable
- License material:
  `THIRD_PARTY_LICENSES/Nuitka-4.1.3-LICENSE-RUNTIME.txt`

The Nuitka compiler itself is not bundled. Nuitka's Runtime Library Exception
is an additional permission under AGPLv3 for target code produced by its
compilation process. The tracked text is a byte-identical copy from the
installed Nuitka 4.1.3 distribution. The application remains distributed under
`GPL-3.0-only`; the exception does not change the project's license.

### Microsoft Visual C++ runtime

- Frozen files: `VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`, `MSVCP140.dll`, and
  `MSVCP140_1.dll` in the application-local runtime directory.
  `VCRUNTIME140_1.dll` is required only for x64.
- Origin: copied unchanged by the compiler from
  the installed CPython 3.14.6 runtime and matching MSVC redistributables
- Deployment: application-local in the Windows x64 and x86 portable package; users do
  not need to install a separate Visual C++ runtime

The tracked CPython license includes its "Additional Conditions for this Windows
binary build" section. It identifies the Microsoft Distributable Code, permits
redistribution of the Windows Python build subject to its listed conditions,
and is shipped unchanged in
`THIRD_PARTY_LICENSES/Python-3.14.6-LICENSE.txt`. The release is Windows-only,
does not alter Microsoft notices, does not claim Microsoft endorsement, and
records the exact SHA-256 of every application-local VC++ runtime file in
`RELEASE-METADATA.json`. The package verifier rejects any runtime file that is
missing or differs from the corresponding CPython or PyQt5-Qt5 build dependency.

## Installer notices

The compiled installer retains the notices embedded by its unmodified compiler.
The compiler itself is not included as a standalone application.

## Drivers and external software

### FTDI D2XX

BimmerStein ECU Tool can call the system-installed FTDI D2XX driver directly.
The project does not need a Python D2XX package and does not intentionally
bundle `ftd2xx.dll`. If the DLL is redistributed in the future, review FTDI's
current redistribution terms first.

## Definition data

The release bundles `BimmerStein MS41 Patch Definitions.xml` and
`BimmerStein MS41 Logger Definitions.xml`, generated from the project's patch
descriptors and verified logger mappings for compatible calibration and logging
software.
The ROM Analyzer can also import user-selected MS41 XML definition
files and stores a private registered copy under the user's local
application-data folder. The application's GPLv3 license does not cover
separately imported data.

## Trademarks and independence

BMW, FTDI, libusb, Microsoft, Nuitka, Python, PyQt, PyUSB, Qt,
pySerial, and OpenSSL names belong to their
respective owners. BimmerStein ECU Tool is independent software and is not
affiliated with or endorsed by those projects or companies.
