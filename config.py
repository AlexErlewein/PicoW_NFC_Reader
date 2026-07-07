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
REQUIRES_PERMISSION = CONFIG.get("requires_permission", True)

# RFID Reader Typ
RFID_READER_TYPE = CONFIG.get("rfid_reader_type", "rc522")

# SPI (für RC522) - optional, nur wenn rfid_reader_type="rc522"
SPI_BUS = CONFIG.get("spi_bus", 0)
SPI_SCK = CONFIG.get("spi_sck", 6)
SPI_MOSI = CONFIG.get("spi_mosi", 7)
SPI_MISO = CONFIG.get("spi_miso", 4)
SPI_CS = CONFIG.get("spi_cs", 5)
SPI_FREQ = CONFIG.get("spi_freq", 100000)
SPI_RST = CONFIG.get("spi_rst", 22)

# I2C (für PN522)
I2C_BUS = CONFIG["i2c_bus"]
I2C_SDA = CONFIG["i2c_sda"]
I2C_SCL = CONFIG["i2c_scl"]
I2C_FREQ = CONFIG["i2c_freq"]

# Betriebsparameter
READ_INTERVAL_MS = CONFIG["read_interval_ms"]
DEBOUNCE_SAME_TAG_S = CONFIG["debounce_same_tag_s"]

# LED Hardware-Konfiguration
# led_green_defect: Grüne LED ist defekt → Blau für Idle & Success, Rot für Error.
# led_pins_swapped: GPIO14 ist der Common-Pin (statt GND/VCC), eine Anode auf GND
#                  → nur Blau und Rot nutzbar, Grün-Kanal wird immer auf 0 gehalten.
# led_common_anode: LED ist Common-Anode (statt Common-Cathode) → PWM invertiert,
#                   Common-Pin wird auf HIGH gezogen.
LED_GREEN_DEFECT = CONFIG.get("led_green_defect", False)
LED_PINS_SWAPPED = CONFIG.get("led_pins_swapped", False)
LED_COMMON_ANODE = CONFIG.get("led_common_anode", False)
