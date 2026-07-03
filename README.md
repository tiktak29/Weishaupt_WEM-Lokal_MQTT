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

## Feedback & Compatibility Reports

To help improve compatibility across different Weishaupt WEM configurations, please share your setup and results in the discussion thread:

➡️ **[Feedback: Tested WEM-Lokal Heat Pump Configurations](https://github.com/tiktak29/Weishaupt_WEM-Lokal_MQTT/discussions/1)**

#### Tested with a Weishaupt WAB 14 (EC WAB V5.3 R10 / WWP‑SG V5.0) — developer system<br>
#### Tested with a Weishaupt WBB 12 (controller version not specified) — user feedback


---

## Overview
This integration acts as a local polling gateway that reads data from the Weishaupt WEM web interface and publishes it to Home Assistant via MQTT Discovery.

All devices and sensors are automatically created in Home Assistant through **MQTT Discovery**.
<br>
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
- Web interface: Webserver must be enabled on the controller (ensure before installing the app)
- Web interface: Username and password must be created and known (ensure before installing the app)
- Web interface: 4‑digit HEX code must be known (ensure before installing the app)
- Home Assistant  
- MQTT broker (e.g., Mosquitto)  

<sub>💡 <a href="#important-notice">Note: Additional information regarding the web interface is provided in the lower section of this document.</a></sub>


---

## Features

### Local Communication
- Direct access to the Weishaupt controller via the integrated web interface  
- No cloud connection required  
- No internet connection required for operation  
- Direct communication within the local network  

### Home Assistant Integration
- Full MQTT Discovery support  
- Automatic creation of all devices and sensors  
- Automatic device assignment in Home Assistant  
- Automatic updates of sensors and entities  
- Support for current and legacy MQTT libraries (paho‑mqtt API v1/v2)  

### Supported Devices
- Heat pump (always enabled automatically)  
- Heating circuit 1  
- Heating circuit 2  
- Statistics  
- Second auxiliary heater (WEZ2)  

### Reliability
- Automatic session re‑authentication  
- Retry mechanism for empty or incomplete responses  
- Robust HTTP error handling  
- Automatic recovery after communication failures  
- Round‑robin polling to reduce load on the web interface  

### Communication Quality Monitoring
- Long-term monitoring of the web interface reliability  
- Included in the daily statistics log
- Tracks:
  - First-request success rate  
  - Retry success rate  
  - Final failures  
  - Daily communication statistics 

### Advanced Features
- Automatic heat pump model detection
- Automatic activation of the heat pump section  
- Dynamic activation of HK1, HK2, Statistics and WEZ2   
- MQTT connection status monitoring  
- Extended diagnostic information for improved troubleshooting  
- Fully local operation without external dependencies

<br>

![Dashboard](images/dashboard.jpg)

---

## Installation

### **1. Add the repository**
In Home Assistant:

**Settings → Apps → Install App → ⋮ → Repositories → Add → Enter URL → Add**

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
| `mqtt_broker` | MQTT broker (e.g., core‑mosquitto) |
| `mqtt_port` | MQTT port |
| `mqtt_username` | MQTT username |
| `mqtt_password` | MQTT password |
| `polling_seconds` | Polling interval in seconds |
| `enable_hk1` | Heating circuit 1 |
| `enable_hk2` | Heating circuit 2 |
| `enable_stats` | Statistics |
| `enable_wez2` | Second auxiliary heater (WEZ2) |

> ℹ️ Note:  
> The heat pump section (`enable_wp`) was removed in v1.0.2.  
> The app now automatically detects and activates the heat pump device.  

![Configuration 1](images/config-1.jpg)  
![Configuration 2](images/config-2.jpg)

---
## Startup Log
The startup log documents the initialization sequence, background login, device discovery, and readiness of all components.

<br>

![Startup Log](images/startup-log.jpg)

---

## Daily Statistics Log
The daily statistics provide insight into communication quality and reliability, including first-request success rates, retry success rates, and final failures.

<br>

![daily-statistics-log](images/daily-statistics-log.jpg)

---

## Important Notice

⚠️ While the app is running, you should not access the heat pump's local web interface through a browser at the same time.  
This applies only to the local web interface of the device itself and does not affect access to the cloud portal.

Parallel data requests may cause the heat pump's web interface to become unstable.

In rare cases, this may cause the controller to become unresponsive, requiring the heat pump controller to be restarted before communication can be restored.

Therefore, it is recommended to avoid additional browser access to the local web interface while the app is running.

### ✅ Cloud Access Unaffected

The official Weishaupt WEM portal can still be used without restrictions.

---

## 1. Enable the web interface
- The following link provides a detailed guide on how to enable the web interface in the Weishaupt controller.  
  💡 [Instructions in the Home Assistant Community Forum](https://community.home-assistant.io/t/weishaupt-heatpump-integration-via-modbus/436823/210?page=13)
<br><br>

## 2. Create web interface username and password
- Open the local IP address of the heat pump in your browser (e.g., http://192.168.178.xx), create a username and set a password.
- These credentials are required in the app configuration as the web interface username and password.
<br><br>

## 3. Determine the 4‑digit HEX code
- Open the web interface in your browser.
- Navigate to: Expert Mode → Info → Heating Circuit 1  
- The browser will show a URL similar to:  
  `http://192.168.178.xx/settings_export.html?stack=0C00000100000000008000????010002000301,0C000C1900000000000000????020003000401`
- The `????` in both blocks represent the 4‑digit HEX code.

The HEX code must be identical in both URL blocks and is required in the app configuration as `webinterface_hex_code`.

![HEX Code Example](images/hex-code-example.jpg)

---


## Changelog

See: [CHANGELOG.md](./CHANGELOG.md)

---

## License

This project uses the **MIT License**.  
See: [LICENSE](./LICENSE)

---

## Thanks

Thanks to everyone who tests, improves, and extends this project.  
Feedback and suggestions are welcome.

---

<br><br>

## Disclaimer
This project is an independent open‑source project and is not affiliated with Weishaupt GmbH.

The app was developed in spare time based on publicly available information.  
Use at your own risk and responsibility.  

The developers assume no liability for any damage or malfunction caused by the use of this app.

---
