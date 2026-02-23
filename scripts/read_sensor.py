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


def format_table(data: dict) -> str:
    """Format sensor readings as a human-readable table."""
    rows = [
        ("CO2",         f"{data['co2']:>8} ppm"),
        ("PM1.0",       f"{data['pm1']:>8.1f} µg/m³"),
        ("PM2.5",       f"{data['pm25']:>8.1f} µg/m³"),
        ("PM4.0",       f"{data['pm4']:>8.1f} µg/m³"),
        ("PM10",        f"{data['pm10']:>8.1f} µg/m³"),
        ("Temperature", f"{data['temp']:>8.1f} °C"),
        ("Humidity",    f"{data['humidity']:>8.1f} %RH"),
    ]
    lines = [f"  {label:<14} {value}" for label, value in rows]
    return "\n".join(lines)


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
        if args.loop:
            print("Reading SEN63C every 2s (Ctrl+C to stop)\n")
            while True:
                data = sensor.read()
                # Clear screen and reprint for a live-updating display
                print("\033[2J\033[H", end="")
                print("AirShell — SEN63C live readings\n")
                print(format_table(data))
                print(f"\n  {time.strftime('%H:%M:%S')}")
                time.sleep(2)
        else:
            data = sensor.read()
            print("AirShell — SEN63C reading\n")
            print(format_table(data))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
