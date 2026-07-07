"""HTTP views exposed by the integration.

Endpoints (all under ``/api/home_intercom``):

* ``POST /record``            — receive PCM/WAV from the PWA, broadcast to speakers
* ``GET  /audio/{filename}``  — serve the recorded WAV (LAN speakers pull this)
* ``GET  /rooms/status``      — online / play_media capability per room
* ``GET  /version``           — frontend version + target PCM rate

``requires_auth`` is intentionally False so the PWA (served inside an HA iframe
without access to the hass auth token) can call them directly. The surface is
only reachable inside the user's LAN and the original standalone container had
the same open ``/record`` design, so this matches the threat model.

All blocking file I/O is delegated to the executor via
``hass.async_add_executor_job`` — HA 2026 aborts coroutines that do blocking
calls inside the event loop.
"""

import json as _json
import logging
import os
import wave

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PCM_RATE, PCM_BPS, WAV_MAGIC, WAV_HEADER_SIZE

_LOGGER = logging.getLogger(__name__)


def _write_audio(data: bytes, filepath: str, rate: int) -> float:
    """Blocking: write the incoming audio to a WAV file, return duration (s)."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    if data[: len(WAV_MAGIC)] == WAV_MAGIC:
        # WAV passthrough (e.g. ESP32 hardware button): write as-is.
        with open(filepath, "wb") as fh:
            fh.write(data)
        with wave.open(filepath, "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
        _LOGGER.info("[intercom] WAV passthrough %sB, %sHz", len(data), wf.getframerate())
    else:
        # Raw 16-bit mono PCM from the PWA: wrap into a WAV.
        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(PCM_BPS)
            wf.setframerate(rate)
            wf.writeframes(data)
        duration = len(data) / (rate * PCM_BPS)
        _LOGGER.info("[intercom] WAV written %sB, %.1fs, %sHz", len(data), duration, rate)
    return duration


def _read_audio(filepath: str):
    """Blocking: read a stored WAV into memory, or None if missing."""
    if not os.path.isfile(filepath):
        return None
    with open(filepath, "rb") as fh:
        return fh.read()


class RecordView(HomeAssistantView):
    url = "/api/home_intercom/record"
    name = "api:home_intercom:record"
    requires_auth = False

    async def post(self, request):
        hass = request.app["hass"]
        store = hass.data[DOMAIN]["store"]
        speaker = hass.data[DOMAIN]["speaker"]
        room_map = store["rooms"]

        target = request.query.get("target", "")
        if not target:
            return self.json({"ok": False, "error": "missing target"}, status_code=400)

        if target == "all":
            targets = [(k, v) for k, v in room_map.items() if v.get("entity")]
            if not targets:
                return self.json({"ok": False, "error": "no rooms configured"}, status_code=500)
        else:
            room = room_map.get(target)
            if not room or not room.get("entity"):
                return self.json(
                    {"ok": False, "error": f"unknown target: {target}"}, status_code=400
                )
            targets = [(target, room)]

        data = await request.read()
        if len(data) < WAV_HEADER_SIZE:
            return self.json({"ok": False, "error": "no audio data"}, status_code=400)

        filename = f"intercom_{target}.wav"
        filepath = os.path.join(store["audio_dir"], filename)

        rate = int(request.query.get("rate", PCM_RATE))
        # File I/O is blocking -> executor.
        duration = await hass.async_add_executor_job(_write_audio, data, filepath, rate)

        # Speakers pull the audio over the LAN => use HA's internal URL.
        base = (hass.config.internal_url or f"{request.scheme}://{request.host}").rstrip("/")
        audio_url = f"{base}/api/home_intercom/audio/{filename}"

        ok_count = 0
        errors = []
        for _key, tgt_room in targets:
            result = await speaker.play_and_auto_pause(
                tgt_room["entity"], audio_url, duration,
                play_method=tgt_room.get("play_method"),
            )
            if result["ok"]:
                ok_count += 1
            else:
                errors.append(
                    {
                        "entity": tgt_room["entity"],
                        "error": result.get("error", "unknown"),
                    }
                )

        name = room_map[target]["name"] if target != "all" else "全部"
        _LOGGER.info("[intercom] played on %d/%d rooms for %s", ok_count, len(targets), name)
        return self.json(
            {
                "ok": True,
                "name": name,
                "rooms_sent": ok_count,
                "rooms_total": len(targets),
                "errors": errors or None,
                "url": audio_url,
            }
        )


class AudioView(HomeAssistantView):
    url = "/api/home_intercom/audio/{filename}"
    name = "api:home_intercom:audio"
    requires_auth = False

    async def get(self, request, filename):
        hass = request.app["hass"]
        audio_dir = hass.data[DOMAIN]["store"]["audio_dir"]
        # Only the basename — guards against path traversal.
        filepath = os.path.join(audio_dir, os.path.basename(filename))
        data = await hass.async_add_executor_job(_read_audio, filepath)
        if data is None:
            return self.json_message("not found", status_code=404)
        return web.Response(
            body=data,
            content_type="audio/wav",
            headers={"Content-Disposition": f'inline; filename="{os.path.basename(filename)}"'},
        )


class StatusView(HomeAssistantView):
    url = "/api/home_intercom/rooms/status"
    name = "api:home_intercom:status"
    requires_auth = False

    async def get(self, request):
        hass = request.app["hass"]
        speaker = hass.data[DOMAIN]["speaker"]
        return self.json(speaker.query_statuses(hass.data[DOMAIN]["store"]["rooms"]))


class VersionView(HomeAssistantView):
    url = "/api/home_intercom/version"
    name = "api:home_intercom:version"
    requires_auth = False

    async def get(self, request):
        from .const import VERSION

        return self.json({"version": VERSION, "pcm_rate": PCM_RATE})


class ConfigView(HomeAssistantView):
    """Read/write room configuration from the PWA settings panel."""

    url = "/api/home_intercom/config"
    name = "api:home_intercom:config"
    requires_auth = False

    async def get(self, request):
        """Return current rooms + playable media_player entities."""
        hass = request.app["hass"]
        store = hass.data[DOMAIN]["store"]
        rooms = store["rooms"]

        PLAY_MEDIA = 512  # MediaPlayerEntityFeature
        reg = er.async_get(hass)
        entities = []
        for state in hass.states.async_all("media_player"):
            eid = state.entity_id
            if eid.startswith("sensor."):
                continue

            platform = None
            if reg:
                ent_entry = reg.async_get(eid)
                if ent_entry:
                    platform = ent_entry.platform

            sf = state.attributes.get("supported_features", 0)
            # Playable if: xiaomi platform (send_command) OR has PLAY_MEDIA flag
            playable = (platform and ("miot" in platform or platform == "xiaomi")) or bool(sf & PLAY_MEDIA)

            entities.append({
                "entity_id": eid,
                "name": state.attributes.get("friendly_name", eid),
                "state": state.state,
                "platform": platform,
                "playable": playable,
            })

        # Sort: playable first, then by name
        entities.sort(key=lambda e: (not e["playable"], e["name"] or ""))

        return self.json({"ok": True, "rooms": rooms, "entities": entities})

    async def post(self, request):
        """Save rooms configuration."""
        hass = request.app["hass"]
        data = await request.json()
        rooms = data.get("rooms", {})

        if not isinstance(rooms, dict) or not rooms:
            return self.json(
                {"ok": False, "error": "至少需要一个房间"},
                status_code=400,
            )
        for key, room in rooms.items():
            if not room.get("entity"):
                return self.json(
                    {"ok": False, "error": f"房间「{key}」缺少音箱实体"},
                    status_code=400,
                )

        # Update in-memory store (immediate effect).
        store = hass.data[DOMAIN]["store"]
        store["rooms"] = rooms

        # Persist to www/rooms.json (for PWA).
        www_dir = os.path.join(os.path.dirname(__file__), "www")
        rooms_path = os.path.join(www_dir, "rooms.json")

        def _write(rp: str, r: dict):
            with open(rp, "w", encoding="utf-8") as fh:
                _json.dump(r, fh, indent=2, ensure_ascii=False)

        await hass.async_add_executor_job(_write, rooms_path, rooms)

        # Also persist to config_entry.options (survives restarts).
        entry = hass.data[DOMAIN].get("entry")
        if entry is not None:
            new_opts = {**entry.options, "rooms": rooms}
            hass.config_entries.async_update_entry(entry, options=new_opts)

        _LOGGER.info(
            "[intercom] rooms updated via PWA settings: %d rooms", len(rooms)
        )
        return self.json({"ok": True, "rooms": rooms})
