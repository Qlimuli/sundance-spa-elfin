"""Constants for the Sundance Spa Elfin integration."""
from typing import Final

DOMAIN: Final = "sundance_elfin"

# Default port for Elfin WiFi-to-Serial adapter
DEFAULT_PORT: Final = 8899

# Connection settings
RECONNECT_INTERVAL: Final = 30

# Temperature limits (Celsius)
MIN_TEMP: Final = 10.0
MAX_TEMP: Final = 40.0
