#!/usr/bin/env python3
"""
ftm_logger_raw.py - Raw CSV logger for FTM session + per-frame data.

Writes two CSV files simultaneously:
  ftm_sessions_<timestamp>.csv  - one row per FTM session  (FTM_S lines)
  ftm_frames_<timestamp>.csv    - one row per frame         (FTM_F lines)

Usage:
    python ftm_logger_raw.py
    python ftm_logger_raw.py --port COM3
    python ftm_logger_raw.py --port /dev/ttyACM0

Dependencies:
    pip install pyserial
"""

import argparse
import csv
import os
import sys
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("pyserial not found - run:  pip install pyserial")

BAUD_RATE = 115200

SESSION_FIELDS = [
    "wall_time", "seq", "timestamp_ms",
    "rtt_raw_ns", "rtt_est_ns", "dist_cm", "n_frames", "status"
]

FRAME_FIELDS = [
    "wall_time", "seq", "frame_idx",
    "rtt_ps", "rtt_ns",
    "t1_ps", "t2_ps", "t3_ps", "t4_ps",
    "rssi"
]


def auto_detect_port() -> str:
    candidates = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        if any(kw in desc or kw in hwid for kw in
               ("cp210", "ch340", "ch9102", "ftdi", "usb serial", "xiao", "esp")):
            candidates.append(p.device)
    if not candidates:
        candidates = [p.device for p in serial.tools.list_ports.comports()]
    if not candidates:
        sys.exit("No serial ports found.")
    if len(candidates) > 1:
        print("Multiple ports found:")
        for i, c in enumerate(candidates):
            print(f"  [{i}] {c}")
        choice = input("Select port number [0]: ").strip() or "0"
        return candidates[int(choice)]
    return candidates[0]


def run(port: str, baud: int, session_path: str, frame_path: str) -> None:
    print(f"Port         : {port} @ {baud} baud")
    print(f"Sessions CSV : {os.path.abspath(session_path)}")
    print(f"Frames CSV   : {os.path.abspath(frame_path)}")
    print("Press Ctrl+C to stop.\n")

    session_count = 0
    frame_count   = 0

    with serial.Serial(port, baud, timeout=2) as ser, \
         open(session_path, "w", newline="", encoding="utf-8") as sf, \
         open(frame_path,   "w", newline="", encoding="utf-8") as ff:

        sw = csv.DictWriter(sf, fieldnames=SESSION_FIELDS)
        fw = csv.DictWriter(ff, fieldnames=FRAME_FIELDS)
        sw.writeheader(); sf.flush()
        fw.writeheader(); ff.flush()

        try:
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    continue

                print(line)
                now = datetime.now().isoformat(timespec="milliseconds")

                # FTM_S,seq,timestamp_ms,rtt_raw_ns,rtt_est_ns,dist_cm,n_frames,status
                if line.startswith("FTM_S,"):
                    parts = line.split(",")
                    if len(parts) != 8:
                        continue
                    _, seq, ts_ms, rtt_raw, rtt_est, dist, n_frames, status = parts
                    sw.writerow({
                        "wall_time":    now,
                        "seq":          seq,
                        "timestamp_ms": ts_ms,
                        "rtt_raw_ns":   rtt_raw,
                        "rtt_est_ns":   rtt_est,
                        "dist_cm":      dist,
                        "n_frames":     n_frames,
                        "status":       status.strip(),
                    })
                    sf.flush()
                    session_count += 1

                # FTM_F,seq,frame_idx,rtt_ps,t1_ps,t2_ps,t3_ps,t4_ps,rssi
                elif line.startswith("FTM_F,"):
                    parts = line.split(",")
                    if len(parts) != 9:
                        continue
                    _, seq, fidx, rtt_ps, t1, t2, t3, t4, rssi = parts
                    # convert rtt from ps to ns
                    try:
                        rtt_ns = float(rtt_ps) / 1000.0
                    except ValueError:
                        rtt_ns = ""
                    fw.writerow({
                        "wall_time":  now,
                        "seq":        seq,
                        "frame_idx":  fidx,
                        "rtt_ps":     rtt_ps,
                        "rtt_ns":     f"{rtt_ns:.3f}" if isinstance(rtt_ns, float) else "",
                        "t1_ps":      t1,
                        "t2_ps":      t2,
                        "t3_ps":      t3,
                        "t4_ps":      t4,
                        "rssi":       rssi.strip(),
                    })
                    ff.flush()
                    frame_count += 1

        except KeyboardInterrupt:
            print(f"\nStopped.")
            print(f"  Sessions : {session_count} rows -> {session_path}")
            print(f"  Frames   : {frame_count} rows -> {frame_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Raw FTM logger - writes session and per-frame CSVs."
    )
    parser.add_argument("--port", "-p", default=None)
    parser.add_argument("--baud", "-b", type=int, default=BAUD_RATE)
    parser.add_argument("--out-sessions", default=None)
    parser.add_argument("--out-frames",   default=None)
    args = parser.parse_args()

    ts           = datetime.now().strftime("%Y%m%d_%H%M%S")
    port         = args.port         or auto_detect_port()
    session_path = args.out_sessions or f"ftm_sessions_{ts}.csv"
    frame_path   = args.out_frames   or f"ftm_frames_{ts}.csv"

    run(port, args.baud, session_path, frame_path)


if __name__ == "__main__":
    main()