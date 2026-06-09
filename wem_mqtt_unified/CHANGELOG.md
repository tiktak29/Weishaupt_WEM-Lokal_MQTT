# Changelog

## v1.0.1 – System Status & Last Update Enhancements

### New Features

* **System Status Sensor (online/offline)**  
  Added a dedicated MQTT sensor that reflects the real-time connectivity status of the WEM local web interface.  
  The sensor automatically switches to **offline** if no successful data retrieval occurs for 5 minutes.

* **Last Successful Update Timestamp**  
  Added a new MQTT sensor with `device_class: timestamp` that publishes the exact ISO‑8601 UTC timestamp of the most recent successful data update.

### Improvements

* Improved status monitoring with separate tracking of login, session stability, and data retrieval  
* Optimized MQTT publishing for system status and last update information  
* Minor internal code cleanup and structural improvements


## v1.0.0 – Initial Release

### Features
- Local data extraction via the WEM web interface
- MQTT Discovery (Home Assistant compatible)
- Heat pump
- Heating circuit 1
- Heating circuit 2
- Statistics
- Second auxiliary heater (WEZ2)
- Retry system for empty responses
- Automatic session re‑authentication
- Model detection
- Daily statistics
- MQTT API v1/v2 compatibility

### Improvements
- Robust HTTP error handling
- Session recovery
- Discovery control
- Dynamic device activation
