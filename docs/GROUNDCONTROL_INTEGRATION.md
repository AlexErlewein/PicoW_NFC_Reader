# PicoW NFC Reader → GroundControl Integration

This project connects to the MakerPi GroundControl MQTT broker running on Raspberry Pi.

## Configuration

The `config.json` has been configured for GroundControl:

```json
{
  "mqtt_broker": "192.168.178.47",  // Raspberry Pi IP
  "mqtt_port": 1883,
  "mqtt_client_id": "picow_nfc_01",
  "mqtt_topic_prefix": "picow_nfc_01"  // Used as device_id in GroundControl
}
```

## MQTT Topics

The PicoW publishes to these topics:

| Topic | Payload | Purpose |
|-------|---------|---------|
| `picow_nfc_01/status` | `{"status": "online"}` | Sent on connection |
| `picow_nfc_01/status` | `{"status": "offline"}` | Sent on disconnect |
| `picow_nfc_01/tag` | `{...tag data...}` | NFC tag scan |

## Tag Data Format

```json
{
  "uid": "04:A3:B5:C2:D3:E0",
  "uid_dec": 123456789,
  "tag_type": "Mifare Classic 1K",
  "atqa": "0004",
  "sak": "08",
  "timestamp": "1710745200.123"
}
```

## GroundControl Dashboard

Access at: http://192.168.178.47:8000

You should see:
- Device `picow_nfc_01` listed under **Devices**
- Status: **online** (green badge)
- NFC tag scans under **Recent Messages**

## Quick Test

1. Deploy GroundControl to your Pi:
   ```bash
   cd /path/to/MakerPi_GroundControl
   sudo bash scripts/setup.sh
   ```

2. Upload this project to your Pico W:
   ```bash
   mpremote cp . :
   mpremote run main.py
   ```

3. Check the dashboard - the device should appear within seconds

## Monitoring

To see MQTT messages in real-time on the Pi:
```bash
mosquitto_sub -h localhost -t "picow_nfc_01/#" -v
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Device not in dashboard | Check MQTT broker is running: `sudo systemctl status mosquitto` |
| Status shows "offline" | Check Pico W can reach Pi: ping 192.168.178.47 |
| No messages on scan | Check topic prefix matches config |

## Network Setup

Both Pico W and Raspberry Pi must be on the same WiFi network.
