import asyncio
import aiohttp
from aiohttp import ClientConnectorError
from bs4 import BeautifulSoup
from urllib.parse import urlencode
import json
import os
import time
import sys
import paho.mqtt.client as mqtt
import unicodedata
import logging
import signal
from datetime import datetime, timezone
from yarl import URL
initial_sync_done = False
INITIAL_SYNC_ORDER = [
    "Heizkreis 1",
    "Heizkreis 2",
    "Heizkreis 3",
    "Heizkreis 4",
    "Statistik",
    "2. WEZ"
]

INITIAL_SYNC_RETRIES = 2
INITIAL_SYNC_RETRY_DELAY = 2
login_fail_count = 0

# Conservative WebIF timing / session-preservation test parameters
NORMAL_RETRY_DELAY_SECONDS = 10.0
STARTUP_AUTH_VALIDATION_DELAY_SECONDS = 5.0
STARTUP_AUTH_TARGET_SECONDS = 120.0
STARTUP_AUTH_MAX_SECONDS = 300.0
SAME_COOKIE_REVALIDATION_DELAY_SECONDS = 15.0
SAME_COOKIE_REVALIDATION_ATTEMPTS = 3
DAILY_WEBIF_REST_SECONDS = 90.0
TRANSPORT_CONFIRMATION_DELAY_SECONDS = 10.0

# Explicit protected-page / control-flow results.
WEBIF_PAGE_VALID = "valid"
WEBIF_PAGE_EMPTY = "empty"
WEBIF_PAGE_LOGIN = "login_page"
WEBIF_PAGE_TRANSPORT = "transport_error"

SESSION_REVALIDATION_ACCEPTED = "accepted"
SESSION_REVALIDATION_REJECTED = "rejected"
SESSION_REVALIDATION_TRANSPORT = "transport_error"

FETCH_CONTROL_NONE = "none"
FETCH_CONTROL_TRANSPORT = "transport"
FETCH_CONTROL_SESSION = "session_suspected"
last_fetch_control = FETCH_CONTROL_NONE

# Isolated WebIF outage protection (v1.1.1)
WEBIF_UNREACHABLE_CONFIRMATIONS = 5
WEBIF_RECOVERY_SHORT_ATTEMPTS = 4
WEBIF_RECOVERY_SHORT_PAUSE = 900
WEBIF_RECOVERY_LONG_PAUSE = 1800

LOGIN_RESULT_SUCCESS = "success"
LOGIN_RESULT_INDEX_UNREACHABLE = "index_unreachable"
LOGIN_RESULT_REQUEST_FAILED = "login_request_failed"
LOGIN_RESULT_REJECTED = "login_rejected"

last_login_result = None
last_login_index_status = None
last_login_post_status = None
last_webif_error = None
current_availability = "online"

# ---------------------------
# DEBUG
# ---------------------------

DEBUG_WEBIF = False

# ---------------------------
# FUNCTIONAL RECOVERY STATE / LOG HELPERS
# ---------------------------

# Functional state: this timestamp gates the 30-minute replacement-login
# cooldown in the runtime recovery path.
last_replacement_login_monotonic = None
last_control_reason = None


def get_session_cookie(session):
    try:
        cookies = session.cookie_jar.filter_cookies(URL(BASE_URL))
        morsel = cookies.get("session")
        return morsel.value if morsel is not None else None
    except Exception:
        return None


def cookie_transition(before, after):
    if before is None and after is not None:
        return "created"
    if before is not None and after is None:
        return "missing"
    if before is not None and after is not None and before != after:
        return "changed"
    if before is not None and after is not None and before == after:
        return "unchanged"
    return "absent"


def log_startup_anomaly(stage, reason, causes_session_renewal=False):
    global last_control_reason
    if causes_session_renewal:
        last_control_reason = f"{stage}: {reason}"


def signal_handler(signum, frame):
    raise SystemExit(0)

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
    logger.info(f"✅ Initial data received: {name}")

# ---------------------------
# CONFIGURATION
# ---------------------------

with open("/data/options.json") as f:
    config = json.load(f)

IP = config.get("webinterface_ip_address", "").strip()
USERNAME = config.get("webinterface_username", "").strip()
PASSWORD = config.get("webinterface_password", "").strip()

if not IP:
    raise ValueError("webinterface_ip_address missing")
if not USERNAME:
    raise ValueError("webinterface_username missing")
if not PASSWORD:
    raise ValueError("webinterface_password missing")
BASE_URL = f"http://{IP}"

# URLS are detected dynamically after login from the WEM Profimodus pages.
# They intentionally start empty so no manual HEX code or static URL generation is required.
URLS = {}
device_ready = {}
SEQUENCE = []

SUPPORTED_DYNAMIC_DEVICES = [
    "Wärmepumpe",
    "Heizkreis 1",
    "Heizkreis 2",
    "Heizkreis 3",
    "Heizkreis 4",
    "Statistik",
    "2. WEZ",
]

def build_round_robin_sequence(active_urls):
    """
    Build the effective Round Robin sequence from BASE_SEQUENCE and the
    dynamically active URL set. Consecutive duplicate devices are collapsed
    so missing optional devices do not create repeated direct WP polling.
    """
    sequence = []

    for device in BASE_SEQUENCE:
        if device not in active_urls:
            continue

        if sequence and sequence[-1] == device:
            continue

        sequence.append(device)

    return sequence


BASE_SEQUENCE = [
    "Wärmepumpe", "Heizkreis 1",
    "Wärmepumpe", "Heizkreis 2",
    "Wärmepumpe", "Heizkreis 3",
    "Wärmepumpe", "Heizkreis 4",
    "Wärmepumpe", "Statistik",
    "Wärmepumpe", "Heizkreis 1",
    "Wärmepumpe", "Heizkreis 2",
    "Wärmepumpe", "Heizkreis 3",
    "Wärmepumpe", "Heizkreis 4",
    "Wärmepumpe", "Statistik",
    "Wärmepumpe", "2. WEZ",
]

SEQUENCE = build_round_robin_sequence(URLS)

# ---------------------------
# STATISTICS
# ---------------------------

stats = {}

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
SESSION_STATE_FILE = "/data/webif_session.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("wem_mqtt_unified")
logger.info("🚀 Starting Weishaupt WEM-Lokal MQTT v1.1.1")
try:
    signal.signal(signal.SIGTERM, signal_handler)
except Exception:
    pass

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
        client.publish(AVAILABILITY_TOPIC, current_availability, qos=1, retain=True)

def on_disconnect(client, userdata, rc=None, properties=None, *args):
    """
    Compatible with both paho-mqtt callback API variants.
    Some versions pass an additional reason/properties argument on disconnect.
    """
    if rc != 0:
        logger.warning("⚠️ MQTT connection lost – reconnecting")

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
except Exception:
    logger.error("❌ MQTT connection failed – check broker address, port and availability")
    logger.error("🛑 WEM-Lokal MQTT stopped")

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
    logger.error("❌ MQTT authentication failed – check username, password and permissions")
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    logger.error("🛑 WEM-Lokal MQTT stopped")
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
    except Exception:
        logger.warning(
            "⚠️ Daily statistics could not be saved – values may be lost after restart"
        )


def load_persisted_session_cookie(session):
    """Load a previously protected-page-validated WEM session into this process."""
    try:
        with open(SESSION_STATE_FILE, "r") as f:
            data = json.load(f)

        if data.get("schema") != 1:
            return False
        if data.get("base_url") != BASE_URL:
            return False
        if data.get("username") != USERNAME:
            return False

        cookie = data.get("session")
        if not isinstance(cookie, str) or not cookie:
            return False

        session.cookie_jar.update_cookies(
            {"session": cookie},
            response_url=URL(BASE_URL),
        )
        return True

    except FileNotFoundError:
        return False
    except Exception:
        return False


def save_validated_session_cookie(session):
    """Persist only a session cookie that has already passed protected validation."""
    cookie = get_session_cookie(session)
    if not cookie:
        return False

    temporary_file = SESSION_STATE_FILE + ".tmp"
    data = {
        "schema": 1,
        "base_url": BASE_URL,
        "username": USERNAME,
        "session": cookie,
    }

    try:
        with open(temporary_file, "w") as f:
            json.dump(data, f)
        try:
            os.chmod(temporary_file, 0o600)
        except Exception:
            pass
        os.replace(temporary_file, SESSION_STATE_FILE)
        return True
    except Exception:
        try:
            os.remove(temporary_file)
        except Exception:
            pass
        logger.warning(
            "⚠️ Validated WebIF session could not be saved – next restart may require a new login"
        )
        return False


def clear_persisted_session_cookie():
    """Remove a session only after the WebIF has explicitly rejected it."""
    try:
        os.remove(SESSION_STATE_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass

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

def is_login_page(html):
    if not isinstance(html, str):
        return False
    html_lower = html.lower()
    return "form-signin" in html_lower or "bitte anmelden" in html_lower

# ---------------------------
# ROBUST HTTP WRAPPING
# ---------------------------

def record_webif_error(method, url, error_type, details):
    """Store the last WebIF request error for the recovery log."""
    global last_webif_error

    last_webif_error = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "url": url,
        "error_type": error_type,
        "details": details,
    }



async def safe_request(session, method, url, **kwargs):
    try:
        async with session.request(method, url, **kwargs) as resp:
            text = await resp.text()
            return resp, text

    except ClientConnectorError as e:
        record_webif_error(method, url, type(e).__name__, str(e).strip() or repr(e))
        return None, None

    except asyncio.TimeoutError as e:
        record_webif_error(method, url, type(e).__name__, str(e).strip() or repr(e))
        return None, None

    except Exception as e:
        record_webif_error(method, url, type(e).__name__, str(e).strip() or repr(e))
        return None, None


# ---------------------------
# LOGIN
# ---------------------------

async def login(session):
    global last_login_result
    global last_login_index_status
    global last_login_post_status

    last_login_index_status = None
    last_login_post_status = None

    resp, _ = await safe_request(session, "GET", "/index.html")
    if resp is None:
        last_login_result = LOGIN_RESULT_INDEX_UNREACHABLE
        return False

    last_login_index_status = resp.status
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
        last_login_result = LOGIN_RESULT_REQUEST_FAILED
        return False

    last_login_post_status = resp.status
    login_ok = resp.status == 303

    if login_ok:
        last_login_result = LOGIN_RESULT_SUCCESS
    else:
        last_login_result = LOGIN_RESULT_REJECTED
        record_webif_error(
            "POST",
            "/login.html",
            f"HTTP {resp.status}",
            "Login response did not contain the expected HTTP 303 status",
        )

    return login_ok


async def check_webif_index_reachability(session, context, attempt=None):
    """Functional reachability check using the existing main session/cookie."""
    resp, html = await safe_request(session, "GET", "/index.html")
    return (resp is not None and html is not None), None


async def startup_authenticate_fast(session):
    """Acquire the first validated WebIF session within a bounded startup window."""
    global login_fail_count

    started = time.monotonic()
    attempt = 0
    target_notice_emitted = False

    def emit_target_notice_if_needed(elapsed_seconds):
        nonlocal target_notice_emitted
        if elapsed_seconds >= STARTUP_AUTH_TARGET_SECONDS and not target_notice_emitted:
            target_notice_emitted = True
            logger.warning(
                "⚠️ WebIF session setup is taking longer than expected – continuing for up to 5 minutes"
            )

    def fail_startup(reason):
        elapsed_seconds = time.monotonic() - started
        emit_target_notice_if_needed(elapsed_seconds)
        logger.error("❌ WebIF session could not be established within 5 minutes")
        return SESSION_REVALIDATION_REJECTED, None, None

    while True:
        elapsed_before = time.monotonic() - started
        emit_target_notice_if_needed(elapsed_before)
        if elapsed_before >= STARTUP_AUTH_MAX_SECONDS:
            return fail_startup("maximum_startup_authentication_time_exceeded")

        attempt += 1
        cookie_before = get_session_cookie(session)
        login_started = time.monotonic()
        remaining = STARTUP_AUTH_MAX_SECONDS - (login_started - started)

        try:
            login_ok = await asyncio.wait_for(login(session), timeout=max(0.001, remaining))
        except asyncio.TimeoutError:
            return fail_startup("startup_login_operation_exceeded_absolute_window")

        login_duration = time.monotonic() - login_started
        cookie_after = get_session_cookie(session)
        login_cookie_transition = cookie_transition(cookie_before, cookie_after)

        if not login_ok:
            login_fail_count += 1
            elapsed = time.monotonic() - started
            emit_target_notice_if_needed(elapsed)

            remaining = STARTUP_AUTH_MAX_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                continue
            retry_sleep = min(10.0, remaining)
            await asyncio.sleep(retry_sleep)
            continue

        login_fail_count = 0
        elapsed = time.monotonic() - started
        emit_target_notice_if_needed(elapsed)
        remaining = STARTUP_AUTH_MAX_SECONDS - elapsed
        if remaining < STARTUP_AUTH_VALIDATION_DELAY_SECONDS:
            return fail_startup("insufficient_time_for_required_startup_validation_grace")

        await asyncio.sleep(STARTUP_AUTH_VALIDATION_DELAY_SECONDS)

        remaining = STARTUP_AUTH_MAX_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            return fail_startup("maximum_startup_authentication_time_exceeded_before_validation")

        try:
            page_result, overview, _ = await asyncio.wait_for(
                detect_webif_overview(session, context_label="startup_fast_validation"),
                timeout=max(0.001, remaining),
            )
        except asyncio.TimeoutError:
            return fail_startup("startup_protected_validation_exceeded_absolute_window")

        elapsed = time.monotonic() - started
        emit_target_notice_if_needed(elapsed)

        if page_result in (WEBIF_PAGE_VALID, WEBIF_PAGE_EMPTY):
            logger.info(f"✅ WebIF session validated in {elapsed:.1f} s")
            return SESSION_REVALIDATION_ACCEPTED, page_result, overview

        if page_result == WEBIF_PAGE_TRANSPORT:
            remaining = STARTUP_AUTH_MAX_SECONDS - elapsed
            if remaining <= 0:
                continue
            await asyncio.sleep(min(10.0, remaining))
            continue




async def startup_validate_persisted_session(session):
    """Validate a persisted WEM session without creating a replacement login.

    Transport errors never delete the persisted cookie.  A fresh login is only
    allowed after the protected WebIF explicitly returns the login page.
    """
    if not load_persisted_session_cookie(session):
        return None, None, None

    logger.info("ℹ️ Checking existing WebIF session")

    started = time.monotonic()
    target_notice_emitted = False

    while True:
        elapsed_before = time.monotonic() - started

        if (
            elapsed_before >= STARTUP_AUTH_TARGET_SECONDS
            and not target_notice_emitted
        ):
            target_notice_emitted = True
            logger.warning(
                "⚠️ Existing WebIF session could not yet be validated – continuing for up to 5 minutes"
            )

        if elapsed_before >= STARTUP_AUTH_MAX_SECONDS:
            logger.error(
                "❌ Existing WebIF session could not be validated within 5 minutes – saved session retained"
            )
            return SESSION_REVALIDATION_TRANSPORT, None, None

        page_result, overview, _ = await detect_webif_overview(
            session,
            context_label="startup_persisted_session",
        )

        elapsed = time.monotonic() - started

        if page_result in (WEBIF_PAGE_VALID, WEBIF_PAGE_EMPTY):
            save_validated_session_cookie(session)
            logger.info(f"✅ Existing WebIF session validated in {elapsed:.1f} s")
            return SESSION_REVALIDATION_ACCEPTED, page_result, overview

        if page_result == WEBIF_PAGE_LOGIN:
            clear_persisted_session_cookie()
            session.cookie_jar.clear()
            logger.info(
                "ℹ️ Existing WebIF session rejected – establishing a new WebIF session"
            )
            return SESSION_REVALIDATION_REJECTED, page_result, None

        remaining = STARTUP_AUTH_MAX_SECONDS - elapsed
        if remaining <= 0:
            continue

        await asyncio.sleep(min(NORMAL_RETRY_DELAY_SECONDS, remaining))


async def same_cookie_revalidate(session, context, trigger):
    """Validate the existing cookie up to three times without creating a login."""
    for attempt in range(1, SAME_COOKIE_REVALIDATION_ATTEMPTS + 1):
        await asyncio.sleep(SAME_COOKIE_REVALIDATION_DELAY_SECONDS)
        page_result, overview, _ = await detect_webif_overview(
            session,
            context_label=f"same_cookie_revalidation:{context}",
        )

        if page_result in (WEBIF_PAGE_VALID, WEBIF_PAGE_EMPTY):
            save_validated_session_cookie(session)
            return SESSION_REVALIDATION_ACCEPTED, page_result, overview

        if page_result == WEBIF_PAGE_TRANSPORT:
            return SESSION_REVALIDATION_TRANSPORT, page_result, None

    clear_persisted_session_cookie()
    return SESSION_REVALIDATION_REJECTED, WEBIF_PAGE_LOGIN, None




async def replacement_login_with_validation(session, context, trigger):
    """Perform one controlled replacement login, then validate its cookie."""
    global last_control_reason
    global last_replacement_login_monotonic

    cookie_before = get_session_cookie(session)
    last_replacement_login_monotonic = time.monotonic()

    login_ok = await login(session)
    cookie_after_login = get_session_cookie(session)
    login_cookie_transition = cookie_transition(cookie_before, cookie_after_login)

    if not login_ok:
        if last_login_result in (LOGIN_RESULT_INDEX_UNREACHABLE, LOGIN_RESULT_REQUEST_FAILED):
            return SESSION_REVALIDATION_TRANSPORT, None, None
        return SESSION_REVALIDATION_REJECTED, None, None

    validation_status, overview_result, overview = await same_cookie_revalidate(
        session,
        context=f"replacement_login:{context}",
        trigger=trigger,
    )

    if validation_status == SESSION_REVALIDATION_ACCEPTED:
        last_control_reason = None
        return validation_status, overview_result, overview

    return validation_status, overview_result, overview




async def run_webif_recovery(
    session,
    first_failure_time,
    trigger,
    transport_outage_confirmed=False,
    replacement_login_allowed=True,
):
    """Long recovery cadence with preserved session and rate-limited replacement-login liveness."""
    global current_availability
    global last_replacement_login_monotonic

    current_availability = "offline"
    mqtt_publish(AVAILABILITY_TOPIC, "offline")
    mqtt_publish(SYSTEM_STATUS_TOPIC, "WebIF Pause")

    if not replacement_login_allowed and last_replacement_login_monotonic is None:
        last_replacement_login_monotonic = time.monotonic()

    if transport_outage_confirmed:
        logger.error("❌ WebIF unavailable – outage confirmed")
        logger.info("⏳ Recovery mode started – next check in 15 minutes")
    else:
        logger.error("❌ WebIF session could not be restored – recovery mode started")
        logger.info("⏳ Next recovery check in 15 minutes")

    recovery_attempt = 0

    while True:
        pause_seconds = (
            WEBIF_RECOVERY_SHORT_PAUSE
            if recovery_attempt < WEBIF_RECOVERY_SHORT_ATTEMPTS
            else WEBIF_RECOVERY_LONG_PAUSE
        )
        await asyncio.sleep(pause_seconds)
        recovery_attempt += 1

        mqtt_publish(SYSTEM_STATUS_TOPIC, "WebIF Test")
        reachable, _ = await check_webif_index_reachability(
            session,
            context="recovery",
            attempt=recovery_attempt,
        )

        if reachable:
            validation_status, overview_result, overview = await same_cookie_revalidate(
                session,
                context="recovery",
                trigger=trigger,
            )

            if validation_status == SESSION_REVALIDATION_ACCEPTED:
                current_availability = "online"
                mqtt_publish(AVAILABILITY_TOPIC, "online")
                mqtt_publish(SYSTEM_STATUS_TOPIC, "online")
                logger.info("✅ WebIF recovered with existing session – Round Robin resumed")
                return True, overview_result, overview

            if validation_status == SESSION_REVALIDATION_REJECTED:
                now_mono = time.monotonic()
                if last_replacement_login_monotonic is None:
                    elapsed = None
                    replacement_allowed_now = True
                else:
                    elapsed = max(0.0, now_mono - last_replacement_login_monotonic)
                    replacement_allowed_now = elapsed >= WEBIF_RECOVERY_LONG_PAUSE

                if replacement_allowed_now:
                    replacement_status, replacement_overview_result, replacement_overview = (
                        await replacement_login_with_validation(
                            session,
                            context="recovery",
                            trigger=trigger,
                        )
                    )
                    if replacement_status == SESSION_REVALIDATION_ACCEPTED:
                        current_availability = "online"
                        mqtt_publish(AVAILABILITY_TOPIC, "online")
                        mqtt_publish(SYSTEM_STATUS_TOPIC, "online")
                        logger.info("✅ WebIF recovered after new login – Round Robin resumed")
                        return True, replacement_overview_result, replacement_overview

        mqtt_publish(SYSTEM_STATUS_TOPIC, "WebIF Pause")

        if recovery_attempt == WEBIF_RECOVERY_SHORT_ATTEMPTS:
            logger.warning(
                "⚠️ WebIF still unavailable after 4 recovery checks – switching to 30-minute interval"
            )
            logger.info(
                "ℹ️ If the WebIF remains unavailable, restart the Webserver in the Weishaupt control:\n"
                "   set Webserver to OFF, wait 60 seconds, then set Webserver to ON"
            )
        elif recovery_attempt < WEBIF_RECOVERY_SHORT_ATTEMPTS:
            logger.info(
                f"⏳ WebIF still unavailable – recovery check {recovery_attempt}/"
                f"{WEBIF_RECOVERY_SHORT_ATTEMPTS}; next check in 15 minutes"
            )
        else:
            logger.info("⏳ WebIF still unavailable – next recovery check in 30 minutes")
            logger.info(
                "ℹ️ If the WebIF remains unavailable, restart the Webserver in the Weishaupt control:\n"
                "   set Webserver to OFF, wait 60 seconds, then set Webserver to ON"
            )


async def handle_transport_interruption(
    session,
    context,
    trigger,
    replacement_login_allowed=True,
):
    """Confirm transport trouble without discarding the main session/cookie."""
    first_failure_time = datetime.now(timezone.utc).isoformat()
    if initial_sync_done:
        logger.warning("⚠️ WebIF connection interrupted – checking availability")
    failed_control_checks = 0
    consecutive_index_unreachable = 0
    replacement_login_used = not replacement_login_allowed

    while failed_control_checks < WEBIF_UNREACHABLE_CONFIRMATIONS:
        attempt = failed_control_checks + 1
        reachable, _ = await check_webif_index_reachability(
            session,
            context=f"transport_confirmation:{context}",
            attempt=attempt,
        )

        if not reachable:
            failed_control_checks += 1
            consecutive_index_unreachable += 1
            if failed_control_checks >= WEBIF_UNREACHABLE_CONFIRMATIONS:
                break
            await asyncio.sleep(TRANSPORT_CONFIRMATION_DELAY_SECONDS)
            continue

        consecutive_index_unreachable = 0
        validation_status, overview_result, overview = await same_cookie_revalidate(
            session,
            context=f"transport_recovery:{context}",
            trigger=trigger,
        )

        if validation_status == SESSION_REVALIDATION_ACCEPTED:
            if initial_sync_done:
                logger.info("✅ WebIF connection restored – Round Robin resumed")
            return True, overview_result, overview

        if validation_status == SESSION_REVALIDATION_REJECTED:
            if not replacement_login_used:
                replacement_login_used = True
                replacement_status, replacement_overview_result, replacement_overview = (
                    await replacement_login_with_validation(
                        session,
                        context=f"transport_recovery:{context}",
                        trigger=trigger,
                    )
                )
                if replacement_status == SESSION_REVALIDATION_ACCEPTED:
                    if initial_sync_done:
                        logger.info("✅ WebIF connection restored – Round Robin resumed")
                    return True, replacement_overview_result, replacement_overview

        failed_control_checks += 1
        if failed_control_checks >= WEBIF_UNREACHABLE_CONFIRMATIONS:
            break
        await asyncio.sleep(TRANSPORT_CONFIRMATION_DELAY_SECONDS)

    transport_outage_confirmed = (
        consecutive_index_unreachable >= WEBIF_UNREACHABLE_CONFIRMATIONS
    )
    return await run_webif_recovery(
        session,
        first_failure_time,
        trigger=trigger,
        transport_outage_confirmed=transport_outage_confirmed,
        replacement_login_allowed=not replacement_login_used,
    )


async def resolve_session_suspicion(session, context, trigger):
    """Same-cookie first; at most one immediate replacement login."""
    if initial_sync_done:
        logger.warning("⚠️ WebIF session requires validation – checking existing session")
    validation_status, overview_result, overview = await same_cookie_revalidate(
        session,
        context=context,
        trigger=trigger,
    )

    if validation_status == SESSION_REVALIDATION_ACCEPTED:
        if initial_sync_done:
            logger.info("✅ WebIF session validated – Round Robin resumed")
        return True, overview_result, overview

    if validation_status == SESSION_REVALIDATION_TRANSPORT:
        return await handle_transport_interruption(session, context, trigger)

    if initial_sync_done:
        logger.warning("⚠️ Existing WebIF session rejected – attempting controlled new login")
    replacement_status, replacement_overview_result, replacement_overview = (
        await replacement_login_with_validation(
            session,
            context=context,
            trigger=trigger,
        )
    )
    if replacement_status == SESSION_REVALIDATION_ACCEPTED:
        if initial_sync_done:
            logger.info("✅ New WebIF session validated – Round Robin resumed")
        return True, replacement_overview_result, replacement_overview

    if replacement_status == SESSION_REVALIDATION_TRANSPORT:
        return await handle_transport_interruption(
            session,
            context=f"replacement_login_transport:{context}",
            trigger=trigger,
            replacement_login_allowed=False,
        )

    # Do not create an immediate replacement-login loop. Enter the existing
    # long recovery cadence with the same main session/cookie instead.
    return await run_webif_recovery(
        session,
        datetime.now(timezone.utc).isoformat(),
        trigger=f"{trigger}:replacement_login_not_validated",
        transport_outage_confirmed=False,
        replacement_login_allowed=False,
    )

# ---------------------------
# WEBIF OVERVIEW DETECTION (READ-ONLY TEST)
# ---------------------------

async def detect_webif_overview(session, context_label="startup"):
    """Read-only detection of the Profimodus overview page."""
    resp, html = await safe_request(
        session,
        "GET",
        "/settings_export.html",
        headers=HEADERS
    )

    if resp is None or html is None:
        return WEBIF_PAGE_TRANSPORT, None, None

    if is_login_page(html):
        global last_control_reason
        last_control_reason = f"overview: login page returned ({context_label})"
        return WEBIF_PAGE_LOGIN, None, None

    soup = BeautifulSoup(html, "html.parser")
    detected = {}

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        h5 = link.find("h5")
        if not h5:
            continue
        if "settings_export.html?stack=" not in href:
            continue
        stack = href.split("stack=", 1)[1].strip()
        if "," in stack:
            continue
        name = h5.get_text(strip=True)
        if name and stack:
            detected[name] = stack

    if not detected:
        return WEBIF_PAGE_EMPTY, {}, None

    if "Fehlerspeicher" not in detected:
        return WEBIF_PAGE_EMPTY, {}, None

    if DEBUG_WEBIF:
        logger.info("🔍 Detected WebIF overview:")
        for name, stack in detected.items():
            logger.info(f"   • {name}: {stack}")
    return WEBIF_PAGE_VALID, detected, None


async def detect_webif_overview_fast_check(session, initial_result=None):
    """Preserve the existing 3-second semantic retry for readable empty pages."""
    attempt = 1
    pending_result = initial_result

    while True:
        if DEBUG_WEBIF:
            logger.info(f"🔍 Detecting WebIF overview (attempt {attempt})")

        if pending_result is not None:
            result, detected = pending_result
            pending_result = None
        else:
            result, detected, _ = await detect_webif_overview(session)

        if result in (WEBIF_PAGE_TRANSPORT, WEBIF_PAGE_LOGIN):
            return result, None

        if result == WEBIF_PAGE_VALID:
            return result, detected

        # WEBIF_PAGE_EMPTY -> authenticated/readable but semantically incomplete.
        if DEBUG_WEBIF:
            logger.info("⏳ WebIF overview contained no usable entries – retrying in 3s")
        await asyncio.sleep(3)
        attempt += 1

# ---------------------------
# WEBIF DATA URL DETECTION (READ-ONLY TEST)
# ---------------------------

async def detect_webif_data_urls(session, overview):
    """Read-only detection of the final data URLs from the Info 1st-stack page."""
    info_stack = overview.get("Info") if overview else None

    if not info_stack:
        log_startup_anomaly("data_urls", "Info stack missing from detected overview")
        return WEBIF_PAGE_EMPTY, {}, None

    info_url = f"/settings_export.html?stack={info_stack}"
    resp, html = await safe_request(session, "GET", info_url, headers=HEADERS)

    if resp is None or html is None:
        log_startup_anomaly("data_urls", "no HTTP response while reading Info stack")
        return WEBIF_PAGE_TRANSPORT, None, None

    if is_login_page(html):
        log_startup_anomaly(
            "data_urls",
            "login page returned while reading Info stack",
            causes_session_renewal=True,
        )
        return WEBIF_PAGE_LOGIN, None, None

    soup = BeautifulSoup(html, "html.parser")
    detected = {}

    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()
        h5 = link.find("h5")
        if not h5:
            continue
        if "settings_export.html?stack=" not in href:
            continue
        if "," not in href:
            continue
        name = h5.get_text(strip=True)
        if not name:
            continue
        final_url = href if href.startswith("/") else "/" + href
        detected[name] = final_url

    if not detected:
        log_startup_anomaly("data_urls", "HTTP response readable but no data URLs detected")
        return WEBIF_PAGE_EMPTY, {}, None

    if "Statistik" not in detected:
        log_startup_anomaly("data_urls", "Statistik missing; likely wrong page / first-stack overview")
        return WEBIF_PAGE_EMPTY, {}, None

    return WEBIF_PAGE_VALID, detected, None


async def detect_webif_data_urls_fast_check(session, overview):
    """Preserve the existing 3-second semantic retry for readable empty pages."""
    attempt = 1

    while True:
        if DEBUG_WEBIF:
            logger.info(f"🔍 Detecting WebIF data URLs (attempt {attempt})")

        result, detected, _ = await detect_webif_data_urls(session, overview)

        if result in (WEBIF_PAGE_TRANSPORT, WEBIF_PAGE_LOGIN):
            return result, None

        if result == WEBIF_PAGE_VALID:
            return result, detected

        if DEBUG_WEBIF:
            logger.info("⏳ WebIF Info stack contained no usable data URLs – retrying in 3s")
        await asyncio.sleep(3)
        attempt += 1

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
    global last_control_reason
    global last_fetch_control

    last_fetch_control = FETCH_CONTROL_NONE
    stats[name]["total"] += 1

    resp, html = await safe_request(session, "GET", url, headers=HEADERS)

    if resp is None or html is None:
        last_control_reason = f"request failed during poll of {name}"
        last_fetch_control = FETCH_CONTROL_TRANSPORT
        stats[name]["failed"] += 1
        return None

    first_login_page = is_login_page(html)
    if first_login_page:
        last_control_reason = f"login page returned during poll of {name}"
        last_fetch_control = FETCH_CONTROL_SESSION
        stats[name]["failed"] += 1
        return None

    values = extract_values(html)
    first_wrong_section = is_wrong_section(name, values) if values else False

    if not values:
        await asyncio.sleep(NORMAL_RETRY_DELAY_SECONDS)
        resp2, html2 = await safe_request(session, "GET", url, headers=HEADERS)

        if resp2 is None or html2 is None:
            stats[name]["failed"] += 1
            return {}

        retry_login_page = is_login_page(html2)
        retry_values = extract_values(html2)
        retry_wrong_section = is_wrong_section(name, retry_values) if retry_values else False

        if retry_values:
            if retry_wrong_section:
                stats[name]["failed"] += 1
                return {}

            stats[name]["retry_success"] += 1
            return retry_values

        stats[name]["failed"] += 1
        if retry_login_page:
            last_control_reason = f"login page returned during retry poll of {name}"
            last_fetch_control = FETCH_CONTROL_SESSION
            return None
        return {}

    if first_wrong_section:
        stats[name]["failed"] += 1
        return {}

    stats[name]["first_success"] += 1
    return values



# ---------------------------
# DYNAMIC URL APPLY
# ---------------------------

def apply_detected_data_urls(data_urls):
    """
    Apply dynamically detected final WebIF data URLs.

    This replaces only the URL/device initialization data.
    The existing login, safe_request, fetch, WP fast check,
    initial sync, round robin, MQTT, discovery and statistics logic
    continue to use the same structures as before.
    """
    global URLS
    global device_ready
    global SEQUENCE
    global stats

    if not data_urls or "Wärmepumpe" not in data_urls:
        return False

    new_urls = {}

    for device in SUPPORTED_DYNAMIC_DEVICES:
        if device in data_urls:
            new_urls[device] = data_urls[device]

    URLS = new_urls
    device_ready = {name: False for name in URLS.keys()}
    SEQUENCE = build_round_robin_sequence(URLS)
    stats = {
        name: {"total": 0, "first_success": 0, "retry_success": 0, "failed": 0}
        for name in URLS.keys()
    }

    logger.info("✅ WebIF devices detected: " + ", ".join(URLS.keys()))

    return True

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
        "entity_category": "diagnostic",
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

    webif_status_payload = {
        "name": "WebIF-Status",
        "uniq_id": "wem_lokal_webif_status",
        "state_topic": SYSTEM_STATUS_TOPIC,
        "value_template": (
            "{% set states = {"
            "'online': 'Verbunden', "
            "'offline': 'Offline – App-Log', "
            "'WebIF Test': 'Überprüfungsphase – App-Log', "
            "'WebIF Pause': 'Erholungsphase – App-Log', "
            "'WebIF Neustart': 'App-Startphase – App-Log'"
            "} %}"
            "{{ states.get(value, value) }}"
        ),
        "icon": "mdi:web",
        "entity_category": "diagnostic",
        "device": {
            "identifiers": [main_device_id],
            "name": "WEM-Lokal Info",
            "manufacturer": "Weishaupt",
            "model": wp_model
        }
    }

    mqtt_publish(
        f"{MQTT_BASE}/sensor/wem_lokal_webif_status/config",
        webif_status_payload,
        retain=True
    )

    last_update_payload = {
        "name": "Update Sensoren",
        "uniq_id": "wem_lokal_last_update",

        "state_topic": LAST_UPDATE_TOPIC,

        "device_class": "timestamp",
        "entity_category": "diagnostic",

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
        "entity_category": "diagnostic",

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
        "Heizkreis 3": ("wem_lokal_hk3", "WEM-Lokal Heizkreis 3"),
        "Heizkreis 4": ("wem_lokal_hk4", "WEM-Lokal Heizkreis 4"),
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

            elif key_lower == "pumpe" and section.startswith("Heizkreis"):
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
# DAILY WEBIF REST
# ---------------------------


async def run_daily_webif_rest(session, jar, statistics_date):
    """90-second request-free transport reset while preserving WEM session cookie."""
    await session.close()
    await asyncio.sleep(DAILY_WEBIF_REST_SECONDS)

    new_session = aiohttp.ClientSession(
        base_url=BASE_URL,
        cookie_jar=jar,
    )
    return new_session



# ---------------------------
# STATISTICS OUTPUT
# ---------------------------

def output_statistics():

    order = [
        "Wärmepumpe",
        "Heizkreis 1",
        "Heizkreis 2",
        "Heizkreis 3",
        "Heizkreis 4",
        "Statistik",
        "2. WEZ"
    ]

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

        system_total += total
        system_first += s["first_success"]
        system_retry += s["retry_success"]
        system_failed += s["failed"]

    if system_total > 0:

        system_total_pct = ((system_first + system_retry) / system_total) * 100

        stats_day = time.localtime(time.time() - 86400)

        log_date = (
            f"{stats_day.tm_year:04d}-"
            f"{stats_day.tm_mon:02d}-"
            f"{stats_day.tm_mday:02d}"
        )
        logger.info(
            f"🕒 [{log_date}] Daily statistics – {system_total_pct:.1f}% successful "
            f"({system_first + system_retry}/{system_total} polls, {system_failed} failed)"
        )

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
    global URLS
    global device_ready
    global SEQUENCE
    global stats
    global last_control_reason

    jar = aiohttp.CookieJar(unsafe=True)
    session = aiohttp.ClientSession(
        base_url=BASE_URL,
        cookie_jar=jar
    )

    try:
        data_store = {}
        dynamic_urls_ready = False
        last_success = time.time()
        last_status = "offline"
        overview_initial_result = None

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

        # Reuse a previously protected-page-validated session across app
        # restarts/updates.  The proven fresh-login startup path remains the
        # exact fallback when no persisted cookie exists or the WebIF
        # explicitly rejects it.  Transport errors do not invalidate it.
        validation_status, overview_result, overview = await startup_validate_persisted_session(session)

        if validation_status == SESSION_REVALIDATION_TRANSPORT:
            return

        if validation_status != SESSION_REVALIDATION_ACCEPTED:
            logger.info("ℹ️ Establishing WebIF session – this may take up to 5 minutes")

            # No separate unbounded/15-minute startup reachability branch is used.
            # login() already probes /index.html on every attempt, and the whole
            # startup authentication phase is bounded to STARTUP_AUTH_MAX_SECONDS.
            # This keeps "first valid login" semantically separate from runtime
            # outage/session recovery.
            # Fast bounded startup authentication, intentionally close to the
            # proven v1.1.10 startup behavior: login -> 5 s grace -> protected
            # overview check -> fresh login if that new cookie is explicitly
            # rejected.  Only after the first protected validation does strict
            # runtime session preservation begin.
            validation_status, overview_result, overview = await startup_authenticate_fast(session)

            if validation_status != SESSION_REVALIDATION_ACCEPTED:
                return

            save_validated_session_cookie(session)

        if overview_result in (WEBIF_PAGE_VALID, WEBIF_PAGE_EMPTY):
            overview_initial_result = (overview_result, overview)

        last_control_reason = None

        # Main startup / runtime control loop. Unlike v1.1.10, session or
        # transport anomalies return here after in-process recovery instead of
        # forcing an unconditional login at the top of the loop.
        while True:
            control_restart = False

            if not dynamic_urls_ready:
                overview_result, overview = await detect_webif_overview_fast_check(
                    session,
                    initial_result=overview_initial_result,
                )
                overview_initial_result = None

                if overview_result == WEBIF_PAGE_LOGIN:
                    _, recovered_overview_result, recovered_overview = await resolve_session_suspicion(
                        session,
                        context="startup_overview",
                        trigger="login_page_during_overview_detection",
                    )
                    if recovered_overview_result in (WEBIF_PAGE_VALID, WEBIF_PAGE_EMPTY):
                        overview_initial_result = (recovered_overview_result, recovered_overview)
                    continue

                if overview_result == WEBIF_PAGE_TRANSPORT:
                    _, recovered_overview_result, recovered_overview = await handle_transport_interruption(
                        session,
                        context="startup_overview",
                        trigger="transport_during_overview_detection",
                    )
                    if recovered_overview_result in (WEBIF_PAGE_VALID, WEBIF_PAGE_EMPTY):
                        overview_initial_result = (recovered_overview_result, recovered_overview)
                    continue

                data_url_result, detected_data_urls = await detect_webif_data_urls_fast_check(
                    session, overview
                )

                if data_url_result == WEBIF_PAGE_LOGIN:
                    _, recovered_overview_result, recovered_overview = await resolve_session_suspicion(
                        session,
                        context="startup_data_urls",
                        trigger="login_page_during_data_url_detection",
                    )
                    if recovered_overview_result == WEBIF_PAGE_VALID:
                        overview_initial_result = (recovered_overview_result, recovered_overview)
                    else:
                        # The previously valid overview remains usable; no URL
                        # state is discarded solely because of session recovery.
                        overview_initial_result = (WEBIF_PAGE_VALID, overview)
                    continue

                if data_url_result == WEBIF_PAGE_TRANSPORT:
                    _, recovered_overview_result, recovered_overview = await handle_transport_interruption(
                        session,
                        context="startup_data_urls",
                        trigger="transport_during_data_url_detection",
                    )
                    if recovered_overview_result == WEBIF_PAGE_VALID:
                        overview_initial_result = (recovered_overview_result, recovered_overview)
                    else:
                        overview_initial_result = (WEBIF_PAGE_VALID, overview)
                    continue

                if not apply_detected_data_urls(detected_data_urls):
                    logger.error("❌ WebIF device detection failed – app cannot start")
                    sys.exit(1)

                data_store = {key: {} for key in URLS.keys()}
                dynamic_urls_ready = True

            # ---------------------------
            # WP-FAST-CHECK (Heat pump fast check until first valid data)
            # ---------------------------
            if not initial_sync_done:
                while True:
                    values = await fetch(session, "Wärmepumpe", URLS["Wärmepumpe"])

                    if values is None:
                        trigger = last_control_reason or "startup heat-pump fast-check"
                        if last_fetch_control == FETCH_CONTROL_SESSION:
                            await resolve_session_suspicion(
                                session,
                                context="startup_wp_fast_check",
                                trigger=trigger,
                            )
                        elif last_fetch_control == FETCH_CONTROL_TRANSPORT:
                            await handle_transport_interruption(
                                session,
                                context="startup_wp_fast_check",
                                trigger=trigger,
                            )
                        control_restart = True
                        break

                    if values:
                        data_store["Wärmepumpe"] = values
                        device_ready["Wärmepumpe"] = True

                        mqtt_publish(
                            LAST_UPDATE_TOPIC,
                            datetime.fromtimestamp(time.time(), timezone.utc).isoformat()
                        )

                        mqtt_publish(
                            MQTT_STATE_TOPIC,
                            {"WEM-Lokal Info": build_clean_data(data_store)}
                        )
                        break

                    await asyncio.sleep(3)

                if control_restart:
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
                            trigger = last_control_reason or f"initial sync control interruption ({dev})"
                            if last_fetch_control == FETCH_CONTROL_SESSION:
                                await resolve_session_suspicion(
                                    session,
                                    context="initial_sync",
                                    trigger=trigger,
                                )
                            elif last_fetch_control == FETCH_CONTROL_TRANSPORT:
                                await handle_transport_interruption(
                                    session,
                                    context="initial_sync",
                                    trigger=trigger,
                                )
                            control_restart = True
                            break

                        if values:
                            break

                        if attempt < INITIAL_SYNC_RETRIES:
                            await asyncio.sleep(INITIAL_SYNC_RETRY_DELAY)

                    if control_restart:
                        break

                    if values:
                        data_store[dev] = values
                        device_ready[dev] = True

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

                    await asyncio.sleep(2)

                if control_restart:
                    continue

                initial_sync_done = True
                waiting_for_initial_data = [
                    name for name, ready in device_ready.items() if not ready
                ]
                if waiting_for_initial_data:
                    logger.info(
                        "ℹ️ Initial sync completed – Round Robin active; waiting for initial data from: "
                        + ", ".join(waiting_for_initial_data)
                    )
                else:
                    logger.info("✅ Initial sync completed – Round Robin active")

            # ---------------------------
            # ROUND ROBIN
            # ---------------------------
            while True:
                control_restart = False
                for rr_slot, name in enumerate(SEQUENCE, start=1):
                    if name not in URLS:
                        continue

                    values = await fetch(session, name, URLS[name])

                    if values is None:
                        reason = last_control_reason or f"unclassified poll interruption ({name})"

                        if last_fetch_control == FETCH_CONTROL_SESSION:
                            await resolve_session_suspicion(
                                session,
                                context="round_robin",
                                trigger=reason,
                            )
                        elif last_fetch_control == FETCH_CONTROL_TRANSPORT:
                            await handle_transport_interruption(
                                session,
                                context="round_robin",
                                trigger=reason,
                            )
                        else:
                            logger.error(
                                f"❌ Unexpected WebIF control interruption – {reason}"
                            )

                        last_control_reason = None
                        control_restart = True
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
                            discovery_enabled = False

                    # ---------------------------------------------------------
                    # DAILY STATISTICS TRIGGER
                    # ---------------------------------------------------------
                    now = time.localtime()

                    if now.tm_yday != last_stats_day:
                        stats_day = time.localtime(time.time() - 86400)

                        statistics_date = (
                            f"{stats_day.tm_year}-{stats_day.tm_mon:02d}-{stats_day.tm_mday:02d}"
                        )
                        output_statistics()
                        last_stats_day = now.tm_yday

                        session = await run_daily_webif_rest(session, jar, statistics_date)
                        logger.info(
                            f"✅ [{statistics_date}] Daily WebIF rest completed – "
                            f"{DAILY_WEBIF_REST_SECONDS:.0f} s, session preserved"
                        )


                    if time.time() - last_success > OFFLINE_TIMEOUT and last_status != "offline":
                        mqtt_publish(SYSTEM_STATUS_TOPIC, "offline")
                        last_status = "offline"

                    await asyncio.sleep(PAUSE_SECONDS)

                if control_restart:
                    break

            if control_restart:
                continue

    finally:
        try:
            if session is not None and not session.closed:
                await session.close()
        except Exception:
            pass
        logger.info("🛑 WEM-Lokal MQTT stopped")

# ---------------------------
# START
# ---------------------------

if __name__ == "__main__":
    asyncio.run(main())
