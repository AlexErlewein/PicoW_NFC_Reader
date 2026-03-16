"""
WLAN-Verbindungshelfer für den Pico W.
"""

import network
import time


class WifiError(Exception):
    pass


def connect(ssid: str, password: str, timeout_s: int = 20) -> str:
    """
    Verbindet mit dem WLAN. Gibt die IP-Adresse zurück.
    Wirft WifiError bei Fehler.
    """
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print(f"[WLAN] Bereits verbunden. IP: {ip}")
        return ip

    print(f"[WLAN] Verbinde mit '{ssid}' …")
    wlan.connect(ssid, password)

    deadline = time.time() + timeout_s
    while not wlan.isconnected():
        if time.time() > deadline:
            wlan.active(False)
            raise WifiError(f"Timeout: Kein WLAN nach {timeout_s}s")
        status = wlan.status()
        if status == network.STAT_WRONG_PASSWORD:
            raise WifiError("Falsches WLAN-Passwort")
        if status == network.STAT_NO_AP_FOUND:
            raise WifiError(f"SSID '{ssid}' nicht gefunden")
        time.sleep(0.5)

    ip = wlan.ifconfig()[0]
    print(f"[WLAN] Verbunden! IP: {ip}")
    return ip


def disconnect():
    wlan = network.WLAN(network.STA_IF)
    wlan.disconnect()
    wlan.active(False)
