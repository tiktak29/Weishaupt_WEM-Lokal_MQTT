<p align="center" style="padding-top: 40px;">
  <img src="wem_mqtt_unified/logo.png" alt="WEM Lokal MQTT Logo" style="max-width: 450px; width: 20%; height: auto;">
</p>

# Weishaupt WEM‑Lokal MQTT

![Version](https://img.shields.io/badge/version-v1.0.2-blue)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-App-green)
![MQTT](https://img.shields.io/badge/MQTT-Discovery-orange)
![License](https://img.shields.io/badge/license-MIT-brightgreen)

Local data extraction from Weishaupt heat pumps via the integrated Weishaupt controller web interface, with MQTT integration and full Home Assistant Discovery – fully local and cloud‑free.

---

> 📌 **Important upgrade note (v1.0.2)**  
> This release updates the configuration schema. Home Assistant does **not** reload schema changes during normal updates.  
>  
> To ensure the new configuration is applied correctly, please **uninstall the app (formerly add-on) once and install it again**.  
>  
> This refreshes the schema, removes deprecated options (e.g., `enable_wp`), and prevents repeated Supervisor warnings.

---

## ✔️ Compatibility

The app communicates exclusively with the local **Weishaupt WEM-Lokal web interface**.  
No model-specific configuration is required.

### How to check if your system is compatible

Your system is compatible if:

- your Weishaupt controller includes the **Webserver** menu (installer/service level), and
- the local WEM-Lokal web interface is enabled.

At startup, the app automatically detects:

- the connected heat pump model  
- all available devices (HK1, HK2, Statistics, WEZ2)

No manual model selection or device configuration is required.

Learn how to check and enable the Webserver in the Home Assistant Community Forum:

💡 **Home Assistant Community Forum – How to enable the Webserver**  
https://community.home-assistant.io/t/weishaupt-heatpump-integration-via-modbus/436823/210?page=13

---

## Feedback & Compatibility Reports

To help improve compatibility across different Weishaupt WEM configurations, please share your setup and results in the discussion thread:

➡️ **[Feedback: Tested WEM-Lokal Heat Pump Configurations](https://github.com/tiktak29/Weishaupt_WEM-Lokal_MQTT/discussions/1)**


---

## Overview

This integration acts as a local polling gateway that reads data from the Weishaupt WEM web interface and publishes it to Home Assistant via MQTT Discovery.

All devices and sensors are automatically created in Home Assistant through **MQTT Discovery**.

#### Weishaupt Web UI → HTTP scraping → Python gateway → data normalization → MQTT → Home Assistant Discovery

Developed to integrate Weishaupt systems into modern Home Assistant environments without requiring any official API access.

---

- Fully local communication  
- No cloud services involved  
- No external APIs or accounts required  
- Operates solely via the local Weishaupt web interface

---

## Requirements

- Weishaupt heat pump with up‑to‑date firmware  
- Web interface: Webserver must be enabled on the controller  
- Web interface: Username and password must be configured
- Web interface: 4-digit HEX code must be available
- Home Assistant  
- MQTT broker (e.g., Mosquitto)

<sub>💡 Additional information regarding the web interface is provided in the lower section of this document.</sub>

---

## Features

### Local Communication
- Direct access to the Weishaupt controller via the integrated web interface  
- No cloud connection required  
- No internet connection required  
- Fully local communication

### Home Assistant Integration
- Full MQTT Discovery support  
- Automatic creation of all devices and sensors  
- Automatic device assignment  
- Automatic updates  
- Support for paho‑mqtt API v1/v2

### Supported Devices
- Heat pump (always enabled automatically)  
- Heating circuit 1  
- Heating circuit 2  
- Statistics  
- Second auxiliary heater (WEZ2)

### Reliability
- Automatic session re‑authentication  
- Retry mechanism  
- Robust HTTP error handling  
- Automatic recovery  
- Round‑robin polling

### Communication Quality Monitoring
Tracks:
- First-request success rate  
- Retry success rate  
- Final failures  
- Daily communication statistics

### Advanced Features
- Automatic heat pump model detection  
- Dynamic activation of HK1, HK2, Statistics and WEZ2  
- MQTT connection status monitoring  
- Extended diagnostic information  
- Fully local operation

<br>

![Dashboard](images/dashboard.jpg)

---

## Installation

### **1. Add the repository**

Home Assistant → Settings → Apps → Install App → ⋮ → Repositories → Add

Repository URL:  
https://github.com/tiktak29/Weishaupt_WEM-Lokal_MQTT

### **2. Install the app**
- Select “Weishaupt WEM‑Local MQTT”  
- Install  
- Configure  
- Start  
- Check logs

---

## Configuration

### **Options (config.json)**

| Parameter | Description |
|----------|-------------|
| `webinterface_ip_address` | web interface IP address |
| `webinterface_username` | web interface username |
| `webinterface_password` | web interface password |
| `webinterface_hex_code` | 4‑digit HEX code from the URL |
| `mqtt_broker` | MQTT broker |
| `mqtt_port` | MQTT port |
| `mqtt_username` | MQTT username |
| `mqtt_password` | MQTT password |
| `polling_seconds` | Polling interval |
| `enable_hk1` | Heating circuit 1 |
| `enable_hk2` | Heating circuit 2 |
| `enable_stats` | Statistics |
| `enable_wez2` | Second auxiliary heater |

> ℹ️ Note:  
> The heat pump section (`enable_wp`) was removed in v1.0.2.  
> The app now automatically detects and activates the heat pump device.

![Configuration 1](images/config-1.jpg)  
![Configuration 2](images/config-2.jpg)

---

## Startup Log

![Startup Log](images/startup-log.jpg)

---

## Daily Statistics Log

![daily-statistics-log](images/daily-statistics-log.jpg)

---

## Important Notice

⚠️ While the app is running, you should not access the heat pump's local web interface through a browser at the same time.

Parallel data requests may cause the web interface to become unstable.

In rare cases, this may require restarting the controller.

### ✅ Cloud Access Unaffected

The official Weishaupt WEM portal can still be used without restrictions.

---

## 1. Enable the web interface

Guide:  
💡 [Home Assistant Community Forum](https://community.home-assistant.io/t/weishaupt-heatpump-integration-via-modbus/436823/210?page=13)

---

## 2. Create web interface username and password

Open the local IP address of the heat pump → create a username → set a password.

---

## 3. Determine the 4‑digit HEX code

Navigate to:  
Expert Mode → Info → Heating Circuit 1

Example URL:

`http://192.168.178.xx/settings_export.html?stack=0C00000100000000008000????010002000301,0C000C1900000000000000????020003000401`

The `????` represent the 4‑digit HEX code.

![HEX Code Example](images/hex-code-example.jpg)

---

## Changelog

See: [CHANGELOG.md](./CHANGELOG.md)

---

## License

MIT License  
See: [LICENSE](./LICENSE)

---

## Thanks

Thanks to everyone who tests, improves, and extends this project.  
Feedback is welcome.

---

## Disclaimer

This project is an independent open‑source project and is not affiliated with Weishaupt GmbH.

Use at your own risk.  
The developers assume no liability for any damage or malfunction caused by the use of this app.
