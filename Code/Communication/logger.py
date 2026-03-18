#!/usr/bin/env python3
"""
ftm_logger.py – Serial → CSV logger for the FTM Initiator firmware.

Usage:
    python ftm_logger.py                          # auto-detect port
    python ftm_logger.py --port COM3              # Windows
    python ftm_logger.py --port /dev/ttyACM0      # Linux
    python ftm_logger.py --port /dev/cu.usbmodem* # macOS
    python ftm_logger.py --port /dev/ttyACM0 --out my_run.csv

Parses lines that start with "FTM," and writes them to a timestamped CSV.
All other serial output (ESP-IDF log lines) is echoed to the terminal only.

Dependencies:
    pip install pyserial
"""

import argparse
import csv
import glob
import os
import sys
import time
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("pyserial not found – run:  pip install pyserial")

# ── Constants ────────────────────────────────────────────────────────────────
BAUD_RATE    = 115200
FTM_SENTINEL = "FTM,"
CSV_FIELDS   = ["seq", "timestamp_ms", "rtt_raw_ns", "rtt_est_ns", "dist_cm", "status"]
WALL_FIELD   = "wall_time"          # extra column added by the logger


# ── Helper: auto-detect a likely ESP32 port ──────────────────────────────────
def auto_detect_port() -> str:
    candidates = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        if any(kw in desc or kw in hwid for kw in
               ("cp210", "ch340", "ch9102", "ftdi", "usb serial", "xiao", "esp")):
            candidates.append(p.device)

    if not candidates:
        # fall back to any available port
        candidates = [p.device for p in serial.tools.list_ports.comports()]

    if not candidates:
        sys.exit("No serial ports found. Connect your XIAO ESP32-S3 and retry, "
                 "or pass --port manually.")

    if len(candidates) > 1:
        print("Multiple ports found:")
        for i, c in enumerate(candidates):
            print(f"  [{i}] {c}")
        choice = input("Select port number [0]: ").strip() or "0"
        return candidates[int(choice)]

    return candidates[0]


# ── Helper: default output filename ─────────────────────────────────────────
def default_csv_path() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"ftm_data_{ts}.csv"


# ── Main logger loop ─────────────────────────────────────────────────────────
def run(port: str, baud: int, out_path: str, verbose: bool) -> None:
    print(f"Opening {port} @ {baud} baud")
    print(f"Writing CSV to: {os.path.abspath(out_path)}")
    print("Press Ctrl+C to stop.\n")

    row_count = 0

    with serial.Serial(port, baud, timeout=2) as ser, \
         open(out_path, "w", newline="", encoding="utf-8") as csvfile:

        writer = csv.DictWriter(csvfile, fieldnames=[WALL_FIELD] + CSV_FIELDS)
        writer.writeheader()
        csvfile.flush()

        try:
            while True:
                raw = ser.readline()
                if not raw:
                    continue

                # Decode – ignore malformed bytes
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    continue

                # Always echo to terminal
                print(line)

                # Only parse FTM data lines
                if not line.startswith(FTM_SENTINEL):
                    continue

                parts = line.split(",")
                # Expected: FTM, seq, timestamp_ms, rtt_raw_ns, rtt_est_ns, dist_cm, status
                if len(parts) != len(CSV_FIELDS) + 1:
                    if verbose:
                        print(f"  [WARN] Unexpected field count: {parts}")
                    continue

                _, *values = parts          # drop the "FTM" sentinel

                row = {WALL_FIELD: datetime.now().isoformat(timespec="milliseconds")}
                for field, value in zip(CSV_FIELDS, values):
                    row[field] = value.strip()

                writer.writerow(row)
                csvfile.flush()
                row_count += 1

                if verbose:
                    print(f"  → saved row {row_count}: {row}")

        except KeyboardInterrupt:
            print(f"\nStopped. {row_count} FTM measurements saved to {out_path}")


# ── CLI ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log FTM RTT data from ESP32-S3 to CSV over serial."
    )
    parser.add_argument(
        "--port", "-p",
        default=None,
        help="Serial port (e.g. COM3, /dev/ttyACM0). Auto-detected if omitted."
    )
    parser.add_argument(
        "--baud", "-b",
        type=int,
        default=BAUD_RATE,
        help=f"Baud rate (default: {BAUD_RATE})"
    )
    parser.add_argument(
        "--out", "-o",
        default=None,
        help="Output CSV file path (default: ftm_data_<timestamp>.csv)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print each saved row to the terminal"
    )
    args = parser.parse_args()

    port     = args.port or auto_detect_port()
    out_path = args.out  or default_csv_path()

    run(port, args.baud, out_path, args.verbose)


if __name__ == "__main__":
    main()