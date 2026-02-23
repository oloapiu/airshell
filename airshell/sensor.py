"""SEN63C sensor driver for AirShell.

Wraps the Sensirion I2C driver into a simple open/read/close interface.
Returns plain dicts so callers don't depend on Sensirion signal types.
"""

import time

from sensirion_i2c_driver import LinuxI2cTransceiver, I2cConnection, CrcCalculator
from sensirion_driver_adapters.i2c_adapter.i2c_channel import I2cChannel
from sensirion_i2c_sen63c.device import Sen63cDevice

# SEN63C fixed I2C address
_SLAVE_ADDRESS = 0x6B

# CRC parameters specified by Sensirion for SEN6x family
_CRC = CrcCalculator(8, 0x31, 0xFF, 0x00)


class SEN63CSensor:
    """Reads CO2, PM, temperature, and humidity from a Sensirion SEN63C over I2C.

    Usage:
        with SEN63CSensor() as sensor:
            data = sensor.read()
            print(data["co2"])

    Or manually:
        sensor = SEN63CSensor()
        sensor.open()
        data = sensor.read()
        sensor.close()
    """

    def __init__(self, i2c_port: int = 1):
        self._device_path = f"/dev/i2c-{i2c_port}"
        self._transceiver = None
        self._device = None

    def open(self):
        """Connect to the sensor, reset it, and start continuous measurement."""
        self._transceiver = LinuxI2cTransceiver(self._device_path)
        channel = I2cChannel(
            I2cConnection(self._transceiver),
            slave_address=_SLAVE_ADDRESS,
            crc=_CRC,
        )
        self._device = Sen63cDevice(channel)

        # Reset puts the sensor into a known state
        self._device.device_reset()
        time.sleep(1.2)

        self._device.start_continuous_measurement()

        # Poll data-ready flag instead of sleeping blind — sensor returns
        # sentinel values (0x7FFF / 0xFFFF) until it has a valid reading.
        for _ in range(20):
            time.sleep(0.5)
            try:
                (ready,) = self._device.read_data_ready_flag()
                if ready:
                    break
            except Exception:
                pass

    def close(self):
        """Stop measurement and release the I2C bus."""
        if self._device is not None:
            try:
                self._device.stop_measurement()
            except Exception:
                pass
            self._device = None
        if self._transceiver is not None:
            try:
                self._transceiver.close()
            except Exception:
                pass
            self._transceiver = None

    def read(self) -> dict:
        """Read all measurands and return a plain dict.

        Returns:
            dict with keys: co2 (ppm), pm1/pm25/pm4/pm10 (µg/m³),
            temp (°C), humidity (%RH). Values are floats (int for co2).
        """
        if self._device is None:
            raise RuntimeError("Sensor not open — call open() first")

        pm1, pm25, pm4, pm10, humidity, temp, co2 = (
            self._device.read_measured_values()
        )

        return {
            "co2": co2.value,
            "pm1": pm1.value,
            "pm25": pm25.value,
            "pm4": pm4.value,
            "pm10": pm10.value,
            "temp": temp.value,
            "humidity": humidity.value,
        }

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False
