#!/usr/bin/env python3
"""Quick test script for the SEN63C sensor.

Reads all measurands once and prints a table.
Use --loop to read continuously every 2 seconds.

Run on the Pi:
    python3 scripts/read_sensor.py
    python3 scripts/read_sensor.py --loop
"""

import argparse
import sys
import time

# Allow running from the repo root without installing the package
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airshell.sensor import SEN63CSensor


HEADER = f"  {'time':>8}  {'CO2':>6}  {'PM1':>6}  {'PM2.5':>6}  {'PM4':>6}  {'PM10':>6}  {'Temp':>6}  {'Hum':>6}"
HEADER += f"\n  {'':>8}  {'ppm':>6}  {'µg/m³':>6}  {'µg/m³':>6}  {'µg/m³':>6}  {'µg/m³':>6}  {'°C':>6}  {'%RH':>6}"
HEADER += f"\n  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}"


def format_row(data: dict) -> str:
    """Format one reading as a single line."""
    co2 = f"{data['co2']:>6}" if data['co2'] < 32767 else f"{'---':>6}"
    return (
        f"  {time.strftime('%H:%M:%S'):>8}"
        f"  {co2}"
        f"  {data['pm1']:>6.1f}"
        f"  {data['pm25']:>6.1f}"
        f"  {data['pm4']:>6.1f}"
        f"  {data['pm10']:>6.1f}"
        f"  {data['temp']:>6.1f}"
        f"  {data['humidity']:>6.1f}"
    )


def main():
    parser = argparse.ArgumentParser(description="Read SEN63C sensor")
    parser.add_argument(
        "--loop", action="store_true",
        help="Read continuously every 2 seconds (Ctrl+C to stop)",
    )
    args = parser.parse_args()

    try:
        sensor = SEN63CSensor()
        sensor.open()
    except PermissionError:
        print("Error: Permission denied on /dev/i2c-1.")
        print("Run with sudo or add your user to the i2c group:")
        print("  sudo usermod -aG i2c $USER")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: /dev/i2c-1 not found.")
        print("Enable I2C via raspi-config:")
        print("  sudo raspi-config → Interface Options → I2C → Enable")
        sys.exit(1)
    except Exception as e:
        print(f"Error: Could not connect to SEN63C sensor: {e}")
        print("Check wiring: VDD→Pin1 (3.3V), GND→Pin6, SDA→Pin3, SCL→Pin5")
        sys.exit(1)

    try:
        print("\nAirShell — SEN63C  (Ctrl+C to stop)\n")
        print(HEADER)
        while True:
            data = sensor.read()
            print(format_row(data))
            if not args.loop:
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
