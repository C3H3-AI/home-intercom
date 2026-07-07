"""Constants for the Home Intercom integration."""

DOMAIN = "home_intercom"
VERSION = "1.0.0"

# ——— PCM / WAV handling ———
PCM_RATE = 16000  # target sample rate (Hz) for Xiaomi speaker WAV output
PCM_BPS = 2  # 16-bit audio = 2 bytes per sample
WAV_MAGIC = b"RIFF"
WAV_HEADER_SIZE = 44  # RIFF(12) + fmt(24) + data(8) = minimum valid WAV header

# ——— Auto-pause tuning ———
STATE_POLL_INTERVAL = 0.5  # poll interval for state checks (seconds)
PLAYING_CONFIRM_RETRIES = 10  # max attempts to confirm "playing" (10 x 0.5s = 5s)
PAUSE_RETRIES = 5  # pause retry count

# MediaPlayerEntityFeature bits (from HA core media_player/const.py).
# Used to decide the auto-stop strategy for each speaker.
SUPPORT_REPEAT_SET = 1 << 18  # = 262144 — modernity proxy (MA/HomePod/Chromecast)
SUPPORT_PLAY_MEDIA = 1 << 9  # = 512 — entity can call play_media at all

# Shared status / error string constants. These MUST match the values used by
# the frontend (intercom.html: EntityStatus / PlayError) so JSON round-trips
# map to the right dot colours and status text.
class EntityStatus:
    ONLINE = "online"
    UNAVAILABLE = "unavailable"
    NO_PLAY_MEDIA = "no_play_media"


class PlayError:
    PLAY_FAILED = "play_failed"
    MA_FAILED = "ma_failed"
