"""
MQTT Client Wrapper für den Pico W.
Nutzt umqtt.simple aus der MicroPython-Standardbibliothek.
"""

import json
import time
from umqtt.simple import MQTTClient as _MQTTClient


class MQTTError(Exception):
    pass


class MQTTManager:
    def __init__(
        self,
        broker_ip: str,
        client_id: str = "pico_nfc",
        port: int = 1883,
        topic_prefix: str = "nfc",
        keepalive: int = 60,
    ):
        self._broker = broker_ip
        self._port = port
        self._client_id = client_id
        self._topic_prefix = topic_prefix
        self._keepalive = keepalive
        self._client: _MQTTClient | None = None

    def connect(self, retries: int = 5) -> bool:
        """Verbindet mit dem MQTT Broker. Gibt True bei Erfolg zurück."""
        for attempt in range(1, retries + 1):
            try:
                self._client = _MQTTClient(
                    self._client_id,
                    self._broker,
                    port=self._port,
                    keepalive=self._keepalive,
                )
                self._client.connect()
                print(f"[MQTT] Verbunden mit {self._broker}:{self._port}")
                return True
            except Exception as e:
                print(f"[MQTT] Verbindung fehlgeschlagen (Versuch {attempt}/{retries}): {e}")
                time.sleep(2)
        return False

    def publish_tag(self, tag: dict, extra: dict = None):
        """
        Publiziert einen NFC-Tag-Read als JSON.
        tag: dict vom PN532 (uid_hex, tag_type, atqa, sak, …)
        extra: optionale Zusatzfelder (z.B. timestamp)
        """
        if self._client is None:
            raise MQTTError("Nicht verbunden")

        payload = {
            "uid":       tag.get("uid_hex", ""),
            "uid_dec":   tag.get("uid_dec", 0),
            "tag_type":  tag.get("tag_type", ""),
            "atqa":      tag.get("atqa", ""),
            "sak":       tag.get("sak", ""),
        }
        if extra:
            payload.update(extra)

        topic = f"{self._topic_prefix}/tag"
        try:
            self._client.publish(topic, json.dumps(payload))
            print(f"[MQTT] Publiziert auf '{topic}': {payload}")
        except Exception as e:
            print(f"[MQTT] Publish fehlgeschlagen: {e}")
            self._client = None  # Reconnect beim nächsten Mal erzwingen
            raise MQTTError(str(e))

    def ping(self):
        """Keepalive-Ping, sollte regelmäßig aufgerufen werden."""
        if self._client:
            try:
                self._client.ping()
            except Exception:
                self._client = None

    def is_connected(self) -> bool:
        return self._client is not None

    def disconnect(self):
        if self._client:
            self._client.disconnect()
            self._client = None
