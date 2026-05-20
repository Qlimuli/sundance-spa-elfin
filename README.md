# Sundance Spa (Elfin WiFi) - Home Assistant Integration

Home Assistant integration for Sundance Spas using the Balboa WiFi protocol via Elfin-EW11A (or similar) WiFi-to-Serial adapter.

Based on [pybalboa](https://github.com/natekspencer/pybalboa) library.

## Features

- Climate control (temperature, heat mode)
- Pump control (on/off)
- Light control
- Filter cycle sensors
- Heating state sensor

## Requirements

- Sundance Spa with Balboa control system
- Elfin-EW11A WiFi-to-Serial adapter (or compatible)
- The adapter must be configured to connect to the spa's RS485 bus

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click "Integrations"
3. Click the three dots menu and select "Custom repositories"
4. Add this repository URL and select "Integration" as the category
5. Click "Add"
6. Search for "Sundance Spa" and install it
7. Restart Home Assistant
8. Go to Settings > Devices & Services > Add Integration
9. Search for "Sundance Spa" and follow the setup wizard

### Manual Installation

1. Download the `custom_components/sundance_elfin` folder
2. Copy it to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant
4. Add the integration via Settings > Devices & Services

## Configuration

During setup, you'll need:

- **IP Address**: The IP address of your Elfin-EW11A adapter
- **Port**: The TCP port (default: 8899 for Elfin adapters)

## Elfin-EW11A Setup

The Elfin adapter needs to be configured in TCP Server mode:

1. Connect to the Elfin's WiFi AP or access its web interface
2. Set the serial parameters:
   - Baud Rate: 115200
   - Data Bits: 8
   - Stop Bits: 1
   - Parity: None
3. Set the network mode to "TCP Server"
4. Note the IP address and port

## Credits

- [pybalboa](https://github.com/natekspencer/pybalboa) - Python library for Balboa spa communication
- [balboa_worldwide_app](https://github.com/ccutrer/balboa_worldwide_app) - Protocol documentation
- [bwalink](https://github.com/jshank/bwalink) - Reference implementation
