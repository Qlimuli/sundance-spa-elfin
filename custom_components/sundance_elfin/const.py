"""Constants for the Sundance Spa Elfin integration."""
from typing import Final

DOMAIN: Final = "sundance_elfin"

# Default port for Elfin WiFi-to-Serial adapter (TCP transparent mode)
DEFAULT_PORT: Final = 8899

# Connection settings
RECONNECT_INTERVAL: Final = 30
CONNECTION_TIMEOUT: Final = 10

# Temperature limits (Celsius)
MIN_TEMP: Final = 10.0
MAX_TEMP: Final = 40.0

# Temperature limits (Fahrenheit)
MIN_TEMP_F: Final = 50.0
MAX_TEMP_F: Final = 104.0

# Balboa Protocol Constants
MSG_DELIMITER: Final = 0x7E

# Message Types (from Balboa protocol)
MSG_TYPE_NEW_CLIENT_CTS: Final = 0x00
MSG_TYPE_CHANNEL_ASSIGN_REQ: Final = 0x01
MSG_TYPE_CHANNEL_ASSIGN_RESP: Final = 0x02
MSG_TYPE_CHANNEL_ASSIGN_ACK: Final = 0x03
MSG_TYPE_EXISTING_CLIENT_REQ: Final = 0x04
MSG_TYPE_EXISTING_CLIENT_RESP: Final = 0x05
MSG_TYPE_CTS: Final = 0x06
MSG_TYPE_NTS: Final = 0x07
MSG_TYPE_TOGGLE_ITEM: Final = 0x11
MSG_TYPE_STATUS_UPDATE: Final = 0x13
MSG_TYPE_SET_TEMP: Final = 0x20
MSG_TYPE_SET_TIME: Final = 0x21
MSG_TYPE_SETTINGS_REQ: Final = 0x22
MSG_TYPE_FILTER_CYCLES: Final = 0x23
MSG_TYPE_INFO_RESP: Final = 0x24
MSG_TYPE_PREFS_RESP: Final = 0x26
MSG_TYPE_SET_PREF: Final = 0x27
MSG_TYPE_FAULT_LOG: Final = 0x28
MSG_TYPE_CONFIG_RESP: Final = 0x2E

# Channels
CHANNEL_BROADCAST: Final = 0xFF
CHANNEL_MULTICAST: Final = 0xFE
CHANNEL_WIFI: Final = 0x0A

# Toggle Item Codes
ITEM_PUMP_1: Final = 0x04
ITEM_PUMP_2: Final = 0x05
ITEM_PUMP_3: Final = 0x06
ITEM_PUMP_4: Final = 0x07
ITEM_PUMP_5: Final = 0x08
ITEM_PUMP_6: Final = 0x09
ITEM_BLOWER: Final = 0x0C
ITEM_MISTER: Final = 0x0E
ITEM_LIGHT_1: Final = 0x11
ITEM_LIGHT_2: Final = 0x12
ITEM_AUX_1: Final = 0x16
ITEM_AUX_2: Final = 0x17
ITEM_TEMP_RANGE: Final = 0x50
ITEM_HEAT_MODE: Final = 0x51

# Settings Request Codes
SETTINGS_CONFIG: Final = 0x00
SETTINGS_FILTER_CYCLES: Final = 0x01
SETTINGS_INFO: Final = 0x02
SETTINGS_PREFS: Final = 0x08
SETTINGS_FAULT_LOG: Final = 0x20

# Heat Modes
HEAT_MODE_READY: Final = 0
HEAT_MODE_REST: Final = 1
HEAT_MODE_READY_IN_REST: Final = 3

# Heat States
HEAT_STATE_OFF: Final = 0
HEAT_STATE_HEATING: Final = 1
HEAT_STATE_HEAT_WAITING: Final = 2

# Temperature Ranges
TEMP_RANGE_LOW: Final = 0
TEMP_RANGE_HIGH: Final = 1

# Dispatcher signal for updates
UPDATE_SIGNAL: Final = f"{DOMAIN}_update"
