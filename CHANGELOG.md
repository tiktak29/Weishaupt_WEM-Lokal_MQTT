# Changelog

## v1.1.1 – Persistent WebIF Sessions & Recovery

### Upgrade notes

* Update normally from v1.1.0. Configuration options and schema are unchanged; no uninstall or reconfiguration is required for this update.
* The first start after updating from v1.1.0 uses the fresh-login path because that version did not persist session cookies. Later restarts and updates can reuse the validated saved session.
* Keep the app's data to retain the saved session. Legacy v1.0.x configuration cleanup is described separately in the README.

### Changes

* **Persistent validated WebIF sessions**
  * Save a session cookie only after protected-page validation
  * Validate the saved session at startup before creating a new login
  * Continue without a new login when the saved session is accepted
  * Retain the saved cookie during transport errors; use the fresh-login path after an explicit login-page rejection
  * Distinguish fresh-session setup and saved-session validation in the public log

* **Session-preserving runtime recovery**
  * Revalidate the existing session before attempting a controlled replacement login
  * Handle transport interruptions separately from session rejection
  * Use 15-minute recovery pauses for the first four checks, then 30-minute pauses
  * Rate-limit replacement logins during long recovery
  * Resume polling after successful in-process recovery

* **Daily WebIF rest**
  * Add a 90-second request-free pause after the daily statistics are generated
  * Recreate the HTTP client while preserving the WebIF session cookie

* **Home Assistant diagnostics and public logging**
  * Add the WebIF-Status diagnostic sensor for connection and recovery states
  * Classify system status, last update and daily success sensors as diagnostic entities
  * Use compact startup, recovery, shutdown and daily statistics messages
  * Remove the duplicate startup banner from `run.sh`; the Python startup message includes the app version

* **Documentation and release metadata**
  * Document both startup paths, saved-session handling and daily rest
  * Mark older log screenshots as v1.1.0 examples
  * Align public version labels to v1.1.1

### Notes

The automatic device support, Round-Robin sequence builder, value parser, value normalization and existing sensor identifiers are retained.
Session reuse has been tested with Stop → Start, a normal Home Assistant app update, and a temporarily disabled Webserver that subsequently accepted the same saved cookie.
Example validation times are observations, not performance guarantees. Saved-session timing checks run between HTTP requests, so an in-flight request can delay the configured startup deadline.

## v1.1.0 – Fully Automatic WebIF Detection

> ⚠️ **Important upgrade note**
>
> This release introduces a completely redesigned startup process and a simplified configuration.
>
> A direct update from v1.0.x to v1.1.0 is supported, and the app should continue to run normally.
>
> However, obsolete configuration options from v1.0.x may remain stored internally and generate repeated Supervisor warnings.
>
> **For a clean migration, existing v1.0.x users should first update to v1.1.0, then uninstall the app once and install it again.**
>
> The v1.1.0 app remains available in the Home Assistant App Store after uninstalling and can be installed again directly.
>
> This ensures:
>
> * obsolete configuration options are removed
> * Supervisor warnings caused by old options are eliminated
> * the new configuration schema is applied cleanly
> * only the configuration options required by v1.1.0 are stored

### Changes

* **Fully automatic WebIF detection**

  * Added automatic detection of the WebIF overview and supported data structure
  * Added automatic detection of all supported WebIF data URLs
  * Removed the need for manual HEX configuration
  * Removed all static WebIF data URLs
  * Removed all model-specific URL handling

* **Automatic device detection**

  * Added automatic detection of all supported WebIF devices
  * Added dynamic support for heating circuits HK1–HK4
  * Polling sequence is now generated automatically based on detected devices
  * MQTT Discovery now creates only detected devices

* **Configuration simplification**

  * Removed `webinterface_hex_code`
  * Removed `enable_hk1`
  * Removed `enable_hk2`
  * Removed `enable_stats`
  * Removed `enable_wez2`
  * Reduced configuration to WebIF credentials, MQTT settings and polling interval

* **Startup improvements**

  * Added automatic WebIF initialization
  * Added automatic overview detection
  * Added automatic data URL detection
  * Added automatic active device detection
  * Improved startup logging
  * Added optional `DEBUG_WEBIF` logging for diagnostics

* **Documentation**

  * Completely reworked README
  * Added compatibility documentation
  * Added WebIF activation guide
  * Added WebIF login instructions
  * Added startup log documentation
  * Added operating recommendations
  * Updated installation and configuration guide

### Notes

This release represents the largest architectural improvement since the initial project release.

The app now adapts automatically to the connected Weishaupt controller and its available WebIF devices without requiring manual HEX configuration, static URLs or manual device selection.

The core communication, session handling, retry logic, Round-Robin polling and MQTT communication remain unchanged and continue to build on the proven architecture introduced in previous releases.

## v1.0.2 – Configuration Cleanup & Stability Improvements  

> ⚠️ **Important upgrade note**
> 
> This release updates the configuration schema. Home Assistant does **not** reload schema changes during normal updates.
>
> To apply the new configuration correctly, please **uninstall the app (formerly add-on) once and install it again**.
>
> This ensures:
> - the new `config.json` is loaded
> - removed options (e.g., `enable_wp`) disappear
> - Supervisor warnings stop
> - the configuration UI matches the new schema

### Changes

* **Updated `config.json`**
  - Removed deprecated option `enable_wp`
  - Cleaned up and aligned all configuration keys with the current feature set
  - Synchronized `options` and `schema`
  - Added strict validation for `polling_seconds` using predefined allowed values

* **Improved URL and device activation logic**
  - URL generation now follows the enabled modules (`HK1`, `HK2`, `Statistik`, `WEZ2`)
  - Removed unused WP module references
  - Kept `URLS` and `SEQUENCE` automatically synchronized with the active configuration

* **Script stability improvements**
  - Refined initial synchronization logic
  - Improved Round-Robin polling behavior
  - Improved recovery from `session_broken` states
  - Refined daily statistics trigger and reset handling
  - Improved MQTT state publishing consistency

* **Repository metadata**
  - Updated repository URL in `config.json`
  - Updated version to `1.0.2`

### Notes

This release focuses on **configuration cleanup**, **legacy code removal**, and **overall stability improvements**.

No breaking changes for existing Home Assistant installations.

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
