"""
config.py – Lädt Konfiguration aus config.json (MicroPython ujson)
"""

import ujson as json

# Konfiguration laden
with open("config.json") as f:
    CONFIG = json.load(f)

# WLAN
WIFI_SSID = CONFIG["wifi_ssid"]
WIFI_PASSWORD = CONFIG["wifi_password"]

# MQTT
MQTT_BROKER = CONFIG["mqtt_broker"]
MQTT_PORT = CONFIG["mqtt_port"]
MQTT_CLIENT_ID = CONFIG["mqtt_client_id"]
MQTT_TOPIC_PREFIX = CONFIG["mqtt_topic_prefix"]
MQTT_KEEPALIVE_S = CONFIG.get("mqtt_keepalive_s", 60)

# RFID Reader Typ
RFID_READER_TYPE = CONFIG.get("rfid_reader_type", "rc522")

# SPI (für RC522)
SPI_BUS = CONFIG["spi_bus"]
SPI_SCK = CONFIG["spi_sck"]
SPI_MOSI = CONFIG["spi_mosi"]
SPI_MISO = CONFIG["spi_miso"]
SPI_CS = CONFIG["spi_cs"]
SPI_FREQ = CONFIG["spi_freq"]
SPI_RST = CONFIG["spi_rst"]

# I2C (für PN522)
I2C_BUS = CONFIG["i2c_bus"]
I2C_SDA = CONFIG["i2c_sda"]
I2C_SCL = CONFIG["i2c_scl"]
I2C_FREQ = CONFIG["i2c_freq"]

# Betriebsparameter
READ_INTERVAL_MS = CONFIG["read_interval_ms"]
DEBOUNCE_SAME_TAG_S = CONFIG["debounce_same_tag_s"]
