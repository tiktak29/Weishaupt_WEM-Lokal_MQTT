# Weishaupt WEM-Lokal MQTT

Local gateway for Weishaupt heat pumps with automatic WebIF and device detection, exposing local data to Home Assistant via MQTT Discovery – completely local and cloud-free.

Version **1.1.3** fixes MQTT Discovery for the compressor sensor (`Verdichter`) when the WebIF reports values in `Hz`. If the initial value is `Aus`, the unit is corrected once a numeric `Hz` or `rpm` reading becomes available. The existing Home Assistant entity ID is preserved, and the sensor displays the correct unit (`Hz` or `rpm`).

The raw `Verdichter` value in the MQTT state topic `wem/wem_lokal_info` is now a numeric string without the `Hz` or `rpm` suffix. WebIF communication, polling, session handling, recovery and app configuration remain unchanged. A normal Home Assistant app update requires no reinstallation.

🔗 **You can find more information here:**  
[Weishaupt WEM-Lokal MQTT on GitHub](https://github.com/tiktak29/Weishaupt_WEM-Lokal_MQTT)
