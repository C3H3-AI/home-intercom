"""Home Intercom — native Home Assistant integration.

Turns a phone PWA (served inside an HA sidebar iframe) into a family broadcast
system: hold to talk -> the recording is pushed to selected speakers. Replaces
the standalone home-intercom container so there is no reverse proxy / TLS /
sub-domain to maintain, and no HA_TOKEN to manage.
"""

import json
import logging
import os

from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .speaker import Speaker
from .views import AudioView, ConfigView, RecordView, StatusView, VersionView

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "home_intercom"
STATIC_URL = "/api/home_intercom/www"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "www")


def _load_rooms(path: str):
    """Blocking: read rooms.json. Runs inside an executor job."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


async def _write_rooms(path: str, rooms: dict) -> None:
    """Blocking: write rooms.json. Runs inside an executor job."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rooms, fh, indent=2, ensure_ascii=False)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    pause_buffer = float(
        entry.options.get("pause_buffer", entry.data.get("pause_buffer", 0.0)) or 0.0
    )

    rooms_path = os.path.join(STATIC_DIR, "rooms.json")
    # Prefer rooms from options (set via the Options UI). Fall back to the
    # on-disk www/rooms.json (legacy) and finally an empty dict.
    rooms = entry.options.get("rooms")
    if rooms is None:
        try:
            rooms = await hass.async_add_executor_job(_load_rooms, rooms_path)
        except (OSError, ValueError) as err:
            _LOGGER.error("[intercom] failed to load rooms.json: %s", err)
            rooms = {}

    # Sync options back to www/rooms.json so the PWA can read it.
    await hass.async_add_executor_job(_write_rooms, rooms_path, rooms)

    audio_dir = hass.config.path(".storage", "home_intercom", "audio")
    # os.makedirs is blocking — run in executor.
    await hass.async_add_executor_job(os.makedirs, audio_dir, 0o755, True)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["store"] = {"rooms": rooms, "audio_dir": audio_dir}
    hass.data[DOMAIN]["speaker"] = Speaker(hass, pause_buffer=pause_buffer)
    hass.data[DOMAIN]["entry"] = entry  # for ConfigView → config_entry.options

    # Register static assets (intercom.html, static/*, rooms.json, manifest).
    # Guarded: on HA reload the path may already be registered.
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, STATIC_DIR, cache_headers=False)]
        )
    except Exception as err:  # already registered on reload
        _LOGGER.debug("[intercom] static path registration skipped: %s", err)

    hass.http.register_view(RecordView())
    hass.http.register_view(AudioView())
    hass.http.register_view(StatusView())
    hass.http.register_view(VersionView())
    hass.http.register_view(ConfigView())

    # Sidebar iframe panel -> the PWA.
    try:
        await async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title="家庭广播",
            sidebar_icon="mdi:broadcast",
            frontend_url_path=PANEL_URL_PATH,
            config={"url": f"{STATIC_URL}/intercom.html"},
            require_admin=False,
        )
    except Exception as err:  # panel may already exist on reload
        _LOGGER.debug("[intercom] panel registration skipped: %s", err)

    # Listen for options updates (rooms edited in Options UI).
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called when the user saves options via the Options UI."""
    rooms = entry.options.get("rooms")
    if rooms is None:
        _LOGGER.debug("[intercom] options updated without rooms — skipping")
        return

    # Persist to www/rooms.json so the PWA reads the latest config.
    rooms_path = os.path.join(STATIC_DIR, "rooms.json")
    await hass.async_add_executor_job(_write_rooms, rooms_path, rooms)

    # Also update the in-memory store used by the backend views.
    store = hass.data.get(DOMAIN, {}).get("store")
    if store is not None:
        store["rooms"] = rooms
        _LOGGER.info("[intercom] rooms updated via options: %d rooms", len(rooms))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].pop("speaker", None)
    hass.data[DOMAIN].pop("store", None)
    return True
