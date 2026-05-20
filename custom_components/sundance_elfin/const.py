"""Constants for the Sundance Spa Elfin integration."""
from typing import Final

DOMAIN: Final = "sundance_elfin"

# Configuration
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"

DEFAULT_PORT: Final = 8899
DEFAULT_NAME: Final = "Sundance Spa"

# Connection settings
RECONNECT_INTERVAL: Final = 30
CONNECTION_TIMEOUT: Final = 10

# Temperature limits (Celsius)
MIN_TEMP: Final = 26.0
MAX_TEMP: Final = 40.0
TEMP_STEP: Final = 0.5

# Entity unique ID prefixes
CLIMATE_UNIQUE_ID: Final = "climate"
PUMP1_UNIQUE_ID: Final = "pump1"
PUMP2_UNIQUE_ID: Final = "pump2"
LIGHT_UNIQUE_ID: Final = "light"
TEMP_SENSOR_UNIQUE_ID: Final = "temperature"
CONNECTION_SENSOR_UNIQUE_ID: Final = "connection"

# Platforms
PLATFORMS: Final = ["climate", "switch", "light", "sensor"]
