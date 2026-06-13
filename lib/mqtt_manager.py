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
        self._command_callback = None  # Stored so reconnects can restore it

    def connect(self, retries: int = 5) -> bool:
        """Verbindet mit dem MQTT Broker. Gibt True bei Erfolg zurück."""
        for attempt in range(1, retries + 1):
            try:
                client = _MQTTClient(
                    self._client_id,
                    self._broker,
                    port=self._port,
                    keepalive=self._keepalive,
                )
                client.connect()  # pyright: ignore[reportOptionalMemberAccess]
                self._client = client
                print(f"[MQTT] Verbunden mit {self._broker}:{self._port}")

                # Restore callback and subscription on every connect (including reconnects)
                if self._command_callback:
                    self._client.set_callback(self._command_callback)
                    self._client.subscribe(f"{self._topic_prefix}/command")
                    print(f"[MQTT] Command subscription restored: {self._topic_prefix}/command")

                # Sende Status-Message für GroundControl Dashboard
                self.publish_status("online")
                return True
            except Exception as e:
                print(f"[MQTT] Verbindung fehlgeschlagen (Versuch {attempt}/{retries}): {e}")
                time.sleep(2)
        return False

    def publish_status(self, status: str):
        """
        Publiziert den Gerätestatus.
        Wird vom GroundControl Dashboard erkannt.
        """
        if self._client is None:
            raise MQTTError("Nicht verbunden")

        payload = json.dumps({"status": status, "timestamp": time.time()})
        topic = f"{self._topic_prefix}/status"
        try:
            self._client.publish(topic, payload)
            print(f"[MQTT] Status gesendet: {status}")
        except Exception as e:
            print(f"[MQTT] Status-Publish fehlgeschlagen: {e}")
            self._client = None
            raise MQTTError(str(e))

    def publish_heartbeat(
        self,
        nfc_ok: bool,
        nfc_error: str | None = None,
        uptime: int = 0,
        requires_permission: bool = True,
    ):
        """
        Publiziert einen Heartbeat mit Systemstatus.
        Wird vom GroundControl Dashboard zur Geräteeinordnung genutzt.
        """
        if self._client is None:
            raise MQTTError("Nicht verbunden")

        payload = {
            "status": "online",
            "nfc_ok": nfc_ok,
            "uptime": uptime,
            "timestamp": time.time(),
            "requires_permission": requires_permission,
        }
        if nfc_error:
            payload["nfc_error"] = nfc_error

        topic = f"{self._topic_prefix}/heartbeat"
        try:
            self._client.publish(topic, json.dumps(payload))
            # Kein print bei jedem Heartbeat zu vermeiden von Spam
        except Exception as e:
            print(f"[MQTT] Heartbeat-Publish fehlgeschlagen: {e}")
            self._client = None
            raise MQTTError(str(e))

    def publish_tag(self, tag: dict, extra: dict | None = None):
        """
        Publiziert einen NFC-Tag-Read als JSON.
        tag: dict vom PN532 (uid_hex, tag_type, atqa, sak, …)
        extra: optionale Zusatzfelder (z.B. timestamp)
        """
        if self._client is None:
            raise MQTTError("Nicht verbunden")

        payload = {
            "uid": tag.get("uid_hex", ""),
            "uid_dec": tag.get("uid_dec", 0),
            "tag_type": tag.get("tag_type", ""),
            "atqa": tag.get("atqa", ""),
            "sak": tag.get("sak", ""),
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

    def set_command_callback(self, callback):
        """Setzt einen Callback für eingehende Commands."""
        if self._client is None:
            raise MQTTError("Nicht verbunden")
        self._command_callback = callback
        self._client.set_callback(callback)

    def subscribe_commands(self):
        """Abonniert das Command-Topic."""
        if self._client is None:
            raise MQTTError("Nicht verbunden")
        topic = f"{self._topic_prefix}/command"
        try:
            self._client.subscribe(topic)
            print(f"[MQTT] Subscribed to '{topic}'")
        except Exception as e:
            print(f"[MQTT] Subscribe fehlgeschlagen: {e}")
            raise MQTTError(str(e))

    def check_messages(self):
        """Prüft auf eingehende MQTT-Nachrichten (non-blocking)."""
        if self._client:
            try:
                self._client.check_msg()
            except Exception as e:
                print(f"[MQTT] check_msg fehlgeschlagen: {e}")
                self._client = None

    def ping(self):
        """Keepalive-Ping, sollte regelmäßig aufgerufen werden."""
        if self._client:
            try:
                self._client.ping()
            except Exception:
                self._client = None

    def is_connected(self) -> bool:
        return self._client is not None

    def publish_write_response(self, result: dict):
        """Publish card write operation result."""
        if self._client is None:
            raise MQTTError("Nicht verbunden")

        topic = f"{self._topic_prefix}/write_response"
        payload = json.dumps(result)
        try:
            self._client.publish(topic, payload)
            print(f"[MQTT] Write response: {result}")
        except Exception as e:
            print(f"[MQTT] Write response failed: {e}")
            self._client = None
            raise MQTTError(str(e))

    def disconnect(self):
        if self._client:
            # Sende Offline-Status bevor disconnect
            try:
                self.publish_status("offline")
            except Exception:
                pass  # Best effort, fehler hier sind nicht kritisch
            self._client.disconnect()
            self._client = None
