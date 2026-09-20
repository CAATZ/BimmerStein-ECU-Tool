# Third-Party Notices: Windows 7 x64

BimmerStein ECU Tool 0.1.0b16 uses the GPLv3 edition of PyQt5 and is
distributed under GPL-3.0-only; see LICENSE.txt.

This Nuitka build contains CPython 3.8.10, PyQt5 5.15.11, Qt 5.15.2,
PyQt5-sip 12.15.0, pyserial 3.5, PyUSB 1.2.1, and libusb1 3.4.0 (libusb 1.0.29).
Nuitka 4.1.3 provides the compiled runtime under its Runtime Library Exception.
Qt uses LGPLv3; SIP and pyserial/PyUSB use BSD licenses; libusb uses LGPL 2.1+.

CPython's unmodified license includes incorporated OpenSSL and libffi notices
and the Additional Conditions for this Windows binary build.
This product includes software developed by the OpenSSL Project for use in
the OpenSSL Toolkit (http://www.openssl.org/).

VCRUNTIME140 and VCRUNTIME140_1 are unchanged copies from CPython 3.8.10.
MSVCP140, MSVCP140_1, MSVCP140_2, and CONCRT140 are unchanged copies from the
Qt 5.15.2 wheel. The Universal CRT and its API forwarding DLLs are copied
unchanged from the Windows SDK redistributable version 10.0.10240.16384.
They are deployed beside the executable; no Windows system files are replaced.
The compiled application targets the Windows 7 API using MinGW's MSVCRT toolchain.

License texts:
- `THIRD_PARTY_LICENSES/Python-3.8.10-LICENSE.txt`
- `THIRD_PARTY_LICENSES/PyQt5-sip-12.15.0-BSD-2-Clause.txt`
- `THIRD_PARTY_LICENSES/PyUSB-1.2.1-BSD-3-Clause.txt`
- `THIRD_PARTY_LICENSES/Nuitka-4.1.3-LICENSE-RUNTIME.txt`
- `THIRD_PARTY_LICENSES/Qt-5.15.2-LICENSE.txt`
- `THIRD_PARTY_LICENSES/pyserial-3.5-BSD-3-Clause.txt`
- `THIRD_PARTY_LICENSES/libusb1-3.4.0-COPYING.txt`
- `THIRD_PARTY_LICENSES/libusb1-3.4.0-COPYING.LESSER.txt`

The pyserial license is the existing project text based on its source license
declaration; the other texts are copied unchanged from their corresponding
distributions. Device drivers are not bundled or installed by this package.

## Compiler runtime

This Windows 7 build uses MinGW-w64 13.0.0 and GCC 15.2.0, including the GCC Runtime Library Exception.
- `THIRD_PARTY_LICENSES/GCC-15.2.0-GPL-3.txt`
- `THIRD_PARTY_LICENSES/GCC-15.2.0-RUNTIME-EXCEPTION.txt`
- `THIRD_PARTY_LICENSES/MinGW-w64-13.0.0-COPYING.txt`
