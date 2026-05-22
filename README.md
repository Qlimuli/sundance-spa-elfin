# Sundance / Balboa Spa – Home Assistant Integration

Home-Assistant-Integration für Sundance-/Balboa-Whirlpools über einen RS485-TCP-Adapter (z. B. ESPHome, ser2net).

## Installation über HACS

1. In HACS: **Integrations** → Menü (⋮) → **Benutzerdefinierte Repositories**
2. Repository-URL eintragen und Typ **Integration** wählen
3. Integration **Sundance / Balboa Spa** installieren
4. Home Assistant neu starten
5. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** nach „Sundance“ suchen

## Manuelle Installation

Kopiere den Ordner `custom_components/sundance_spa` nach:

```
/config/custom_components/sundance_spa
```

Danach Home Assistant neu starten.

## Konfiguration

| Feld | Standard | Beschreibung |
|------|----------|--------------|
| Host | – | IP des RS485-TCP-Adapters |
| Port | 8899 | TCP-Port des Adapters |

## Entitäten

- **Climate** – Thermostat (Soll-/Ist-Temperatur)
- **Switch** – Pumpe 1/2, Auto-Zirkulation, ClearRay UV
- **Light** – RGB-Licht mit Effektmodi
- **Sensor** – Temperaturen, Heizmodus, Uhrzeit, Lichtstatus

## Voraussetzungen

- RS485-TCP-Bridge zum Spa-Controller (Balboa-Protokoll)
- Home Assistant 2023.1 oder neuer

## Support

- [Dokumentation / Original-Projekt](https://github.com/HyperActiveJ/sundance780-jacuzzi-balboa-rs485-tcp)
- [Issues](https://github.com/HyperActiveJ/sundance780-jacuzzi-balboa-rs485-tcp/issues)
