"""Constants for the Sundance Spa Elfin integration."""
from typing import Final

DOMAIN: Final = "sundance_elfin"

# Configuration
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"

DEFAULT_PORT: Final = 8899
DEFAULT_NAME: Final = "Sundance Spa"

# Connection settings
RECONNECT_INTERVAL: Final = 30  # seconds
CONNECTION_TIMEOUT: Final = 10  # seconds
READ_TIMEOUT: Final = 5  # seconds

# Temperature limits (Celsius)
MIN_TEMP: Final = 26.0
MAX_TEMP: Final = 40.0
TEMP_STEP: Final = 0.5

# Protocol constants - These are placeholder values
# You will need to update these with actual hex codes from your spa
PACKET_START: Final = 0x7E
PACKET_END: Final = 0x7E

# Message types (placeholders - update with real values)
MSG_STATUS: Final = 0x13
MSG_SET_TEMP: Final = 0x20
MSG_TOGGLE_PUMP1: Final = 0x04
MSG_TOGGLE_PUMP2: Final = 0x05
MSG_TOGGLE_LIGHT: Final = 0x11

# Status byte positions (placeholders - update with real values)
POS_CURRENT_TEMP: Final = 5
POS_TARGET_TEMP: Final = 6
POS_HEATING_STATE: Final = 7
POS_PUMP1_STATE: Final = 8
POS_PUMP2_STATE: Final = 9
POS_LIGHT_STATE: Final = 10

# Entity unique ID prefixes
CLIMATE_UNIQUE_ID: Final = "climate"
PUMP1_UNIQUE_ID: Final = "pump1"
PUMP2_UNIQUE_ID: Final = "pump2"
LIGHT_UNIQUE_ID: Final = "light"
TEMP_SENSOR_UNIQUE_ID: Final = "temperature"
CONNECTION_SENSOR_UNIQUE_ID: Final = "connection"

# Platforms
PLATFORMS: Final = ["climate", "switch", "light", "sensor"]
