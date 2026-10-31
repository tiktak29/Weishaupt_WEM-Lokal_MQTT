"""True Home Assistant Core 2026.10.2 MQTT Discovery migration test.

Runs in the official Home Assistant container on an isolated GitHub runner.
Uses real MQTT config flow, MQTT entity registry, state machine and Mosquitto.
Only source functions from two checked-out GitHub versions; no real add-on,
no WebIF or external Home Assistant connection.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from paho.mqtt import client as mqtt
from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.config_entries import ConfigEntryState
from test_issue14_v113 import load_real_functions, OLD, NEW

HOST = "127.0.0.1"
PORT = int(os.environ.get("WEM_HA_TEST_MQTT_PORT", "18884"))
UPGRADE_SCENARIO = os.environ.get("WEM_UPGRADE_SCENARIO", "bad_config_first")
assert UPGRADE_SCENARIO in (
    "bad_config_first", "bad_state_first",
    "normalized_config_first", "normalized_state_first"
), f"Unrecognized scenario: {UPGRADE_SCENARIO}"
LOG = logging.getLogger("wem_issue14_ha_core")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

INITIAL = {
    "Außengerät Variante": "WSB 6",
    "Verdichter": "0 Hz",
    "Soll Frequenz Verdichter": "Aus",
    "Ist Frequenz Verdichter": "Aus",
    "Drehzahl Pumpe M1": "55 %",
    "Betriebsstd. Verdichter": "4388 h",
}
INITIAL_COMPRESSOR = os.environ.get("WEM_INITIAL_COMPRESSOR", "0 Hz")
assert INITIAL_COMPRESSOR in ("0 Hz", "45 Hz")
INITIAL["Verdichter"] = INITIAL_COMPRESSOR
INITIAL_EXPECTED_NUMERIC = INITIAL_COMPRESSOR.removesuffix(" Hz")

RUNNING = {
    **INITIAL,
    "Verdichter": "45 Hz",
    "Soll Frequenz Verdichter": "52 HZ",
    "Ist Frequenz Verdichter": "43 hZ",
}
KEYS = ["Verdichter", "Soll Frequenz Verdichter", "Ist Frequenz Verdichter",
        "Drehzahl Pumpe M1", "Betriebsstd. Verdichter"]


def publish_now(packets):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(HOST, PORT, 5)
    client.loop_start()
    try:
        for topic, obj, retain in packets:
            payload = json.dumps(obj, ensure_ascii=False) if isinstance(obj, (dict, list)) else obj
            receipt = client.publish(topic, payload, qos=1, retain=retain)
            receipt.wait_for_publish(timeout=7)
            assert receipt.is_published(), f"MQTT publish failed: {topic}"
    finally:
        client.disconnect()
        client.loop_stop()


async def send(packets):
    await asyncio.to_thread(publish_now, packets)


def prepare_discovery(ns, events, values):
    events.clear()
    ns["publish_discovery"]({"Wärmepumpe": values})
    return list(events)


async def wait_for(condition, label, duration=55):
    deadline = asyncio.get_running_loop().time() + duration
    last = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            got = condition()
            if got:
                return got
        except Exception as exc:
            last = str(exc)
        await asyncio.sleep(0.4)
    raise AssertionError(f"Timeout waiting for {label}; last error={last}")


def sensor_entries(hass):
    registry = er.async_get(hass)
    found = {}
    for key in KEYS:
        uid = "wem_lokal_wp_" + ns_new["normalize_id"](key)
        matches = [entry for entry in registry.entities.values() if entry.platform == "mqtt" and entry.unique_id == uid]
        if len(matches) == 1:
            found[key] = matches[0].entity_id
    return found


def require_state(hass, entity_id, unit, value=None, state_class=None):
    s = hass.states.get(entity_id)
    if s is None or s.attributes.get("unit_of_measurement") != unit:
        return False
    if value is not None and s.state != value:
        return False
    if state_class is not None and s.attributes.get("state_class") != state_class:
        return False
    return s


async def run_test():
    with tempfile.TemporaryDirectory(prefix="wem_issue14_ha_") as temp:
        conf = Path(temp)
        (conf / "configuration.yaml").write_text("homeassistant:\n  name: Isolated Issue 14 Test\n", encoding="utf-8")
        hass = HomeAssistant(str(conf))
        loader.async_setup(hass)
        # Real Home Assistant configuration, config entries and entity registry.
        configured = await bootstrap.async_from_config_dict({"homeassistant": {}}, hass)
        assert configured is hass, "Home Assistant Core bootstrap failed"
        LOG.info("Home Assistant Core initialized")

        try:
            # Use the public MQTT integration config flow, not injected .storage files.
            flow = await hass.config_entries.flow.async_init("mqtt", context={"source": "user"})
            LOG.info("MQTT config flow initial result: %s %s", flow["type"], flow.get("step_id"))
            assert flow["type"] == "form" and flow.get("step_id") == "broker", flow
            configured_mqtt = await hass.config_entries.flow.async_configure(
                flow["flow_id"], {
                    "broker": HOST,
                    "port": PORT,
                    "protocol": "5",
                    "other_settings": {
                        "set_client_cert": False,
                        "set_ca_cert": "off",
                        "transport": "tcp",
                    },
                }
            )
            assert configured_mqtt["type"] == "create_entry", configured_mqtt
            await wait_for(lambda: [entry for entry in hass.config_entries.async_entries("mqtt")
                                    if entry.state == ConfigEntryState.LOADED],
                           "MQTT integration loaded", 90)
            LOG.info("Actual Home Assistant MQTT integration loaded with local broker")

            # Simulate existing v1.1.2 retained config and state.
            await send(prepare_discovery(ns_old, events_old, INITIAL))
            await send([("wem/availability", "online", True),
                        ("wem/wem_lokal_info", {"WEM-Lokal Info": ns_old["build_clean_data"]({"Wärmepumpe": INITIAL})}, True)])
            old_ids = await wait_for(
                lambda: (ids if len(ids := sensor_entries(hass)) == len(KEYS) else None),
                "v1.1.2 HA entity registry entries", 85
            )
            LOG.info("v1.1.2 entity IDs: %s", old_ids)
            await wait_for(lambda: require_state(hass, old_ids["Verdichter"], "h", state_class="total_increasing"),
                           "v1.1.2 buggy hour-classified Verdichter", 40)
            await wait_for(lambda: require_state(hass, old_ids["Drehzahl Pumpe M1"], "%", value="55"),
                           "v1.1.2 unchanged pump speed", 40)
            await wait_for(lambda: require_state(hass, old_ids["Betriebsstd. Verdichter"], "h", value="4388"),
                           "v1.1.2 unchanged operating hours", 40)
            LOG.info("Baseline v1.1.2 Discovery was processed by Home Assistant")

            # Positive control: repair ONLY the simulated old sensor's MQTT
            # state, without changing its retained v1.1.2 discovery config.
            # This distinguishes a stale nonnumeric internal value from
            # a general v1.1.3 Discovery migration bug.
            if UPGRADE_SCENARIO.startswith("normalized_"):
                normalized = dict(INITIAL)
                normalized["Verdichter"] = "0"
                await send([("wem/wem_lokal_info",
                             {"WEM-Lokal Info": ns_old["build_clean_data"](
                                 {"Wärmepumpe": normalized}
                             )}, True)])
                await wait_for(
                    lambda: require_state(hass, old_ids["Verdichter"], "h",
                                          value="0", state_class="total_increasing"),
                    "normalized old h sensor state", 30
                )
                LOG.info("Positive control: old sensor now has valid numeric 0 (unit h)")
            else:
                LOG.info("Fault case: old sensor retains invalid 0 Hz (unit h)")

            # Explicitly record all unhandled asyncio task exceptions.
            # These must not silently pass merely because later states recover.
            loop = asyncio.get_running_loop()
            saved_handler = loop.get_exception_handler()
            unhandled = []

            def track_task_exception(loop, context):
                unhandled.append(context)
                if saved_handler is None:
                    loop.default_exception_handler(context)
                else:
                    saved_handler(loop, context)

            loop.set_exception_handler(track_task_exception)

            # Test both the existing CI ordering and the ACTUAL app ordering:
            # script.py publishes MQTT_STATE_TOPIC BEFORE publish_discovery().
            next_discovery = prepare_discovery(ns_new, events_new, INITIAL)
            next_state = [("wem/wem_lokal_info",
                           {"WEM-Lokal Info": ns_new["build_clean_data"](
                               {"Wärmepumpe": INITIAL}
                           )}, True)]
            if UPGRADE_SCENARIO.endswith("state_first"):
                LOG.info("Upgrade order: state BEFORE Discovery (matches app)")
                await send(next_state)
                await asyncio.sleep(0.35)
                await send(next_discovery)
                # In the real app the following regular polling cycle
                # publishes the next state AFTER corrected Discovery.
                # Without this, the original (unconvertible) v1.1.2
                # native value may remain cached indefinitely in the test.
                await asyncio.sleep(0.35)
                await send(next_state)
            else:
                LOG.info("Upgrade order: Discovery BEFORE state (control)")
                await send(next_discovery)
                await asyncio.sleep(0.35)
                await send(next_state)
            expected = {
                "Verdichter": ("Hz", INITIAL_EXPECTED_NUMERIC),
                "Soll Frequenz Verdichter": ("Hz", "0"),
                "Ist Frequenz Verdichter": ("Hz", "0"),
                "Drehzahl Pumpe M1": ("%", "55"),
                "Betriebsstd. Verdichter": ("h", "4388"),
            }
            for key, (unit, state) in expected.items():
                await wait_for(
                    lambda k=key, u=unit, v=state: require_state(
                        hass, old_ids[k], u, v, "total_increasing" if k == "Betriebsstd. Verdichter" else "measurement"
                    ),
                    f"v1.1.3 migration {key}={state} {unit}",
                    75,
                )
                LOG.info("Migrated %s: %s %s (%s)", key, state, unit, old_ids[key])

            await send([("wem/wem_lokal_info",
                         {"WEM-Lokal Info": ns_new["build_clean_data"]({"Wärmepumpe": RUNNING})}, True)])
            for key, expected_value in [
                ("Verdichter", "45"),
                ("Soll Frequenz Verdichter", "52"),
                ("Ist Frequenz Verdichter", "43"),
            ]:
                await wait_for(
                    lambda k=key, v=expected_value: require_state(hass, old_ids[k], "Hz", v, "measurement"),
                    f"running compressor frequency {key}={expected_value}", 50,
                )

            new_ids = sensor_entries(hass)
            assert old_ids == new_ids, f"Entity IDs changed: {old_ids} => {new_ids}"
            relevant = [e for e in er.async_get(hass).entities.values()
                        if e.platform == "mqtt" and e.unique_id in {
                            "wem_lokal_wp_" + ns_new["normalize_id"](k) for k in KEYS
                        }]
            assert len(relevant) == len(KEYS), f"Duplicate or missing entities: {relevant}"
            # Let queued entity update callbacks finish before classifying
            # the migration's exception behavior.
            await asyncio.sleep(1.0)
            errors = [str(context.get("exception") or context.get("message"))
                      for context in unhandled]
            LOG.info("UPGRADE_EXCEPTION_AUDIT scenario=%s count=%d details=%s",
                     UPGRADE_SCENARIO, len(errors), errors)
            # The experimental code emits numeric bare Verdichter Hz
            # in MQTT_STATE_TOPIC before Discovery during startup.
            # The deliberately impossible config-first stale-value control
            # should still reproduce the exception, but state-first must
            # now be free of unhandled exceptions.
            if UPGRADE_SCENARIO == "bad_config_first":
                expected_text = f"non-numeric value: '{INITIAL_COMPRESSOR}'"
                assert any(expected_text in error for error in errors), (
                    "Expected to reproduce the stale invalid MQTT state upgrade "
                    f"error, but got: {errors}"
                )
                LOG.warning(
                    "CONFIRMED: Upgrade with stale %s provokes unhandled "
                    "Home Assistant exception in scenario %s",
                    INITIAL_COMPRESSOR, UPGRADE_SCENARIO,
                )
            else:
                assert not errors, (
                    "Unexpected unhandled exception even after normalizing "
                    f"the old MQTT state first: {errors}"
                )
                LOG.info(
                    "SAFE UPGRADE PASS: no unhandled exception in scenario %s",
                    UPGRADE_SCENARIO,
                )
            LOG.info("SCENARIO COMPLETE: HA entity states correct; exceptions explicitly audited")
        finally:
            await hass.async_stop(force=True)


if __name__ == "__main__":
    _, ns_old, events_old = load_real_functions(OLD)
    _, ns_new, events_new = load_real_functions(NEW)
    asyncio.run(run_test())
