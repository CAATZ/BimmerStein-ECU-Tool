# Windows 7 x64 build

This corresponding-source archive builds BimmerStein ECU Tool 0.1.0b16 for
Windows 7 SP1 x64. Use the separate modern Windows source archive for the
PyInstaller and MSVC editions. GPL-3.0-only; OFF-ROAD USE ONLY.

Use CPython 3.8.10 x64 with the pinned requirements-build.txt packages.
The build script expects that Python distribution at .venv/python.exe; a directory
junction to an existing Python 3.8.10 installation is sufficient. The Python
executable and DLLs must be the original distribution, not a newer runtime.

Run build_windows_nuitka.ps1 -Version 0.1.0b16. Nuitka 4.1.3 selects the pinned
MinGW GCC 15.2.0 / MinGW-w64 13.0.0 MSVCRT toolchain. Compilation disables LTO,
uses 12 jobs, and targets _WIN32_WINNT=0x0601. Do not substitute the UCRT edition
of MinGW. The script deploys the original CPython and Qt runtime DLLs and
Windows SDK UCRT 10.0.10240.16384 redistributables beside the executable.
The SDK redistributables must be installed under the standard Windows Kits/10
location. No Windows system DLL is replaced. Media-service plugins requiring
newer Windows are omitted; they are not used by this application.

The canonical manual and screenshots are supplied with this archive. They are
built and reviewed using the modern release documentation toolchain and are not
regenerated with the older ReportLab version during this build.

After the mandatory release admission and test gates pass, stage the complete
Nuitka application directory beneath release/ and run packaging/build_installer.ps1
-Version 0.1.0b16 -Backend nuitka -SourceDir <staged-folder> with Inno Setup 6.
The installer targets Windows 7 SP1 and uses the BimmerStein ECU Tool name.
The intended release tag is v0.1.0b16; these commands do not publish it.

Verify the package with packaging/verify_dist.py --backend nuitka --expected-version
0.1.0b16 <staged-folder>. The Windows 7 package has been tested and is working. Windows 7 requires KB2533623
or a superseding update. Device drivers are installed separately.
