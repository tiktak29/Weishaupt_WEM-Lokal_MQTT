<p align="center" style="padding-top: 40px;">
  <img src="wem_mqtt_unified/logo.png" alt="WEM Lokal MQTT Logo" style="max-width: 450px; width: 18%; height: auto;">
</p>

# Weishaupt WEM‑Lokal → MQTT  

![Version](https://img.shields.io/badge/version-v1.0.0-blue)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-App-green)
![MQTT](https://img.shields.io/badge/MQTT-Discovery-orange)
![License](https://img.shields.io/badge/license-MIT-brightgreen)  

Lokale Auslesung von Weishaupt Wärmepumpen über das integrierte Web‑Interface der Weishaupt Regelung mit MQTT‑Integration und vollständiger Home‑Assistant‑Discovery – komplett ohne Cloud.

#### Getestet mit einer Weishaupt WAB 14 (EC WAB V5.3 R10 / WWP-SG V5.0)

---

## Überblick
Diese Integration fungiert als lokales Abfrage-Gateway, das die lokale Weishaupt WEM-Weboberfläche ausliest und die Daten über MQTT Discovery an Home Assistant bereitstellt.  
Alle Geräte und Sensoren werden automatisch per **MQTT Discovery** in Home Assistant angelegt.
<br>
#### Weishaupt Web UI → HTTP scraping → Python Gateway → data normalization → MQTT Broker → Discovery → Home Assistant  
Entwickelt, um Weishaupt Systeme ohne offiziellen API-Zugang in moderne Home Assistant-Umgebungen zu integrieren.

---
 
✔ Keine Cloud  
✔ Keine API‑Keys  
✔ Keine Abhängigkeiten von Weishaupt‑Servern  
✔ 100% lokal

---

## Voraussetzungen
- Weishaupt Wärmepumpe mit aktueller Software
- Web-Interface: Webserver muss in der Regelung aktiviert sein (vor Installation der App sicherstellen, siehe unten)
- Web-Interface: Benutzer und Passwort sind angelegt (vor Installation der App sicherstellen, siehe unten)
- Web-Interface: 4-stelliger HEX-Code ist bekannt (vor Installation der App sicherstellen, siehe unten) 
- Home Assistant  
- MQTT‑Broker (z. B. Mosquitto)  

---

## Funktionen

### Lokale Kommunikation
- Direkter Zugriff auf die Weishaupt-Regelung über das integrierte Web-Interface  
- Keine Cloud-Anbindung erforderlich  
- Keine API-Schlüssel oder Herstellerkonten notwendig  
- Keine Internetverbindung für den Betrieb erforderlich  
- Direkte Kommunikation innerhalb des lokalen Netzwerks  

### Home Assistant Integration
- Vollständige MQTT Discovery  
- Automatische Erstellung aller Geräte und Sensoren  
- Automatische Gerätezuordnung in Home Assistant  
- Automatische Aktualisierung von Sensoren und Entitäten  
- Unterstützung für aktuelle und ältere MQTT-Bibliotheken (paho-mqtt API v1/v2)  

### Unterstützte Geräte
- Wärmepumpe  
- Heizkreis 1  
- Heizkreis 2  
- Statistik  
- 2.WEZ  

### Zuverlässigkeit
- Automatische Wiederanmeldung bei Session-Verlust  
- Retry-Mechanismus bei leeren oder unvollständigen Antworten  
- Robuste HTTP-Fehlerbehandlung  
- Automatische Wiederherstellung nach Kommunikationsfehlern  
- Round-Robin-Abfrage zur Entlastung des Web-Interfaces  

### Erweiterte Funktionen
- Automatische Modell-Erkennung der Wärmepumpe 
- Dynamische Aktivierung einzelner Gerätebereiche  
- Tägliche Kommunikations- und Erfolgsstatistik im Log  
- Optionaler MQTT Availability-/Last-Will-Status (online/offline)  
- Vollständig lokaler Betrieb ohne externe Abhängigkeiten  

![Dashboard](images/dashboard.jpg)

---

## Installation

### **1. Repository hinzufügen**
In Home Assistant:

**Einstellungen → Apps → App installieren → ⋮ → Repositories → Hinzufügen → URL eingeben**

Repository‑URL: https://github.com/tiktak29/Weishaupt_WEM-Lokal_MQTT

### **2. App installieren**
- „Weishaupt WEM‑Lokal MQTT“ auswählen  
- Installieren
- Konfigurieren  
- Starten  
- Logs prüfen  

![Log beim Start](images/startup-log.jpg)

---

## Konfiguration

### **Optionen (config.json)**

| Parameter | Beschreibung |
|----------|--------------|
| `webinterface_ip_address` | IP-Adresse Web-Interface |
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
| `enable_wez2` | 2.WEZ |

![Konfiguration 1](images/config-1.jpg)
![Konfiguration 2](images/config-2.jpg)

---
## Wichtiger Hinweis

⚠️ Während die App läuft sollte nicht gleichzeitig über einen Webbrowser auf das Web-Interface zugegriffen werden.

Parallele Datenabfragen können das Web-Interface der Wärmepumpe instabil machen.

In Einzelfällen kann dies dazu führen, dass die Regelung nicht mehr reagiert und die Wärmepumpe erst nach einem Neustart der Steuerung wieder in Betrieb geht und erreichbar ist.

Daher wird empfohlen, während des Betriebs der App keine zusätzlichen Browserzugriffe auf das Web-Interface durchzuführen.

---

## 1. Web-Interface aktivieren
[Anleitung im Home Assistant Community Forum](https://community.home-assistant.io/t/weishaupt-heatpump-integration-via-modbus/436823/210?page=13)
<br><br>
## 2. Web-Interface Benutzer und Kennwort anlegen
Lokale IP (Beispiel: http://192.168.178.xx) der Wärmepumpe im Browser aufrufen, Benutzer anlegen und Passwort vergeben
<br><br>
## 3. Web-Interface 4-stellige HEX-Zahl ermitteln
Benutzeroberfläche vom Web-Interface im Browser öffnen.  
Auswählen: Profimodus → Info → Heizkreis 1
Oben im Browser wird die URL angezeigt.  
Beispiel: http://192.168.178.89/settings_export.html?stack=0C00000100000000008000HHHH010002000301,0C000C1900000000000000HHHH020003000401  
Im ersten und zweiten Block HHHH ist der 4-stellige HEX-Code.  

Der 4-stellige HEX-Code muss in beiden URL-Blöcken identisch sein und wird in der App-Konfiguration als webinterface_hex_code eingetragen.

![HEX-Code Beispiel](images/hex-code-example.jpg)

---

## Changelog

Siehe: [CHANGELOG.md](./CHANGELOG.md)

---

## Lizenz

Dieses Projekt verwendet die **MIT‑Lizenz**.  
Siehe: [LICENSE](./LICENSE)

---

## Danke

Danke an alle, die dieses Projekt testen, verbessern und erweitern.  
Feedback und Verbesserungsvorschläge sind willkommen.

---

<br><br>
  
##  Haftungsausschluss
Dieses Projekt ist ein unabhängiges Open-Source-Projekt und steht in keiner Verbindung zur Weishaupt GmbH.

Die App wurde in der Freizeit auf Basis öffentlich zugänglicher Informationen entwickelt.  
Die Nutzung erfolgt auf eigenes Risiko und in eigener Verantwortung.  
Für Schäden oder Fehlfunktionen, die durch die Nutzung dieser App entstehen, übernehmen die Entwickler keinerlei Haftung.

---

