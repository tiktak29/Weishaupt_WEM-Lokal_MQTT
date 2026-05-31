# Weishaupt WEM‑Lokal → MQTT  
Lokale Auslesung von Weishaupt WEM‑Wärmepumpen über das integrierte Web‑Interface mit MQTT‑Integration und vollständiger Home‑Assistant‑Discovery – komplett ohne Cloud.

---

## Überblick
Diese Home‑Assistant App (vorher Add‑on) liest alle relevanten Daten einer **Weishaupt Wärmepumpe** direkt über das **lokale Web‑Interface** der Steuerung aus und stellt sie über **MQTT** bereit.  
Alle Geräte und Sensoren werden automatisch per **MQTT Discovery** in Home Assistant angelegt. 

✔ Keine Cloud  
✔ Keine API‑Keys  
✔ Keine Abhängigkeiten von Weishaupt‑Servern  
✔ 100% lokal, schnell und stabil

---

## Funktionen

### **Lokale Kommunikation**
- Zugriff direkt über das WEM‑Web‑Interface  
- Keine Internetverbindung erforderlich  
- Robuste HTTP‑Fehlerbehandlung  
- Automatische Wiederanmeldung bei Session‑Timeout

### **MQTT Discovery**
Automatische Erstellung aller Geräte und Sensoren in Home Assistant:

- Wärmepumpe  
- Heizkreis 1  
- Heizkreis 2  
- Statistik  
- Zweite WEZ  
- Tagesstatistik  
- Modell‑Erkennung  
- Dynamische Geräteaktivierung  
- MQTT API v1/v2 kompatibel  

### **Stabilität & Zuverlässigkeit**
- Retry‑System bei leeren Antworten  
- Session Recovery  
- Round‑Robin‑Abfrage (schont die WEM‑Weboberfläche)  
- Optionaler Last‑Will (online/offline)

---

## Voraussetzungen
- Weishaupt WEM‑System mit lokalem Web‑Interface  
- Home Assistant  
- MQTT‑Broker (z. B. Mosquitto)  

---

## Installation

### **1. Repository hinzufügen**
In Home Assistant:

**Einstellungen → Apps → App installieren → ⋮ → Repositories**

Repository‑URL: https://github.com/tiktak29/Weishaupt_WEM-Lokal_MQTT

### **2. App installieren**
- „Weishaupt WEM‑Lokal MQTT“ auswählen  
- Installieren
- Konfigurieren  
- Starten  
- Logs prüfen  

---

## Konfiguration

### **Optionen (config.json)**

| Parameter | Beschreibung |
|----------|--------------|
| `webinterface_ip_address` | IP Steuerung |
| `webinterface_username` | Benutzername |
| `webinterface_password` | Passwort |
| `webinterface_hex_code` | HEX‑Code aus der URL Webinterface |
| `mqtt_broker` | MQTT‑Broker (z. B. core-mosquitto) |
| `mqtt_port` | Port |
| `mqtt_username` | Benutzer |
| `mqtt_password` | Passwort |
| `polling_seconds` | Abfrageintervall |
| `enable_wp` | Wärmepumpe |
| `enable_hk1` | Heizkreis 1 |
| `enable_hk2` | Heizkreis 2 |
| `enable_stats` | Statistik |
| `enable_wez2` | 2. WEZ |

---

## Unterstützte Geräte

- Wärmepumpe  
- Heizkreis 1  
- Heizkreis 2  
- Statistik  
- Zweite WEZ  
- Tagesstatistik  

Alle Sensoren werden automatisch per MQTT Discovery angelegt.

---

## Changelog

Siehe: [CHANGELOG.md](./CHANGELOG.md)

---

## Lizenz

Dieses Projekt verwendet die **MIT‑Lizenz**.  
Siehe: [LICENSE](./LICENSE)

---

## Hinweis

Dies ist ein **inoffizielles Open‑Source‑Projekt**.  
Es besteht **keine Verbindung zur Weishaupt GmbH**.

---

## Danke

Danke an alle, die dieses Projekt testen, verbessern und erweitern.  
Feedback sind willkommen!


