"""
config.py – Lädt Konfiguration aus config.json (MicroPython ujson)
"""

import ujson

try:
    with open("config.json", "r") as f:
        _cfg = ujson.load(f)
except OSError:
    msg = "config.json nicht gefunden!"
    msg += " Kopiere example-config.json nach config.json und passe die Werte an."
    raise RuntimeError(msg)

# --- WLAN ---
WIFI_SSID = _cfg["wifi_ssid"]
WIFI_PASSWORD = _cfg["wifi_password"]

# --- MQTT Broker ---
MQTT_BROKER = _cfg["mqtt_broker"]
MQTT_PORT = _cfg["mqtt_port"]
MQTT_CLIENT_ID = _cfg["mqtt_client_id"]
MQTT_TOPIC_PREFIX = _cfg["mqtt_topic_prefix"]
MQTT_KEEPALIVE_S = _cfg["mqtt_keepalive_s"]

# --- SPI Pins (Pico W) ---
SPI_BUS = _cfg["spi_bus"]
SPI_SCK = _cfg["spi_sck"]
SPI_MOSI = _cfg["spi_mosi"]
SPI_MISO = _cfg["spi_miso"]
SPI_CS = _cfg["spi_cs"]
SPI_FREQ = _cfg["spi_freq"]
SPI_RST = _cfg["spi_rst"]

# --- Verhalten ---
READ_INTERVAL_MS = _cfg["read_interval_ms"]
DEBOUNCE_SAME_TAG_S = _cfg["debounce_same_tag_s"]
