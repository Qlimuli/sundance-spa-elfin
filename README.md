# Sundance Spa (Elfin/EW11 WiFi) - Home Assistant Integration

Home Assistant integration for Sundance Spas (including Cameo 880) using the native Balboa RS485 protocol via EW11/Elfin WiFi-to-Serial adapter.

**Version 2.0** - Complete rewrite with native Balboa protocol implementation (no external dependencies).

## Supported Models

- Sundance Cameo 880
- Sundance 780 Series
- Other Sundance/Jacuzzi spas with Balboa BP-series controllers

## Features

- **Climate Control**: Temperature setting, heat mode (Ready/Rest/Ready-in-Rest)
- **Pump Control**: Toggle pumps 1-3 (supports multi-speed pumps)
- **Light Control**: Toggle spa lights
- **Sensors**: 
  - Water temperature
  - Target temperature
  - Spa time
  - Connection status
- **Binary Sensors**:
  - Heating active
  - Circulation pump running
  - Filter cycle active

## Requirements

- Sundance Spa with Balboa control system
- EW11 or Elfin-EW11A WiFi-to-RS485 adapter connected to the spa's IOT port
- The adapter **MUST** be configured in TCP Server (transparent) mode, **NOT** Modbus mode

## Important: EW11 Configuration

Your scan shows the EW11 is currently configured as a Modbus TCP gateway. This will **NOT** work with this integration. The Balboa spa uses a proprietary RS485 protocol, not Modbus.

### Required EW11 Settings

1. Access your EW11 web interface (http://192.168.178.54)
2. Go to **Serial Port Settings**:
   - Baud Rate: **115200**
   - Data Bits: **8**
   - Stop Bits: **1**
   - Parity: **None**
   - Flow Control: **None**

3. Go to **Network Settings**:
   - Work Mode: **TCP Server** (NOT Modbus TCP Server!)
   - Local Port: **8899**
   - Max Connections: 1

4. **Disable Modbus**: Make sure Modbus TCP is disabled. The EW11 should act as a transparent TCP-to-RS485 bridge.

5. Save and restart the EW11

## Wiring

Connect to the spa's RS485 bus (IOT port on Balboa controllers):

| EW11 Pin | Spa Pin | Description |
|----------|---------|-------------|
| A (TX-/RX-) | Pin 3 | RS-485 A |
| B (TX+/RX+) | Pin 2 | RS-485 B |
| GND | Pin 4 | Ground/Return |

**Note**: Pin 1 on the spa connector is +12V - do not connect this to the EW11 data pins!

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click "Integrations"
3. Click the three dots menu and select "Custom repositories"
4. Add `https://github.com/Qlimuli/v0-sundance-spa-elfin` and select "Integration"
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

During setup, enter:

- **IP Address**: The IP of your EW11 adapter (e.g., `192.168.178.54`)
- **Port**: TCP port (default: `8899`)

## Troubleshooting

### "Cannot connect" error

1. **Check EW11 mode**: Must be TCP Server, not Modbus TCP
2. **Verify port**: Default is 8899
3. **Test connection**: `nc -v 192.168.178.54 8899` should connect
4. **Check wiring**: Ensure RS485 A/B are connected correctly

### No data received

1. The spa may need a few seconds to start sending status updates
2. Check that the EW11 serial settings match (115200/8/N/1)
3. Try swapping RS485 A and B wires

### Incorrect temperature readings

The spa sends temperatures in its configured unit (Celsius or Fahrenheit). The integration auto-detects this from the status messages.

## Protocol Reference

This integration implements the Balboa BP-series RS485 protocol:
- Message delimiter: `0x7E`
- CRC-8: Polynomial 0x07, Init 0x02, XOR 0x02
- Status updates: Message type `0x13` at ~3.3 Hz
- Commands use toggle-style messages (type `0x11`)

Based on documentation from:
- [balboa_worldwide_app Wiki](https://github.com/ccutrer/balboa_worldwide_app/wiki)
- [pybalboa](https://github.com/natekspencer/pybalboa)

## Credits

- [balboa_worldwide_app](https://github.com/ccutrer/balboa_worldwide_app) - Protocol documentation
- [pybalboa](https://github.com/natekspencer/pybalboa) - Reference implementation
- [HyperActiveJ/sundance780-jacuzzi-balboa-rs485-tcp](https://github.com/HyperActiveJ/sundance780-jacuzzi-balboa-rs485-tcp) - Sundance-specific adaptations

## License

MIT License
