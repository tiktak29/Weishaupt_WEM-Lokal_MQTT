"""Isolated Home Assistant Core test: first WebIF 'Verdichter = Aus' then Hz/rpm.

Two distinct modes:
 - no_rediscovery: what the app does when all device_ready flags are true
 - repeat_discovery: conditional path if initial synchronization remains active

A missing correct unit is a HARD TEST FAILURE, not a passing known limitation.
Test only. Do not touch the app's WebIF, discovery, or polling implementation.
"""
import asyncio
import logging
import os
from pathlib import Path
import tempfile

from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

import test_issue14_ha_core as helpers
from test_issue14_v113 import load_real_functions, NEW

LOG = logging.getLogger("wem_issue14_off_first")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

MODE = os.environ.get("WEM_OFF_FIRST_MODE", "")
NEXT_RAW = os.environ.get("WEM_OFF_FIRST_NEXT", "")
assert MODE in ("no_rediscovery", "repeat_discovery"), MODE
assert NEXT_RAW in ("45 Hz", "2163 rpm"), NEXT_RAW
EXPECTED_UNIT = "Hz" if NEXT_RAW.endswith(" Hz") else "rpm"
EXPECTED_STATE = "45" if EXPECTED_UNIT == "Hz" else "2163"

_, ns, events = load_real_functions(NEW)
DEVICE_ID = "wem_lokal_wp_" + ns["normalize_id"]("Verdichter")
CONFIG_TOPIC = "homeassistant/sensor/" + DEVICE_ID + "/config"

def payload(raw):
    return {"Wärmepumpe": {
        "Außengerät Variante": "WSB 6" if EXPECTED_UNIT == "Hz" else "WAB 14",
        "Verdichter": raw,
    }}

def discovery(raw):
    events.clear()
    ns["publish_discovery"](payload(raw))
    found = [x for x in events if x[0] == CONFIG_TOPIC]
    assert len(found) == 1, found
    return list(events), found[0][1]

async def publish_state(raw):
    await helpers.send([(
        "wem/wem_lokal_info",
        {"WEM-Lokal Info": ns["build_clean_data"](payload(raw))},
        True
    )])

def find_entity(hass):
    matches = [e for e in er.async_get(hass).entities.values()
               if e.platform == "mqtt" and e.unique_id == DEVICE_ID]
    if not matches:
        return None
    assert len(matches) == 1, f"Duplicate entities: {matches}"
    return matches[0].entity_id

async def main():
    LOG.info("START mode=%s first=Aus then=%s", MODE, NEXT_RAW)
    exceptions = []
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()

    def capture(loop, context):
        exceptions.append(str(context.get("exception") or context.get("message")))
        if old_handler:
            old_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(capture)
    try:
        with tempfile.TemporaryDirectory(prefix="wem_offfirst_") as folder:
            path = Path(folder)
            (path / "configuration.yaml").write_text(
                "homeassistant:\n  name: Off First Discovery Test\n",
                encoding="utf-8"
            )
            hass = HomeAssistant(str(path))
            loader.async_setup(hass)
            initialized = await bootstrap.async_from_config_dict(
                {"homeassistant": {}}, hass
            )
            assert initialized is hass, "Home Assistant did not initialize"
            try:
                flow = await hass.config_entries.flow.async_init(
                    "mqtt", context={"source": "user"}
                )
                assert flow["type"] == "form" and flow["step_id"] == "broker", flow
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"], {
                        "broker": helpers.HOST,
                        "port": helpers.PORT,
                        "protocol": "5",
                        "other_settings": {
                            "set_client_cert": False,
                            "set_ca_cert": "off",
                            "transport": "tcp",
                        },
                    }
                )
                assert result["type"] == "create_entry", result
                await helpers.wait_for(
                    lambda: [c for c in hass.config_entries.async_entries("mqtt")
                             if c.state == ConfigEntryState.LOADED],
                    "real MQTT integration loaded", 90
                )

                # Actual order: MQTT_STATE_TOPIC first, then MQTT Discovery.
                await helpers.send([("wem/availability", "online", True)])
                await publish_state("Aus")
                first_msgs, first_cfg = discovery("Aus")
                await helpers.send(first_msgs)
                first_entity = await helpers.wait_for(
                    lambda: find_entity(hass), "initial bare Verdichter sensor", 60
                )
                await helpers.wait_for(
                    lambda: hass.states.get(first_entity)
                    if (hass.states.get(first_entity) is not None
                        and hass.states.get(first_entity).state == "0")
                    else None,
                    "Aus converted into state 0", 35
                )
                first = hass.states.get(first_entity)
                LOG.info("INITIAL sensor=%s state=%s unit=%r state_class=%r",
                         first_entity, first.state,
                         first.attributes.get("unit_of_measurement"),
                         first.attributes.get("state_class"))
                assert first.attributes.get("unit_of_measurement") is None
                assert first_cfg.get("unit_of_measurement") is None
                assert first_cfg.get("state_class") is None

                # Next round: normal state MQTT publish. If discovery_enabled
                # was disabled after first completed round, no new config is sent.
                await publish_state(NEXT_RAW)
                if MODE == "repeat_discovery":
                    next_msgs, next_cfg = discovery(NEXT_RAW)
                    LOG.info("SECOND_DISCOVERY unit=%r class=%r",
                             next_cfg.get("unit_of_measurement"),
                             next_cfg.get("state_class"))
                    assert next_cfg.get("unit_of_measurement") == EXPECTED_UNIT
                    assert next_cfg.get("state_class") == "measurement"
                    await helpers.send(next_msgs)
                    # Simulate next polling cycle so HA reprocesses new template.
                    await asyncio.sleep(0.3)
                    await publish_state(NEXT_RAW)
                else:
                    LOG.info("NO_REDISCOVERY: simulating discovery_enabled=False")

                await helpers.wait_for(
                    lambda: hass.states.get(first_entity)
                    if hass.states.get(first_entity) is not None
                    and (hass.states.get(first_entity).state == EXPECTED_STATE
                         if MODE == "repeat_discovery"
                         else hass.states.get(first_entity).state in (
                             EXPECTED_STATE, NEXT_RAW
                         )) else None,
                    "new raw state processed", 40
                )
                final = hass.states.get(first_entity)
                last_id = find_entity(hass)
                final_unit = final.attributes.get("unit_of_measurement")
                final_class = final.attributes.get("state_class")
                await asyncio.sleep(0.8)
                LOG.info("FINAL mode=%s raw=%s entity=%s state=%s unit=%r "
                         "class=%r exceptions=%s",
                         MODE, NEXT_RAW, last_id, final.state,
                         final_unit, final_class, exceptions)
                assert first_entity == last_id, "Entity ID changed unexpectedly"
                assert final.state == EXPECTED_STATE, (
                    f"MQTT sensor value wrong for {NEXT_RAW}: {final.state!r}"
                )
                assert final_unit == EXPECTED_UNIT, (
                    f"FIRST-AUS-DISCOVERY GAP: expected {EXPECTED_UNIT!r} "
                    f"but Home Assistant still has {final_unit!r} after "
                    f"{NEXT_RAW}; mode={MODE}"
                )
                assert final_class == "measurement", final_class
                assert not exceptions, (
                    f"Unhandled Home Assistant exceptions on transition: {exceptions}"
                )
                LOG.info("PASS_FIRST_AUS mode=%s transition=%s retained unit=%s",
                         MODE, NEXT_RAW, final_unit)
            finally:
                await hass.async_stop(force=True)
    finally:
        loop.set_exception_handler(old_handler)

if __name__ == "__main__":
    asyncio.run(main())
