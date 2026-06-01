import asyncio
import aiohttp
from aiohttp import ClientConnectorError
from bs4 import BeautifulSoup
from urllib.parse import urlencode
import json
import time
import paho.mqtt.client as mqtt
import unicodedata
import logging

# ---------------------------
# GLOBALS
# ---------------------------

login_start_time = time.time()
app_start_time = time.time()

# Discovery control
discovery_enabled = True
device_ready = {}

def all_devices_ready():
    return all(device_ready.values())

def log_missing_devices():
    missing = [name for name, ready in device_ready.items() if not ready]
    if missing:
        logger.info(f" Waiting for first data from: {', '.join(missing)}")

def log_device_ready(name):
    logger.info(f"✅ {name} ready – first data successfully received")

def log_summary_after_discovery(data_store):
    logger.info("📋 Summary of all devices:")

    wp_model = (
        data_store.get("Wärmepumpe", {})
        .get("Außengerät Variante", "")
        .strip()
    )

    if wp_model:
        logger.info(f"⚙️ Detected model: {wp_model}")

    for name, ready in device_ready.items():
        status = "✅ Data received" if ready else "⏳ No data"
        logger.info(f"{status}  – {name}")

# ---------------------------
# CONFIGURATION
# ---------------------------

with open("/data/options.json") as f:
    config = json.load(f)

IP = config.get("webinterface_ip_address", "").strip()
USERNAME = config.get("webinterface_username", "").strip()
PASSWORD = config.get("webinterface_password", "").strip()
HEX = config.get("webinterface_hex_code", "").strip()

ENABLE_WP = config.get("enable_wp", True)
ENABLE_HK1 = config.get("enable_hk1", True)
ENABLE_HK2 = config.get("enable_hk2", True)
ENABLE_STATS = config.get("enable_stats", True)
ENABLE_WEZ2 = config.get("enable_wez2", True)

if not IP:
    raise ValueError("webinterface_ip_address missing")
if not USERNAME:
    raise ValueError("webinterface_username missing")
if not PASSWORD:
    raise ValueError("webinterface_password missing")
if not HEX:
    raise ValueError("webinterface_hex_code missing")

BASE_URL = f"http://{IP}"

def build_urls(hex_code):
    return {
        "Wärmepumpe": f"/settings_export.html?stack=0C00000100000000008000{hex_code}010002000301,0C000C2200000000000000{hex_code}020003000401",
        "Heizkreis 1": f"/settings_export.html?stack=0C00000100000000008000{hex_code}010002000301,0C000C1900000000000000{hex_code}020003000401",
        "Heizkreis 2": f"/settings_export.html?stack=0C00000100000000008000{hex_code}010002000301,0C000C1A00000000000000{hex_code}020003000401",
        "Statistik":   f"/settings_export.html?stack=0C00000100000000008000{hex_code}010002000301,0C000C2700000000000000{hex_code}020003000401",
        "2. WEZ":      f"/settings_export.html?stack=0C00000100000000008000{hex_code}010002000301,0C000C2300000000000000{hex_code}020003000401"
    }

all_urls = build_urls(HEX)

URLS = {}
if ENABLE_WP: URLS["Wärmepumpe"] = all_urls["Wärmepumpe"]
if ENABLE_HK1: URLS["Heizkreis 1"] = all_urls["Heizkreis 1"]
if ENABLE_HK2: URLS["Heizkreis 2"] = all_urls["Heizkreis 2"]
if ENABLE_STATS: URLS["Statistik"] = all_urls["Statistik"]
if ENABLE_WEZ2: URLS["2. WEZ"] = all_urls["2. WEZ"]

if not URLS:
    raise ValueError("At least one device must be enabled.")

device_ready = {name: False for name in URLS.keys()}

BASE_SEQUENCE = [
    "Wärmepumpe", "Heizkreis 1", "Wärmepumpe", "Heizkreis 2",
    "Wärmepumpe", "Statistik", "Wärmepumpe", "Heizkreis 1",
    "Wärmepumpe", "Heizkreis 2", "Wärmepumpe", "Statistik",
    "Wärmepumpe", "2. WEZ",
]

SEQUENCE = [device for device in BASE_SEQUENCE if device in URLS]

# ---------------------------
# STATISTICS
# ---------------------------

stats = {
    name: {"total": 0, "first_success": 0, "retry_success": 0, "failed": 0}
    for name in URLS.keys()
}

raw_pause = config.get("polling_seconds", 10)
try:
    PAUSE_SECONDS = int(raw_pause)
except (ValueError, TypeError):
    PAUSE_SECONDS = 10

PAUSE_SECONDS = max(10, min(PAUSE_SECONDS, 300))

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/index.html",
    "Content-Type": "application/x-www-form-urlencoded",
}
# ---------------------------
# MQTT
# ---------------------------

MQTT_BROKER = config.get("mqtt_broker", "core-mosquitto").strip()
try:
    MQTT_PORT = int(config.get("mqtt_port", 1883))
except (ValueError, TypeError):
    MQTT_PORT = 1883

MQTT_USER = config.get("mqtt_username", "").strip()
MQTT_PASS = config.get("mqtt_password", "")
MQTT_BASE = "homeassistant"
MQTT_STATE_TOPIC = "wem/wem_lokal_info"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("wem_mqtt_unified")

# ---------------------------
# HYBRID CALLBACKS (API v1 + API v2)
# ---------------------------

def on_connect(client, userdata, flags, rc, properties=None):
    """
    Hybrid-Callback:
    - API v1: on_connect(client, userdata, flags, rc)
    - API v2: on_connect(client, userdata, flags, reason_code, properties)
    """
    if rc == 0:
        logger.info(" MQTT connected")
        client.publish(
            MQTT_STATE_TOPIC,
            json.dumps({"status": "connected"}),
            retain=True
        )
    else:
        logger.error(f"❌  MQTT connection failed (rc={rc})")

def on_disconnect(client, userdata, rc, properties=None):
    if rc == 0:
        logger.info("🔌 MQTT disconnected cleanly")
    else:
        logger.warning(f"⚠️ MQTT disconnected unexpectedly (rc={rc})")

# ---------------------------
# MQTT CLIENT (without forcing API version)
# ---------------------------

# MQTT-Client mit Fallback für alte paho-mqtt Versionen
try:
    mqtt_client = mqtt.Client(
        protocol=mqtt.MQTTv311,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )
except TypeError:
    # Fallback für alte paho-mqtt Versionen ohne callback_api_version
    mqtt_client = mqtt.Client(protocol=mqtt.MQTTv311)

if MQTT_USER:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
except Exception as e:
    logger.error("❌ MQTT connection failed – unable to connect to MQTT broker")
    logger.error(f"🔧 Technical info: {e}")
    logger.error("🛑 App has been stopped.")
    while True:
        time.sleep(3600)

mqtt_client.loop_start()

# Kurzer Verbindungscheck
mqtt_login_ok = False
mqtt_check_start = time.time()

while time.time() - mqtt_check_start < 3:
    if mqtt_client.is_connected():
        mqtt_login_ok = True
        break
    time.sleep(0.1)

if not mqtt_login_ok:
    logger.error("❌ MQTT login failed – broker rejected authentication")
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    while True:
        time.sleep(3600)

def mqtt_publish(topic, payload, retain=True):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False)
    mqtt_client.publish(topic, payload, retain=retain)

# ---------------------------
# HILFSFUNKTIONEN
# ---------------------------

def normalize_id(text):
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return (
        text.lower()
        .replace(" ", "_")
        .replace(".", "")
        .replace("/", "_")
    )
# ---------------------------
# WP-SIGNATUR
# ---------------------------

WP_SIGNATURE = ["Verdichter", "Hochdruck", "Niederdruck"]

def is_wrong_section(section, values):
    if section == "Wärmepumpe":
        return False
    for key in values.keys():
        for sig in WP_SIGNATURE:
            if sig.lower() in key.lower():
                return True
    return False

# ---------------------------
# ROBUST HTTP WRAPPING
# ---------------------------

async def safe_request(session, method, url, **kwargs):
    try:
        async with session.request(method, url, **kwargs) as resp:
            text = await resp.text()
            return resp, text

    except ClientConnectorError:
        if url != "/index.html":
            logger.error(f"❌ Connection error to WEM-Local ({url})")
        return None, None

    except asyncio.TimeoutError:
        if url != "/index.html":
            logger.error(f"❌ Timeout during request to WEM-Local ({url})")
        return None, None

    except Exception as e:
        if url != "/index.html":
            logger.error(f"❌ Unexpected error during request ({url}): {e}")
        return None, None

# ---------------------------
# LOGIN
# ---------------------------

async def login(session):
    global login_start_time
    login_start_time = time.time()

    resp, _ = await safe_request(session, "GET", "/index.html")
    if resp is None:
        logger.warning("⚠️ /index.html not reachable – login aborted")
        return False

    payload = urlencode({"user": USERNAME, "pass": PASSWORD})

    resp, _ = await safe_request(
        session,
        "POST",
        "/login.html",
        data=payload,
        headers=HEADERS,
        allow_redirects=False,
    )

    if resp is None:
        logger.warning("⚠️ Login request failed (no response)")
        return False

    return resp.status == 303

# ---------------------------
# PARSER
# ---------------------------

def extract_values(html):
    soup = BeautifulSoup(html, "html.parser")
    values = {}

    for item in soup.find_all("div", class_="nav-link browseobj"):
        h5 = item.find("h5")
        if not h5:
            continue
        name = h5.text.strip()
        raw = item.get_text(separator=" ", strip=True)
        value = raw.replace(name, "", 1).strip()
        values[name] = value

    for row in soup.find_all("div", class_="browseobj"):
        h5 = row.find("h5")
        if not h5:
            continue
        name = h5.text.strip()
        raw = row.get_text(separator=" ", strip=True)
        value = raw.replace(name, "", 1).strip()
        values[name] = value

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) == 2:
            name = tds[0].get_text(strip=True)
            value = tds[1].get_text(strip=True)
            values[name] = value

    if "Wärmetauscher AG Austrit" in values:
        values["Wärmetauscher AG Austritt"] = values.pop("Wärmetauscher AG Austrit")

    if "Expansionsventil AG Eintr" in values:
        values["Expansionsventil AG Eintritt"] = values.pop("Expansionsventil AG Eintr")

    if "Verdichtersauggastemp." in values:
        values["Verdichtersauggastemperatur"] = values.pop("Verdichtersauggastemp.")

    return values

# ---------------------------
# FETCH
# ---------------------------

async def fetch(session, name, url):

    stats[name]["total"] += 1

    resp, html = await safe_request(session, "GET", url, headers=HEADERS)

    if resp is None or html is None:
        logger.warning(f"⚠️ No response from WEM‑Local ({name})")
        stats[name]["failed"] += 1
        return None

    if "form-signin" in html.lower() or "bitte anmelden" in html.lower():
        if time.time() - login_start_time > 300:
            logger.error("❌ Login failed – incorrect username or password")
        stats[name]["failed"] += 1
        return None

    values = extract_values(html)

    if not values:
        await asyncio.sleep(2)
        resp2, html2 = await safe_request(session, "GET", url, headers=HEADERS)

        if resp2 and html2:
            retry_values = extract_values(html2)

            if retry_values:
                if is_wrong_section(name, retry_values):
                    stats[name]["failed"] += 1
                    return {}

                stats[name]["retry_success"] += 1
                return retry_values

        stats[name]["failed"] += 1
        return {}

    if is_wrong_section(name, values):
        stats[name]["failed"] += 1
        return {}

    stats[name]["first_success"] += 1
    return values

# ---------------------------
# MQTT DISCOVERY
# ---------------------------

def publish_discovery(data_store):

    main_device_id = "wem_lokal_info"

    main_device_payload = {
        "name": "Systemstatus",
        "uniq_id": "wem_lokal_info_status",
        "state_topic": MQTT_STATE_TOPIC,
        "value_template": "online",
        "icon": "mdi:heat-pump",
        "device": {
            "identifiers": [main_device_id],
            "name": "WEM-Lokal Info",
            "manufacturer": "Weishaupt",
            "model": "Web-Interface → MQTT"
        }
    }

    mqtt_publish(
        f"{MQTT_BASE}/sensor/wem_lokal_info_status/config",
        main_device_payload,
        retain=True
    )

    if not any(values for values in data_store.values() if values):
        return

    device_map = {
        "Wärmepumpe": ("wem_lokal_wp", "WEM-Lokal Wärmepumpe"),
        "Heizkreis 1": ("wem_lokal_hk1", "WEM-Lokal Heizkreis 1"),
        "Heizkreis 2": ("wem_lokal_hk2", "WEM-Lokal Heizkreis 2"),
        "2. WEZ": ("wem_lokal_wez2", "WEM-Lokal 2. WEZ"),
        "Statistik": ("wem_lokal_stats", "WEM-Lokal Statistik"),
    }

    wp_model = (
        data_store.get("Wärmepumpe", {})
        .get("Außengerät Variante", "")
        .strip()
    )

    if not wp_model:
        wp_model = "WEM Portal Lokal"

    for section, values in data_store.items():
        if not values or section not in device_map:
            continue

        dev_id, dev_name = device_map[section]

        for key, value in values.items():

            key_lower = key.lower()
            v = str(value).strip()
            v_lower = v.lower()

            sensor_id = f"{dev_id}_{normalize_id(key)}"
            template_key = key.replace("'", "\\'")

            value_template = (
                f"{{{{ value_json['WEM-Lokal Info']['{section}']['{template_key}'] }}}}"
            )

            payload = {
                "name": key,
                "uniq_id": sensor_id,
                "state_topic": MQTT_STATE_TOPIC,
                "value_template": value_template,
                "device": {
                    "identifiers": [dev_id],
                    "name": dev_name,
                    "via_device": main_device_id,
                    "manufacturer": "Weishaupt",
                    "model": wp_model
                }
            }

            # ---------------------------
            # Special cases
            # ---------------------------

            if "leistungsanforderung" in key_lower:
                payload["unit_of_measurement"] = "%"
                payload["state_class"] = "measurement"
                payload["value_template"] = value_template.replace("}}", " | replace(' %','') }}")

            elif "at langzeitwert" in key_lower or "at mittelwert" in key_lower:
                payload["unit_of_measurement"] = "°C"
                payload["device_class"] = "temperature"
                payload["state_class"] = "measurement"
                payload["value_template"] = value_template.replace(
                    "}}", " | replace(' °C','') | replace(' K','') }}"
                )

            elif key_lower == "drehzahl pumpe m1":
                payload["unit_of_measurement"] = "%"
                payload["state_class"] = "measurement"
                if v_lower in ("aus", "0"):
                    payload["value_template"] = "0"
                else:
                    payload["value_template"] = value_template.replace("}}", " | replace(' %','') }}")

            elif "status" in key_lower and section == "2. WEZ":
                payload["value_template"] = (
                    "{% set v = " + value_template.replace("{{", "").replace("}}", "") + " | int %}"
                    "{{ 'Ein' if v == 1 else 'Aus' }}"
                )

            elif key_lower == "pumpe" and section == "Heizkreis 2":
                payload["value_template"] = (
                    "{% set v = " + value_template.replace("{{", "").replace("}}", "") + " | int %}"
                    "{{ 'Ein' if v == 1 else 'Aus' }}"
                )

            elif "°c" in v_lower or v.endswith(" K"):
                payload["unit_of_measurement"] = "°C"
                payload["device_class"] = "temperature"
                payload["state_class"] = "measurement"
                payload["value_template"] = value_template.replace(
                    "}}", " | replace(' °C','') | replace(' K','') }}"
                )

            elif "energie" in key_lower or "kwh" in v_lower:
                payload["unit_of_measurement"] = "kWh"
                payload["device_class"] = "energy"
                payload["state_class"] = "total_increasing"
                payload["value_template"] = value_template.replace(
                    "}}", " | replace(' KWh','') | replace(' kWh','') | replace('h','') }}"
                )

            elif "leistung" in key_lower or " kw" in v_lower:
                payload["unit_of_measurement"] = "kW"
                payload["device_class"] = "power"
                payload["state_class"] = "measurement"
                payload["value_template"] = value_template.replace(
                    "}}", " | replace(' KW','') | replace(' kW','') }}"
                )

            elif " bar" in v_lower:
                payload["unit_of_measurement"] = "bar"
                payload["device_class"] = "pressure"
                payload["state_class"] = "measurement"
                payload["value_template"] = value_template.replace(
                    "}}", " | replace(' bar','') | replace(' BAR','') }}"
                )

            elif "m3/h" in v_lower or "m³/h" in v_lower:
                payload["unit_of_measurement"] = "m³/h"
                payload["device_class"] = "volume_flow_rate"
                payload["state_class"] = "measurement"
                payload["value_template"] = value_template.replace(
                    "}}", " | replace('m3/h','') | replace('m³/h','') }}"
                )

            elif " h" in v_lower and section != "Statistik":
                payload["unit_of_measurement"] = "h"
                payload["state_class"] = "total_increasing"
                payload["value_template"] = value_template.replace("}}", " | replace(' h','') }}")

            elif "schaltspiele" in key_lower:
                payload["state_class"] = "total_increasing"

            elif "rpm" in v_lower:
                payload["unit_of_measurement"] = "rpm"
                payload["state_class"] = "measurement"
                payload["value_template"] = value_template.replace("}}", " | replace(' rpm','') }}")

            elif "%" in v:
                payload["unit_of_measurement"] = "%"
                payload["state_class"] = "measurement"
                payload["value_template"] = value_template.replace("}}", " | replace(' %','') }}")

            elif v.isdigit():
                payload["state_class"] = "total_increasing"

            disc_topic = f"{MQTT_BASE}/sensor/{sensor_id}/config"
            mqtt_publish(disc_topic, payload, retain=True)

# ---------------------------
# STATISTICS OUTPUT
# ---------------------------

def output_statistics():

    order = ["Wärmepumpe", "Heizkreis 1", "Heizkreis 2", "Statistik", "2. WEZ"]

    system_total = 0
    system_first = 0
    system_retry = 0
    system_failed = 0

    for device in order:

        if device not in stats:
            continue

        s = stats[device]
        total = s["total"]

        if total == 0:
            continue

        first = s["first_success"]
        retry = s["retry_success"]
        failed = s["failed"]

        first_pct = (first / total) * 100
        retry_pct = (retry / total) * 100
        failed_pct = (failed / total) * 100
        total_pct = ((first + retry) / total) * 100

        logger.info(f"{device}:")
        logger.info(f"  First request successful: {first_pct:.1f} % ({first})")
        logger.info(f"  Retry successful:      {retry_pct:.1f} % ({retry})")
        logger.info(f"  Final failures: {failed_pct:.1f} % ({failed})")
        logger.info(f"  Total successful:    {total_pct:.1f} % ({first + retry} of {total})")

        system_total += total
        system_first += first
        system_retry += retry
        system_failed += failed

    if system_total > 0:

        system_first_pct = (system_first / system_total) * 100
        system_retry_pct = (system_retry / system_total) * 100
        system_failed_pct = (system_failed / system_total) * 100
        system_total_pct = ((system_first + system_retry) / system_total) * 100

        logger.info("SYSTEM TOTAL:")
        logger.info(f"  First request successful: {system_first_pct:.1f} % ({system_first})")
        logger.info(f"  Retry successful:      {system_retry_pct:.1f} % ({system_retry})")
        logger.info(f"  Final failures: {system_failed_pct:.1f} % ({system_failed})")
        logger.info(f"  Total successful:    {system_total_pct:.1f} % ({system_first + system_retry} of {system_total})")

    for device in stats:
        stats[device] = {
            "total": 0,
            "first_success": 0,
            "retry_success": 0,
            "failed": 0,
        }

# ---------------------------
# MAIN LOOP (ROUND-ROBIN)
# ---------------------------

async def main():

    global discovery_enabled

    jar = aiohttp.CookieJar(unsafe=True)

    async with aiohttp.ClientSession(
        base_url=BASE_URL,
        cookie_jar=jar
    ) as session:

        data_store = {key: {} for key in URLS.keys()}

        logger.info("📡 Discovery is active until all devices have delivered data at least once...")
        log_missing_devices()

        logger.info("⏳ Background login in progress (full stabilization may take up to 5 minutes)")

        resp, _ = await safe_request(session, "GET", "/index.html")

        if resp is None:
            logger.error("❌ Web interface not reachable")
            logger.error(f"🔧 Technical info: IP {IP} could not be contacted")
            logger.error("🛑 App has been stopped.")
            while True:
                await asyncio.sleep(3600)

        while True:

            if not await login(session):
                logger.warning("⚠️ Login failed – retrying in 10s")
                await asyncio.sleep(10)
                continue

            await asyncio.sleep(5.0)

            while True:

                session_broken = False

                for name in SEQUENCE:

                    if name not in URLS:
                        continue

                    values = await fetch(session, name, URLS[name])

                    if values is None:
                        session_broken = True
                        break

                    if values:
                        data_store[name] = values

                        if not device_ready[name]:
                            device_ready[name] = True
                            log_device_ready(name)
                            log_missing_devices()

                    clean_data_store = {}

                    for section, vals in data_store.items():

                        clean_vals = {}

                        for key, value in vals.items():

                            v = str(value).strip()

                            if v.lower() == "aus":
                                clean_vals[key] = "0"

                            elif v.lower() == "ein":
                                clean_vals[key] = "1"

                            else:
                                v = v.replace(" KW", "")
                                v = v.replace(" kW", "")
                                v = v.replace(",", ".")
                                clean_vals[key] = v

                        clean_data_store[section] = clean_vals

                    mqtt_publish(
                        MQTT_STATE_TOPIC,
                        {"WEM-Lokal Info": clean_data_store}
                    )

                    if discovery_enabled:
                        publish_discovery(data_store)

                        if all_devices_ready():
                            logger.info(" All devices have delivered data – discovery will be disabled")
                            log_summary_after_discovery(data_store)
                            logger.info("🕒 Daily statistics of data polling will be generated at 00:00")
                            discovery_enabled = False

                    weekday_short = {
                        0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu",
                        4: "Fri", 5: "Sat", 6: "Sun"
                    }

                    now = time.localtime()

                    if now.tm_hour == 0 and now.tm_min == 0:

                        stats_day = time.localtime(time.time() - 86400)

                        weekday_str = weekday_short[stats_day.tm_wday]
                        date_str = time.strftime("%Y-%m-%d", stats_day)

                        logger.info(f"🕒 Creating daily statistics for {weekday_str}, {date_str}")

                        output_statistics()

                        logger.info(f"🕒 Daily statistics for {weekday_str}, {date_str} has been created")

                        await asyncio.sleep(60)

                    await asyncio.sleep(PAUSE_SECONDS)

                if session_broken:
                    break

# ---------------------------
# START
# ---------------------------

if __name__ == "__main__":
    asyncio.run(main())

