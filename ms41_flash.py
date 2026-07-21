#!/usr/bin/env python3
"""
BimmerStein ECU Tool — entry point.

Most live ECU operations (connect / write / config / live data) use the DS2
protocol and live in the Qt GUI (gui.py).  This CLI does the OFFLINE file
operations (checksum verify / correct) plus a headless live --dump (read the
ECU to a .bin, K-Line or --no-echo direct tap); run with --gui (or no arguments)
for everything else.
"""

import sys
import argparse


def main():
    parser = argparse.ArgumentParser(
        description="BimmerStein ECU Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Launch the Qt GUI (all live ECU operations are here, over DS2)
  python ms41_flash.py --gui

  # Dump the ECU to a .bin over a direct ASC0 tap (full-duplex, no K-Line echo)
  python ms41_flash.py --dump backup.bin --port COM3 --no-echo
  # ...or the 24 KB tune only, over the normal K-Line:
  python ms41_flash.py --dump tune.bin --port COM3 --tune

  # Verify all checksums of a .bin file (offline)
  python ms41_flash.py --check-file myrom.bin

  # Correct all checksums of a .bin file (offline)
  python ms41_flash.py --fix-file myrom.bin --output fixed.bin
        """
    )

    parser.add_argument("--gui", action="store_true", help="Launch the Qt GUI")
    parser.add_argument("--check-file", type=str, metavar="FILE",
                        help="Verify all checksums of a local .bin file (offline)")
    parser.add_argument("--fix-file", type=str, metavar="FILE",
                        help="Correct all checksums of a local .bin file (offline)")
    parser.add_argument("--output", type=str, help="Output .bin path for --fix-file")
    parser.add_argument("--dump", type=str, metavar="FILE",
                        help="LIVE: read the ECU over DS2 and save to FILE (needs --port)")
    parser.add_argument("--port", type=str, help="Serial port for --dump (e.g. COM3)")
    parser.add_argument("--no-echo", action="store_true",
                        help="--dump: full-duplex DIRECT TAP on ASC0 (no K-Line echo) instead of "
                             "the K-Line. Use when wired straight onto TxD0/RxD0 (same tap as BSL).")
    parser.add_argument("--tune", action="store_true",
                        help="--dump: read only the 24 KB calibration/tune partial (default: full 256 KB)")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Detailed diagnostic logging")

    args = parser.parse_args()

    if args.dump:
        run_dump(args)
    elif args.check_file or args.fix_file:
        run_offline(args)
    else:
        launch_gui()


def launch_gui():
    try:
        from gui import run_gui
        sys.exit(run_gui())
    except ImportError:
        print("PyQt5 not installed. Install with: pip install PyQt5")
        sys.exit(1)


def run_offline(args):
    """Offline checksum verify/correct on a .bin file (no ECU connection)."""
    from ms41 import MS41ECU
    from checksum import verify_checksum, correct_checksums, checksum_state
    import logging

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="[%(levelname)s] %(message)s")

    if args.check_file:
        with open(args.check_file, "rb") as f:
            data = bytearray(f.read())
        ok, details = verify_checksum(data)
        print(f"Checksum verification state: {checksum_state(data).upper()}  ({args.check_file})")
        for d in details:
            print(f"  {d}")
        sys.exit(0 if ok else 1)

    if args.fix_file:
        with open(args.fix_file, "rb") as f:
            data = bytearray(f.read())
        if len(data) != MS41ECU.FULL_ROM_SIZE:
            print("Checksum correction needs a full 256 KB ROM image."); sys.exit(1)
        is_ms413 = MS41ECU.detect_variant(data) == "MS41.3"
        patched, details = correct_checksums(data, correct_program=not is_ms413)
        if is_ms413:
            if patched[0x605C] == 0xFF:
                details.append(
                    "MS41.3: boot and calibration checksums corrected; program checksum "
                    "left unchanged because stock program verification is disabled."
                )
            else:
                details.append(
                    "MS41.3: boot and calibration checksums corrected; program checksum "
                    "left unchanged, but program verification is enabled in this image."
                )
        out = args.output if args.output else args.fix_file.replace(".bin", "_cksum.bin")
        with open(out, "wb") as f:
            f.write(patched)
        print(f"✓ Checksums corrected → {out}")
        for d in details:
            print(f"  {d}")
        sys.exit(0)


def run_dump(args):
    """LIVE: dump once at native-fast DS2, with a confirmed-9600 fallback."""
    import logging
    import ds2_fast_read
    from ds2 import DS2Interface
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="[%(levelname)s] %(message)s")
    if not args.port:
        print("--dump needs --port (e.g. --port COM3)"); sys.exit(2)

    def progress(done, total, label):
        print(f"\r  {label}: {done}/{total} B ({100 * done // total}%)", end="", flush=True)

    def logf(msg, level="info"):
        print(f"\n  {msg}")

    ds2 = None
    try:
        print("Trying stock native DS2 at the exact 187,500 baud rate…")
        try:
            if args.tune:
                result = ds2_fast_read.read_partial_d2xx(
                    args.port, progress_cb=progress, echo=not args.no_echo)
                data = result.data
            else:
                result = ds2_fast_read.read_full_d2xx(
                    args.port, progress_cb=progress, echo=not args.no_echo)
                data = result.file_image
            ident = result.identity
            print(f"\nECU [{'direct tap / no echo' if args.no_echo else 'K-Line'}]: "
                  f"{ident.hex(' ').upper()}")
        except Exception as fast_error:
            print(f"\nNative-fast dump did not complete ({fast_error}).")
            print("Confirming normal DS2 at 9,600 before restarting the whole dump…")
            ds2 = DS2Interface(
                port=args.port, baud=9600, verbose=args.verbose,
                echo=not args.no_echo)
            ds2.open()
            ident = ds2.identify()  # successful response is the fallback authority
            print(f"ECU [{'direct tap / no echo' if args.no_echo else 'K-Line'}]: "
                  f"{ident.hex(' ').upper()}")
            if args.tune:
                print("Reading 24 KB calibration/tune partial at 9,600…")
                data = ds2.read_partial(progress_cb=progress, log_fn=logf)
            else:
                print("Reading full 256 KB ROM at 9,600…")
                data = ds2.read_full(progress_cb=progress, log_fn=logf)
        with open(args.dump, "wb") as f:
            f.write(data)
        print(f"\nSaved {len(data)} bytes -> {args.dump}")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}"); sys.exit(1)
    finally:
        if ds2 is not None:
            try:
                ds2.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
