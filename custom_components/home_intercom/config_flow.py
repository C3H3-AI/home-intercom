"""Config flow for Home Intercom.

Rooms and their speaker entities are configured through the Options flow
(Configuration → Integrations → 家庭广播 → Options). The data is persisted
in ``config_entry.options`` and also written to ``www/rooms.json`` so the
frontend PWA can read it directly.
"""

from __future__ import annotations

import json
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# ——— Step IDs ———
STEP_ROOMS = "rooms"

# ——— Default rooms when none have been configured ———
DEFAULT_ROOMS = {
    "living": {
        "name": "Living Room",
        "name_en": "Living Room",
        "entity": "media_player.living_room_speaker",
    },
    "xiaomi_pro": {
        "name": "Xiaomi Pro",
        "name_en": "Xiaomi Pro",
        "entity": "media_player.xiaomi_pro_speaker",
        "play_method": "xiaomi_miot",
    },
}


def _rooms_json_schema(current: dict) -> vol.Schema:
    """Build a schema with a JSON-editor textarea for the room list."""
    pretty = json.dumps(current or DEFAULT_ROOMS, indent=2, ensure_ascii=False)
    return vol.Schema(
        {
            vol.Required("rooms_json", default=pretty): selector.TextSelector(
                selector.TextSelectorConfig(
                    multiline=True,
                    type=selector.TextSelectorType.TEXT,
                )
            ),
        }
    )


class HomeIntercomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial (bare) config flow — no configuration needed."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="家庭广播", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))


class HomeIntercomOptionsFlow(config_entries.OptionsFlow):
    """Handle the Options flow — rooms configuration."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        """First (and only) options step: edit the rooms JSON."""
        if user_input is not None:
            raw = user_input.get("rooms_json", "{}")
            try:
                rooms = json.loads(raw)
            except json.JSONDecodeError as exc:
                return self.async_show_form(
                    step_id="init",
                    data_schema=_rooms_json_schema(
                        self.config_entry.options.get("rooms", DEFAULT_ROOMS)
                    ),
                    errors={"rooms_json": f"JSON 格式错误: {exc}"},
                )
            if not isinstance(rooms, dict) or not rooms:
                return self.async_show_form(
                    step_id="init",
                    data_schema=_rooms_json_schema(
                        self.config_entry.options.get("rooms", DEFAULT_ROOMS)
                    ),
                    errors={"rooms_json": "必须至少定义一个房间"},
                )
            return self.async_create_entry(
                title="",
                data={**self.config_entry.options, "rooms": rooms},
            )

        current = self.config_entry.options.get("rooms", DEFAULT_ROOMS)
        return self.async_show_form(
            step_id="init",
            data_schema=_rooms_json_schema(current),
            description_placeholders={
                "example": json.dumps(DEFAULT_ROOMS, indent=2, ensure_ascii=False)
            },
        )


async def async_get_options_flow(config_entry: config_entries.ConfigEntry):
    return HomeIntercomOptionsFlow()
