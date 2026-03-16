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

# --- I2C Pins (Pico W) ---
I2C_BUS = _cfg["i2c_bus"]
I2C_SDA = _cfg["i2c_sda"]
I2C_SCL = _cfg["i2c_scl"]
I2C_FREQ = _cfg["i2c_freq"]

# --- Verhalten ---
READ_INTERVAL_MS = _cfg["read_interval_ms"]
DEBOUNCE_SAME_TAG_S = _cfg["debounce_same_tag_s"]
