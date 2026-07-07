"""Speaker playback with three-tier auto-stop.

Ported from the original ``ha_client.py`` (mdj2812/home-intercom). The original
talked to HA over the REST API with a Bearer token. Inside an integration we
already have the ``hass`` object, so we drop the HTTP client, the entity cache
and the background threads entirely:

* state / attributes come straight from ``hass.states.get`` (cheap, sync, no I/O)
* service calls use ``hass.services.async_call``
* the auto-pause timer is an ``asyncio`` coroutine scheduled on HA's loop

The three tiers are preserved 1:1:

1. Music Assistant (``app_id == "music_assistant"``) -> ``play_announcement``
2. Modern player (supports ``repeat_set``) -> ``play_media(announce=True)``
3. Basic player (e.g. Xiaomi) -> ``play_media(announce=True)`` + timed pause
"""

import asyncio
import logging
import time

from .const import (
    EntityStatus,
    PlayError,
    STATE_POLL_INTERVAL,
    PAUSE_RETRIES,
    PLAYING_CONFIRM_RETRIES,
    SUPPORT_PLAY_MEDIA,
    SUPPORT_REPEAT_SET,
)

_LOGGER = logging.getLogger(__name__)


class Speaker:
    """Wraps media_player (and music_assistant) playback via the hass object."""

    def __init__(self, hass, pause_buffer: float = 0.0):
        self.hass = hass
        self._pause_buffer = pause_buffer

    # ——— helpers ———

    def _entity_info(self, entity_id: str) -> dict:
        """Read app_id + supported_features from current state (no API call)."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return {"app_id": "", "supported_features": 0}
        attrs = state.attributes
        return {
            "app_id": attrs.get("app_id", ""),
            "supported_features": attrs.get("supported_features", 0),
        }

    @staticmethod
    def _has_play_media(info: dict) -> bool:
        return bool(info["supported_features"] & SUPPORT_PLAY_MEDIA)

    # ——— public API (mirrors original HAClient interface) ———

    async def play_and_auto_pause(
        self, entity_id: str, audio_url: str, duration: float,
        play_method: str | None = None,
    ) -> dict:
        """Play audio on one entity, choosing the right auto-stop tier.

        ``play_method`` is passed from the room config. Known values:
        ``"xiaomi_miot"`` — use ``send_command(player_play_url)`` instead of
        ``play_media`` (Xiaomi MIoT entities don't respect play_media URLs).

        Returns ``{"ok": True}`` or ``{"ok": False, "error": ...}``.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state == EntityStatus.UNAVAILABLE:
            return {"ok": False, "error": EntityStatus.UNAVAILABLE}

        if play_method == "xiaomi_miot":
            return await self._play_xiaomi_miot(entity_id, audio_url)

        info = self._entity_info(entity_id)
        if info["app_id"] == "music_assistant":
            return await self._play_ma_announcement(entity_id, audio_url)
        return await self._play_standard(entity_id, audio_url, duration, info)

    async def _play_ma_announcement(self, entity_id: str, audio_url: str) -> dict:
        _LOGGER.info("[intercom] %s MA player — play_announcement", entity_id)
        ok = await self.hass.services.async_call(
            "music_assistant",
            "play_announcement",
            {"entity_id": entity_id, "url": audio_url},
            blocking=True,
        )
        if ok:
            return {"ok": True}
        return {"ok": False, "error": PlayError.MA_FAILED}

    async def _play_xiaomi_miot(self, entity_id: str, audio_url: str) -> dict:
        """Play audio via xiaomi_miot.send_command(method=player_play_url).
        
        Xiaomi MIoT entities do not advertise the PLAY_MEDIA feature flag
        (and the play_control proxy entity hijacks play_media to play cloud
        tracks instead of our URL). The only reliable path is raw miio
        ``player_play_url``.
        """
        _LOGGER.info("[intercom] %s xiaomi_miot — player_play_url", entity_id)
        try:
            result = await self.hass.services.async_call(
                "xiaomi_miot",
                "send_command",
                {
                    "entity_id": entity_id,
                    "method": "player_play_url",
                    "params": [{"url": audio_url, "type": 0}],
                },
                blocking=True,
            )
        except Exception as exc:
            _LOGGER.error(
                "[intercom] %s xiaomi_miot send_command failed: %s",
                entity_id,
                exc,
            )
            return {"ok": False, "error": PlayError.PLAY_FAILED}
        _LOGGER.info("[intercom] %s player_play_url accepted: %s", entity_id, result)
        return {"ok": True}

    async def _play_standard(
        self, entity_id: str, audio_url: str, duration: float, info: dict
    ) -> dict:
        # NOTE: some integrations (e.g. Xiaomi/miio) accept ``play_media`` for
        # URL streaming but do NOT advertise the PLAY_MEDIA feature bit. We no
        # longer hard-reject on the feature flag — we attempt the call and let
        # HA's service result decide. A missing bit is only a soft warning.
        if not self._has_play_media(info):
            _LOGGER.warning(
                "[intercom] %s does not advertise play_media (features=0x%s) "
                "— attempting play_media anyway",
                entity_id,
                format(info["supported_features"], "x"),
            )

        modern = bool(info["supported_features"] & SUPPORT_REPEAT_SET)
        _LOGGER.info(
            "[intercom] %s modern=%s (features=0x%s)",
            entity_id,
            modern,
            format(info["supported_features"], "x"),
        )

        try:
            ok = await self.hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": entity_id,
                    "media_content_id": audio_url,
                    "media_content_type": "music",
                    "announce": True,
                },
                blocking=True,
            )
        except Exception as exc:
            _LOGGER.error(
                "[intercom] %s play_media failed: %s",
                entity_id,
                exc,
            )
            return {"ok": False, "error": PlayError.PLAY_FAILED}
        if not ok:
            return {"ok": False, "error": PlayError.PLAY_FAILED}

        if modern:
            _LOGGER.info("[intercom] %s modern player — self-stopping", entity_id)
            return {"ok": True}

        # Basic player: schedule a timer that pauses after the clip ends.
        self.hass.async_create_task(self._auto_pause(entity_id, duration))
        return {"ok": True}

    async def _auto_pause(self, entity_id: str, wait_sec: float) -> None:
        """Confirm playback -> wait remaining duration + buffer -> pause."""
        t0 = time.monotonic()

        for _ in range(PLAYING_CONFIRM_RETRIES):
            state = self.hass.states.get(entity_id)
            if state is not None and state.state == "playing":
                _LOGGER.info("[intercom] %s playing confirmed", entity_id)
                break
            await asyncio.sleep(STATE_POLL_INTERVAL)
        else:
            _LOGGER.info("[intercom] %s short clip, polling missed 'playing'", entity_id)

        elapsed = time.monotonic() - t0
        remaining = max(0.0, wait_sec - elapsed + self._pause_buffer)
        if remaining > 0:
            _LOGGER.info(
                "[intercom] %s sleeping %.1fs (buffer +%.1fs)",
                entity_id,
                remaining,
                self._pause_buffer,
            )
            await asyncio.sleep(remaining)

        for attempt in range(1, PAUSE_RETRIES + 1):
            await self.hass.services.async_call(
                "media_player",
                "media_pause",
                {"entity_id": entity_id},
                blocking=False,
            )
            await asyncio.sleep(STATE_POLL_INTERVAL)
            state = self.hass.states.get(entity_id)
            if state is None or state.state != "playing":
                _LOGGER.info("[intercom] %s paused (attempt %d)", entity_id, attempt)
                return
            _LOGGER.info(
                "[intercom] %s still playing, retry pause (%d/%d)",
                entity_id,
                attempt,
                PAUSE_RETRIES,
            )
        _LOGGER.warning(
            "[intercom] %s may still be playing after %d retries",
            entity_id,
            PAUSE_RETRIES,
        )

    def query_statuses(self, room_map: dict) -> dict[str, str]:
        """Batch query speaker status for all rooms.

        Returns EntityStatus values: ``online`` / ``unavailable``.
        (Play capability is verified at broadcast time via the service call,
        not by the feature flag — Xiaomi/miio omits the PLAY_MEDIA bit.)
        """
        status: dict[str, str] = {}
        for key, room in room_map.items():
            entity = room.get("entity", "")
            if not entity:
                status[key] = EntityStatus.ONLINE
                continue
            state = self.hass.states.get(entity)
            if state is None or state.state == EntityStatus.UNAVAILABLE:
                status[key] = EntityStatus.UNAVAILABLE
                continue
            status[key] = EntityStatus.ONLINE
        return status
