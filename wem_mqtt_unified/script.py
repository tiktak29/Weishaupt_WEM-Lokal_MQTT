import asyncio
import aiohttp
from aiohttp import ClientConnectorError
from bs4 import BeautifulSoup
from urllib.parse import urlencode
import json
import time
import sys
import paho.mqtt.client as mqtt
import unicodedata
import logging
from datetime import datetime, timezone
initial_sync_done = False
INITIAL_SYNC_ORDER = [
    "Heizkreis 1",
    "Heizkreis 2",
    "Statistik",
    "2. WEZ"
]

INITIAL_SYNC_RETRIES = 2
INITIAL_SYNC_RETRY_DELAY = 2
login_fail_count = 0

# ---------------------------
# GLOBALS
# ---------------------------

# Discovery control
discovery_enabled = True
device_ready = {}
last_stats_day = time.localtime().tm_yday

def all_devices_ready():
    return all(device_ready.values())

def log_device_ready(name):
    logger.info(f"✅ {name:<12} → Initial data received")

def log_summary_after_discovery(data_store):
    logger.info("📋 Summary of all devices:")

    wp_model = (
        data_store.get("Wärmepumpe", {})
        .get("Außengerät Variante", "")
        .strip()
    )

    if wp_model:
        logger.info(f"⚙️ Weishaupt {wp_model}")

    for name, ready in device_ready.items():
        if ready:
            logger.info(f"🟢 {name}")
        else:
            logger.info(f"🔴 {name}")

# ---------------------------
# CONFIGURATION
# ---------------------------

with open("/data/options.json") as f:
    config = json.load(f)

IP = config.get("webinterface_ip_address", "").strip()
USERNAME = config.get("webinterface_username", "").strip()
PASSWORD = config.get("webinterface_password", "").strip()
HEX = config.get("webinterface_hex_code", "").strip()

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
    info = f"0C00000100000000008000{hex_code}010002000301"

    stacks = {
    
        "Wärmepumpe": f"0C000C2200000000000000{hex_code}020003000401",
        "Heizkreis 1": f"0C000C1900000000000000{hex_code}020003000401",
        "Heizkreis 2": f"0C000C1A00000000000000{hex_code}020003000401",
        "Statistik":   f"0C000C2700000000000000{hex_code}020003000401",
        "2. WEZ":      f"0C000C2300000000000000{hex_code}020003000401",
    }

    return {
        name: f"/settings_export.html?stack={info},{stack}"
        for name, stack in stacks.items()
    }

all_urls = build_urls(HEX)

URLS = {
    "Wärmepumpe": all_urls["Wärmepumpe"] 
}
if ENABLE_HK1: URLS["Heizkreis 1"] = all_urls["Heizkreis 1"]
if ENABLE_HK2: URLS["Heizkreis 2"] = all_urls["Heizkreis 2"]
if ENABLE_STATS: URLS["Statistik"] = all_urls["Statistik"]
if ENABLE_WEZ2: URLS["2. WEZ"] = all_urls["2. WEZ"]

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
AVAILABILITY_TOPIC = "wem/availability"
LAST_UPDATE_TOPIC = "wem/last_update"
SYSTEM_STATUS_TOPIC = "wem/system_status"
DAILY_SUCCESS_TOPIC = "wem/daily_success"
DAILY_SUCCESS_ATTR_TOPIC = "wem/daily_success_attributes"
OFFLINE_TIMEOUT = 300
STATS_FILE = "/data/daily_success.json"

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
        logger.info("✔️ MQTT connected")
        client.publish(AVAILABILITY_TOPIC, "online", qos=1, retain=True)
    else:
        logger.error(f"❌  MQTT connection failed (rc={rc})")

def on_disconnect(client, userdata, rc, properties=None):
    if rc == 0:
        logger.info("🔌 MQTT disconnected cleanly")
    else:
        logger.warning(f"⚠️ MQTT disconnected unexpectedly (rc={rc})")

# ---------------------------
# MQTT client with fallback for older paho-mqtt versions
# ---------------------------

try:
    mqtt_client = mqtt.Client(
        protocol=mqtt.MQTTv311,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )
except TypeError:
    # Fallback for older paho-mqtt versions without callback_api_version
    mqtt_client = mqtt.Client(protocol=mqtt.MQTTv311)

if MQTT_USER:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

mqtt_client.will_set(
    AVAILABILITY_TOPIC,
    payload="offline",
    qos=1,
    retain=True
)

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
except Exception as e:
    logger.error("❌ MQTT connection failed – unable to connect to MQTT broker")
    logger.error(f"🔧 Technical info: {e}")
    logger.error("🔍 Please check:")
    logger.error("   • MQTT broker address")
    logger.error("   • MQTT port")
    logger.error("   • Whether the MQTT broker is running")
    logger.error("   • MQTT username and password")
    logger.error("🛑 App is shutting down")
    
    mqtt_client.loop_stop()
    sys.exit(1)

mqtt_client.loop_start()

# Connection check
mqtt_login_ok = False
mqtt_check_start = time.time()

while time.time() - mqtt_check_start < 3:
    if mqtt_client.is_connected():
        mqtt_login_ok = True
        break
    time.sleep(0.1)

if not mqtt_login_ok:
    logger.error("❌ MQTT login failed – broker rejected authentication")
    logger.error("🔧 Authentication was rejected by the MQTT broker")
    logger.error("🔍 Please check:")
    logger.error("   • MQTT username")
    logger.error("   • MQTT password")
    logger.error("   • MQTT access rights")
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    logger.error("🛑 App is shutting down.")
    sys.exit(1)

def mqtt_publish(topic, payload, retain=True):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False)
    mqtt_client.publish(topic, payload, retain=retain)


def load_last_daily_stats():
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_daily_stats(data):
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(
            f"⚠️ Unable to save daily statistics: {e}"
        )

# ---------------------------
# HELPER FUNCTIONS
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
# CLEAN DATA BUILDER
# ---------------------------

def build_clean_data(data_store):
    clean = {}

    for section, vals in data_store.items():
        clean_vals = {}

        for k, v in vals.items():
            value = str(v).strip()

            # Ein/Aus → 1/0
            if value.lower() == "aus":
                clean_vals[k] = "0"

            elif value.lower() == "ein":
                clean_vals[k] = "1"

            else:
                # Allgemeine Bereinigung
                clean_vals[k] = (
                    value
                    .replace(" KW", "")
                    .replace(" kW", "")
                    .replace(",", ".")
                )

        clean[section] = clean_vals

    return clean

# ---------------------------
# HEAT PUMP SIGNATURE
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
        logger.warning(
            "⚠️ Login request failed (no response from web interface)"
        )
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
        stats[name]["failed"] += 1
        return None

    values = extract_values(html)

    if not values:
        await asyncio.sleep(1.5)
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

    wp_model = (
        data_store.get("Wärmepumpe", {})
        .get("Außengerät Variante", "")
        .strip()
    )

    if not wp_model:
        wp_model = "WEM Portal Lokal"

    main_device_payload = {
        "name": "Systemstatus",
        "uniq_id": "wem_lokal_info_status",
        "state_topic": SYSTEM_STATUS_TOPIC,
        "value_template": "{{ value }}",
        "icon": "mdi:heat-pump",
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "identifiers": [main_device_id],
            "name": "WEM-Lokal Info",
            "manufacturer": "Weishaupt",
            "model": wp_model
        }       
    }

    mqtt_publish(
        f"{MQTT_BASE}/sensor/wem_lokal_info_status/config",
        main_device_payload,
        retain=True
    )

    last_update_payload = {
        "name": "Update Sensoren",
        "uniq_id": "wem_lokal_last_update",

        "state_topic": LAST_UPDATE_TOPIC,

        "device_class": "timestamp",

        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",

        "device": {
            "identifiers": [main_device_id],
            "name": "WEM-Lokal Info",
            "manufacturer": "Weishaupt",
            "model": wp_model
        }
    }

    mqtt_publish(
        f"{MQTT_BASE}/sensor/wem_lokal_last_update/config",
        last_update_payload,
        retain=True
    )

    success_payload = {
        "name": "Erfolgsquote (Gestern)",
        "uniq_id": "wem_daily_success",

        "state_topic": DAILY_SUCCESS_TOPIC,

        "json_attributes_topic": DAILY_SUCCESS_ATTR_TOPIC,

        "unit_of_measurement": "%",
        "icon": "mdi:chart-line",

        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",

        "device": {
            "identifiers": [main_device_id],
            "name": "WEM-Lokal Info",
            "manufacturer": "Weishaupt",
            "model": wp_model
        }
    }

    mqtt_publish(
        f"{MQTT_BASE}/sensor/wem_daily_success/config",
        success_payload,
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

                "availability_topic": AVAILABILITY_TOPIC,
                "payload_available": "online",
                "payload_not_available": "offline",

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
                payload["value_template"] = value_template.replace("}}", " | replace(' %','') }}")

            elif "status" in key_lower and section == "2. WEZ":
                payload["value_template"] = (
                    "{% set v = " + value_template.replace("{{", "").replace("}}", "") + " | int %}"
                    "{{ 'Ein' if v == 1 else 'Aus' }}"
                )

            elif key_lower == "2. wez" and section == "2. WEZ":
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

        logger.info(f" {device}:")
        logger.info(f"  First-pass success rate: {first_pct:5.1f} % ({first})")
        logger.info(f"  Retry-pass success rate: {retry_pct:5.1f} % ({retry})")
        logger.info(f"  Overall failure rate:    {failed_pct:5.1f} % ({failed})")
        logger.info(f"  Overall success rate:    {total_pct:5.1f} % ({first + retry}/{total})")

        system_total += total
        system_first += first
        system_retry += retry
        system_failed += failed

    if system_total > 0:

        system_first_pct = (system_first / system_total) * 100
        system_retry_pct = (system_retry / system_total) * 100
        system_failed_pct = (system_failed / system_total) * 100
        system_total_pct = ((system_first + system_retry) / system_total) * 100

        logger.info(" Overall system:")
        logger.info(f"  First-pass success rate: {system_first_pct:5.1f} % ({system_first})")
        logger.info(f"  Retry-pass success rate: {system_retry_pct:5.1f} % ({system_retry})")
        logger.info(f"  Overall failure rate:    {system_failed_pct:5.1f} % ({system_failed})")
        logger.info(f"  Overall success rate:    {system_total_pct:5.1f} % ({system_first + system_retry}/{system_total})")

        stats_day = time.localtime(time.time() - 86400)

        date_string = (
            f"{stats_day.tm_mday:02d}."
            f"{stats_day.tm_mon:02d}."
            f"{stats_day.tm_year}"
        )

        mqtt_publish(
            DAILY_SUCCESS_TOPIC,
            round(system_total_pct, 1)
        )

        mqtt_publish(
            DAILY_SUCCESS_ATTR_TOPIC,
            {
                "Datum": date_string,
                "Abfragen": system_total,
                "Erfolgreich": system_first + system_retry,
                "Fehlgeschlagen": system_failed
            }
        )

        save_daily_stats(
            {
                "success": round(system_total_pct, 1),
                "attributes": {
                    "Datum": date_string,
                    "Abfragen": system_total,
                    "Erfolgreich": system_first + system_retry,
                    "Fehlgeschlagen": system_failed
                }
            }
        )

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
    global initial_sync_done
    global login_fail_count
    global last_stats_day

    jar = aiohttp.CookieJar(unsafe=True)

    async with aiohttp.ClientSession(
        base_url=BASE_URL,
        cookie_jar=jar
    ) as session:

        data_store = {key: {} for key in URLS.keys()}
        last_success = time.time()
        last_status = "offline"

        stored_stats = load_last_daily_stats()

        if stored_stats:

            mqtt_publish(
                DAILY_SUCCESS_TOPIC,
                stored_stats["success"]
            )

            mqtt_publish(
                DAILY_SUCCESS_ATTR_TOPIC,
                stored_stats["attributes"]
            )

        else:

            mqtt_publish(
                DAILY_SUCCESS_TOPIC,
                0
            )

            mqtt_publish(
                DAILY_SUCCESS_ATTR_TOPIC,
                {
                    "Datum": "Noch nicht verfügbar",
                    "Abfragen": 0,
                    "Erfolgreich": 0,
                    "Fehlgeschlagen": 0
                }
            )

        logger.info("📡 Discovery active until all devices provide initial data")

        logger.info("ℹ️ Initial connection in progress (may take up to 5 minutes)")

        resp, _ = await safe_request(session, "GET", "/index.html")

        if resp is None:
            logger.error("❌ Web interface not reachable – entering recovery wait mode (15 minutes)")
            logger.error(f"🔧 Technical info: IP {IP} could not be contacted")
            logger.error("🔍 Please check:")
            logger.error("   • Web interface IP address")
            logger.error("   • Whether the web interface is enabled")
            logger.error("   • Network connectivity")
            logger.error("   • Firewall / VLAN settings")
            logger.error("⏳ Waiting 15 minutes before restart")
            
            await asyncio.sleep(900)
            logger.error("🛑 Recovery timeout expired – restarting application")
            sys.exit(1)

        while True:

            if not await login(session):

                login_fail_count += 1

                logger.warning(
                    f"⚠️ Login failed ({login_fail_count} consecutive attempts) – retrying in 10s"
                )

                if login_fail_count == 5:
                    logger.error("❌ Login has failed 5 consecutive times")
                    logger.error("🔍 Please check:")
                    logger.error("   • Username")
                    logger.error("   • Password")
                    logger.error("   • HEX code")
                    logger.error("   • Web interface settings")

                await asyncio.sleep(10)
                continue

            login_fail_count = 0

            await asyncio.sleep(5.0)

            session_broken = False
            
            # ---------------------------
            # WP-FAST-CHECK (Heat pump fast check until first valid data)
            # ---------------------------

            if not initial_sync_done:

                while True:
                    values = await fetch(session, "Wärmepumpe", URLS["Wärmepumpe"])

                    if values is None:
                        session_broken = True
                        break

                    if values:
                        data_store["Wärmepumpe"] = values
                        device_ready["Wärmepumpe"] = True
                        log_device_ready("Wärmepumpe")

                        mqtt_publish(
                            LAST_UPDATE_TOPIC,
                            datetime.fromtimestamp(time.time(), timezone.utc).isoformat()
                        )

                        mqtt_publish(
                            MQTT_STATE_TOPIC,
                            {"WEM-Lokal Info": build_clean_data(data_store)}
                        )

                        break  # Heat pump delivered data → fast-check completed

                    await asyncio.sleep(3)  # Fast-check interval

                if session_broken:
                    continue
                    
            # ---------------------------
            # INITIAL SYNC (optimized)
            # ---------------------------

            if not initial_sync_done:

                for dev in INITIAL_SYNC_ORDER:

                    if dev not in URLS:
                        continue

                    values = {}

                    for attempt in range(INITIAL_SYNC_RETRIES + 1):

                        values = await fetch(session, dev, URLS[dev])

                        if values is None:
                            session_broken = True
                            break

                        if values:
                            break

                        if attempt < INITIAL_SYNC_RETRIES:
                            logger.info(
                                f"🔄 Initial sync retry {attempt + 1}/{INITIAL_SYNC_RETRIES} ({dev})"
                            )
                            await asyncio.sleep(INITIAL_SYNC_RETRY_DELAY)

                    if session_broken:
                        break

                    if values:
                        data_store[dev] = values
                        device_ready[dev] = True
                        log_device_ready(dev)

                        mqtt_publish(
                            LAST_UPDATE_TOPIC,
                            datetime.fromtimestamp(
                                time.time(),
                                timezone.utc
                            ).isoformat()
                        )

                        mqtt_publish(
                            MQTT_STATE_TOPIC,
                            {"WEM-Lokal Info": build_clean_data(data_store)}
                        )

                    else:
                        logger.warning(
                            f"⚠️ Initial sync failed for {dev}"
                        )

                    # Retry polling during initial synchronization
                    await asyncio.sleep(2)

                if not session_broken:
                    initial_sync_done = True
                    logger.info(
                        "🔄 Initial sync completed – switching to Round Robin polling"
                    )

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
                        last_success = time.time()

                        if last_status != "online":
                            mqtt_publish(SYSTEM_STATUS_TOPIC, "online")
                            last_status = "online"

                        mqtt_publish(
                            LAST_UPDATE_TOPIC,
                            datetime.fromtimestamp(
                                last_success,
                                timezone.utc
                            ).isoformat()
                        )

                        if not device_ready[name]:
                            device_ready[name] = True
                            log_device_ready(name)

                    mqtt_publish(
                        MQTT_STATE_TOPIC,
                        {"WEM-Lokal Info": build_clean_data(data_store)}
                    )

                    if discovery_enabled:
                        publish_discovery(data_store)

                        if all_devices_ready():
                            logger.info("ℹ️ All devices provided initial data – discovery disabled")
                            log_summary_after_discovery(data_store)
                            logger.info("🕒 Daily polling statistics will be generated at 00:00")
                            discovery_enabled = False

                    # ---------------------------------------------------------
                    # DAILY STATISTICS TRIGGER
                    # ---------------------------------------------------------
                    now = time.localtime()

                    if now.tm_yday != last_stats_day:

                        # Statistik wird für den Vortag erzeugt
                        stats_day = time.localtime(time.time() - 86400)

                        weekday_short = {
                            0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu",
                            4: "Fri", 5: "Sat", 6: "Sun"
                        }

                        weekday_str = weekday_short[stats_day.tm_wday]
                        date_str = f"{weekday_str}, {stats_day.tm_year}-{stats_day.tm_mon:02d}-{stats_day.tm_mday:02d}"
                        logger.info(f"🕒 Creating daily statistics for {date_str}")
                        output_statistics()
                        logger.info(f"🕒 Daily statistics generated for {date_str}")
                        last_stats_day = now.tm_yday

                    if time.time() - last_success > OFFLINE_TIMEOUT and last_status != "offline":
                        mqtt_publish(SYSTEM_STATUS_TOPIC, "offline")
                        last_status = "offline"

                    await asyncio.sleep(PAUSE_SECONDS)

                if session_broken:
                    break

# ---------------------------
# START
# ---------------------------

if __name__ == "__main__":
    asyncio.run(main())
