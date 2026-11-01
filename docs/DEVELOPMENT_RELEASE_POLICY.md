# Entwicklungs- und Release-Richtlinie – WEM-Lokal MQTT

**Status:** Verbindliche Entwicklungs- und Release-Richtlinie ab Aufnahme in `main`  
**Repository:** https://github.com/tiktak29/Weishaupt_WEM-Lokal_MQTT  
**Repository-Pfad:** `docs/DEVELOPMENT_RELEASE_POLICY.md`  
**Stand:** 01.11.2026  
**Gültigkeit:** Ab Aufnahme in `main` für neue Entwicklungsarbeiten, Bugfixes und Releases ab v1.1.4; bestehende Releases werden nicht rückwirkend geändert.

## 1. Ziele und Grundsätze

1. `main` ist der stabile, veröffentlichungsfähige Hauptbranch. Änderungen werden normalerweise in eigenen, kurzlebigen Entwicklungsbranches über Pull Requests (PR) nach `main` eingebracht.
2. **Konservative Entwicklung:** Bestehenden Normalpfad erhalten; Änderungen nur nach belegter Analyse, mit kleinem, begründetem Umfang und überprüfbaren Tests.
3. **WebIF-Betriebsschutz:** Niemals durch Tests oder Diagnose einen parallelen Browser-/WebIF-Zugriff auf die produktiv genutzte Anlage auslösen. CI muss ausschließlich mit isolierten Testdaten, lokalen Mocks/Containern oder ausdrücklich freigegebenen Testsystemen arbeiten. Keine Verbindungen zur realen Weishaupt-Anlage oder Home Assistant durch GitHub Actions.
4. Änderungen am Repository, an Workflows, an Releases, an Hardware oder Home Assistant nur nach ausdrücklicher Freigabe. Analyse und Entwurf allein sind keine Freigabe zur Ausführung.
5. `main`, Release-Tags und bereits veröffentlichte Releases werden nicht stillschweigend umgeschrieben. Keine Force-Pushes oder Tag-Verschiebungen als Normalverfahren.
6. Jede Freigabe bezieht sich auf konkrete Änderungen und einen nachvollziehbaren Commit-Stand.

## 2. Branch- und PR-Verfahren

1. Vor Entwicklungsbeginn die aktuellen Referenzen (`main`-SHA, letzter Release-Tag, relevante offene PRs) prüfen.
2. Neuen Branch **von aktuellem `main`** anlegen, z. B. `fix/issue-xx-kurzbeschreibung-v1.1.4`, `feature/...` oder `docs/...`. Ein Branchname ist keine dauerhafte Workflow-Abhängigkeit.
3. Änderung auf die betroffenen Funktionen und Dateien begrenzen. Nicht angeforderte Refactorings vermeiden.
4. PR nach `main` erstellen. Im PR dokumentieren: Fehler/Ursache bzw. Ziel, Änderungsscope, Risiken, Testnachweise, Auswirkungen auf Entitäten/MQTT, Rückwärtskompatibilität und ggf. Rollback.
5. **Vor dem Merge** müssen die erforderlichen PR-Checks erfolgreich sein; der getestete **Head-SHA** bzw. GitHubs geprüfter Merge-Stand muss zum freizugebenden PR-Stand passen. Neue Commits nach den Tests erfordern eine erneute Prüfung.
6. Bei Squash-Merge beachten: Die ursprünglichen Einzelcommits erscheinen nicht einzeln in `main`. Bei dokumentationswürdiger Entwicklungs-Historie vor dem Löschen einen Archivbedarf entscheiden.
7. Merge erst nach ausdrücklicher Freigabe. Direktänderungen auf `main` sind grundsätzlich zu vermeiden.

## 3. GitHub Actions – Trigger und Lebenszyklus

### 3.1 Dauerhafte Workflows

Für dauerhaft benötigte, versionsübergreifende Checks grundsätzlich beide Ereignisse vorsehen:

```yaml
on:
  pull_request:
    branches:
      - main
  push:
    branches:
      - main
  workflow_dispatch:
```

- `pull_request.branches` bezeichnet den **Zielbranch**, nicht den Quellbranch. Das ermöglicht Tests unterschiedlicher Entwicklungsbranches vor dem Merge.
- `push.branches: [main]` prüft den integrierten Stand nach dem Merge.
- `workflow_dispatch` ist optional, aber für gezielte Nachprüfungen hilfreich; bei manuellem Start die ausgewählte Referenz dokumentieren.
- `paths`-Filter dürfen notwendige Tests nicht unbemerkt auslassen. Bei erforderlichen Branch-Protection-Checks können ausgelassene pfadgefilterte Workflows zu wartenden Checks führen; Strategie vor Aktivierung prüfen.
- Tests müssen bei PR-Läufen **tatsächlich den PR-Kandidaten**, bei `main`-Läufen **tatsächlich den `main`-Commit** auschecken. `actions/checkout`-Referenzen, `with: ref:`, relative Pfade und Umgebungsvariablen kontrollieren.

### 3.2 Issuespezifische Workflows

- Für einen einmaligen Bugfix dürfen zusätzliche, eng begrenzte Tests erstellt werden. Diese werden ausdrücklich als issuespezifisch dokumentiert.
- Vor dem Merge entscheiden: **in dauerhafte Regressionstests überführen**, **nur manuell verfügbar lassen** oder **später nach Freigabe entfernen**.
- Keine verwaisten `push`-Trigger auf bereits gelöschte Feature-Branches als langfristigen Standard belassen.
- Änderungen an Test-Triggern sind unabhängig von der App-Funktion zu prüfen; sie verändern zwar nicht direkt das Polling, können aber die Qualitätssicherung schwächen.

### 3.3 Aktuell bekannter Ausgangszustand (01.11.2026)

Auf `main` befinden sich fünf für Issue #14 / v1.1.3 entwickelte Workflows:

- `.github/workflows/issue14-docker-smoke.yml`
- `.github/workflows/issue14-first-aus.yml`
- `.github/workflows/issue14-ha-core-integration.yml`
- `.github/workflows/issue14-release-audit.yml`
- `.github/workflows/issue14-v113-regression.yml`

Zum Prüftermin enthalten alle einen automatischen `push`-Trigger auf den inzwischen gelöschten Branch `fix/issue-14-verdichter-hz-v1.1.3` und außerdem `workflow_dispatch`. **Damit lösen diese Workflows bei einem normalen `push` auf `main` nicht automatisch aus.** Das ist ein dokumentierter Ausgangsbefund, keine Aufforderung, sie ungeprüft umzuschalten. Die Tests sind teils fest auf v1.1.3 und den Vergleich mit v1.1.2 zugeschnitten; vor jeder Verallgemeinerung sind ihre Testannahmen einzeln zu prüfen.

## 4. Versionen, Referenzen und Testqualität

1. Verweise auf frühere Tags (z. B. `v1.1.2`) darauf prüfen, ob sie eine **bewusst unveränderte Regression-Baseline** oder eine versehentlich veraltete Vorgabe sind. Historische Baselines dürfen sinnvoll bleiben; sie werden nicht automatisch auf die neueste Version gesetzt.
2. Kein Test darf durch falschen Checkout, fest codierten Branch, unpassenden Trigger oder Pfadfilter unbemerkt gegen den falschen Code laufen.
3. Jede erforderliche CI-Ausführung dokumentieren: Workflow, Event (`pull_request`/`push`/manuell), Ergebnis, Head-SHA, ggf. Merge-SHA, verwendete Baseline und Testumfang.
4. Für Änderungen an MQTT-Discovery, Einheiten, Parsern und Entitäten zusätzlich Kompatibilität bestehender Entity-IDs, numerische Zustände, Zustandsklassen, `Aus`/`unknown`, Hz/rpm/h und MQTT-Retained-Verhalten prüfen, **soweit vom Änderungsscope betroffen**.
5. Änderungen an WebIF, HTTP, Sessions, Polling, Retries, Recovery oder Scheduler erfordern gesonderte Ursachenanalyse und ausdrücklich passende Tests. Keine zusätzlichen WebIF-Aufrufe ohne belegten Bedarf.
6. Fehlgeschlagene, übersprungene oder nicht gestartete erforderliche Checks sind keine erfolgreichen Checks. Ursache klären; Ausnahmen einzeln begründen und freigeben.
7. Grün allein genügt nicht: Es muss zusätzlich nachgewiesen sein, **welcher Commit**, **welcher Codepfad** und **welcher Trigger** geprüft wurden.

## 5. Release-Vorbereitung und Abnahme

### A. Vor dem PR-Merge

- [ ] Änderungsbedarf und Ursache nachvollziehbar dokumentiert; Scope eingegrenzt.
- [ ] Basisbranch `main` und aktueller Head-SHA geprüft.
- [ ] PR zeigt ausschließlich beabsichtigte Änderungen; keine versehentlichen Dateien oder Abhängigkeiten.
- [ ] Bestehendes Normalverhalten und WebIF-Betriebsschutz bewertet.
- [ ] CI-Trigger decken PR mit Ziel `main` ab; relevante Pfadfilter geprüft.
- [ ] Alle erforderlichen PR-Tests ausgeführt und erfolgreich.
- [ ] Resultate gehören zum zuletzt freizugebenden PR-Stand; keine ungetesteten Nachträge.
- [ ] Dokumentation, Changelog, Migrations- und MQTT-Kompatibilität geprüft.
- [ ] Ausdrückliche Merge-Freigabe erteilt.

### B. Unmittelbar nach dem Merge

- [ ] PR-Status tatsächlich `merged`; Merge-Verfahren und Merge-Commit-SHA festgehalten.
- [ ] `main` zeigt auf den erwarteten Commit; Datei-/Tree-Vergleich gegen freigegebenen Kandidaten, soweit sinnvoll.
- [ ] Erforderliche `push`-Checks auf `main` wurden ausgelöst und sind erfolgreich; bei Ausnahmen dokumentierte Ersatzprüfung und Freigabe.
- [ ] Workflows enthalten keine unbeabsichtigten dauerhaften Trigger auf kurzlebige Branches.
- [ ] Versionsdateien, insbesondere `wem_mqtt_unified/config.json`, `wem_mqtt_unified/info.md`, README und relevante Changelogs, sind konsistent.

### C. Vor dem Release

- [ ] Release-Kandidat ist genau der geprüfte `main`-Stand; SHA notiert.
- [ ] Tag-Name und Tag-Ziel sind richtig; keine Überschreibung eines bestehenden Tags.
- [ ] App-Versionsnummer in `config.json` stimmt mit Release und Dokumentation überein.
- [ ] Testnachweise, offene Risiken und erforderliche reale Hardware-Bestätigung sind dokumentiert.
- [ ] Release-Notes beschreiben echte Änderungen und Upgrade-Hinweise korrekt; keine doppelte Überschrift und keine unzutreffende Aussage wie „Issue behoben“, wenn Praxistest noch aussteht.
- [ ] Release explizit zur Veröffentlichung freigegeben.

### D. Nach der Veröffentlichung

- [ ] Tag-SHA, Release-Sichtbarkeit und veröffentlichte Assets/Quellarchive geprüft.
- [ ] `main` und Tag auf dem beabsichtigten Code-Stand; keine unerwarteten weiteren Änderungen.
- [ ] Offene Issues bleiben offen, bis deren Abschluss fachlich begründet ist; reale Rückmeldungen auswerten.
- [ ] Entwicklungsbranch erst nach vollständiger Übernahme, Referenzprüfung und **separater Löschfreigabe** entfernen.
- [ ] Nach einer Löschung `main`, verbleibende Branches und Tags erneut lesen und vergleichen.
- [ ] Dauerhafte CI-Workflows für die nächste Entwicklungsrunde korrekt triggerbar; issuespezifische Testartefakte gezielt archivieren oder später separat bereinigen.

## 6. Änderungs- und Freigabegrenzen

- **Lesen und prüfen**: Repository, Branches, PRs, Tags, Workflows, Logs und Dokumentation dürfen im jeweils freigegebenen Umfang analysiert werden.
- **Schreiben**: Keine Commits, PRs, Workflow-/Dateiänderungen, Branch-Löschungen, Releases oder Issue-Statusänderungen ohne ausdrückliche Freigabe für die konkrete Aktion.
- **Produktive Anlagen**: Keine aktiven Verbindungen, Steuerungen, Neustarts oder Parallelabfragen auf WebIF/Home Assistant allein zur CI-Prüfung.
- **Abweichungen**: Technisch notwendige Ausnahmen transparent begründen, vorab abstimmen und dokumentieren.

## 7. Anwendung dieser Richtlinie

Vor Beginn einer neuen Entwicklung wird die Richtlinie herangezogen und gegen den **tatsächlich aktuellen** Repository-Zustand geprüft. Die Richtlinie ersetzt keine Einzelprüfung und keine Freigabe. Sie wird im Repository selbst über einen eigenen Branch und Pull Request geändert; ältere Releases bleiben unverändert.

---

**Inkrafttreten:** Mit dem genehmigten Merge dieser Richtlinie in `main`.