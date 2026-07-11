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

## Sundance Cameo 880 – Hinweise

- Steuerbefehle werden als **Panel-Tastendrücke** (CC-Nachrichten) auf RS485-Kanal `0x10` gesendet, sobald der Spa **Clear-To-Send** meldet.
- **Temperatur setzen:** Warmer/Cooler (225/226) mit Retry-Schleife; bei Bedarf Temperaturbereich Low/High (200/201).
- **Licht:** An/Aus und Farbmodus per Feedback (241/242), analog zum Sundance-780-Referenzprojekt.
- **40-Ampere-Modelle (Cameo):** Pumpen 1 und 2 werden vor Temperaturänderungen automatisch ausgeschaltet (Heiz-/Panel-Logik).
- **Temperatur-Sperre am Panel** deaktivieren, sonst werden Warmer/Cooler ignoriert.
- Die Soll-Temperatur wird ab Raw-Wert 80 als °F, darunter als halbe °C-Schritte dekodiert (Cameo 880).

## Fehlerbehebung

| Symptom | Maßnahme |
|--------|----------|
| Soll-Temperatur falsch (z. B. 41 °C statt 28 °C) | Integration ≥ 1.2.0 installieren (Dekodierung korrigiert) |
| Temperatur lässt sich nicht ändern | Temperatur-Sperre am Panel prüfen; Pumpen dürfen bei 40 A nicht laufen |
| Licht reagiert nicht | Licht am Panel einmal manuell testen; ggf. 2-Stunden-Automatik beachten |
| Pumpen funktionieren, Rest nicht | Home Assistant neu starten; nur **eine** TCP-Verbindung zum EW11 (Port 8899) |

## Voraussetzungen

- RS485-TCP-Bridge zum Spa-Controller (Balboa-Protokoll)
- Home Assistant 2023.1 oder neuer

## Support

- [Dokumentation / Original-Projekt](https://github.com/HyperActiveJ/sundance780-jacuzzi-balboa-rs485-tcp)
- [Issues](https://github.com/HyperActiveJ/sundance780-jacuzzi-balboa-rs485-tcp/issues)
