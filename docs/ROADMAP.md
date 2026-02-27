# Roadmap

## Ziele und Leitplanken

Diese Roadmap konsolidiert die bisherigen Planungsartefakte aus `plans/` in eine dauerhafte, wartbare Referenz.

Leitplanken:
- Keine Breaking Changes an bestehenden öffentlichen Endpunkten.
- Stabilität und API-Konsistenz haben Vorrang vor Feature-Breite.
- UI-Verbesserungen sind additiv und kompatibel zu bestehenden Pfaden.

## Stabilitäts- und Qualitätsziele

1. Einheitliche Alarm-Transitionslogik (`ack`, `resolve`, `cancel`) für single und bulk.
2. Kanonische Notes-Implementierung auf `POST /v1/alarms/{alarm_id}/notes`.
3. Funktionale Simulation-Endpunkte unter `/v1/simulation/*`.
4. Grüne Qualitätsgates: `ruff check .` und `pytest -q`.
5. Konsistente Dokumentationsstruktur unter `docs/`.

## Umsetzungsphasen

## Phase 0: Baseline und Sicherheitsnetz
- Lint- und Test-Baseline erfassen.
- API- und UI-Regressionen über dedizierte Tests absichern.

## Phase 1: Repository- und Doku-Cleanup
- Planungsartefakte aus `plans/` in `docs/` konsolidieren.
- `docs/README.md` als kanonischen Index pflegen.
- Temporäre/duplizierte Doku entfernen, operative Kerndoku behalten.

## Phase 2: API-Konsistenz und Deduplizierung
- Doppelte Routen und redundante Handler eliminieren.
- Gemeinsame Helper für Zustandsübergänge und Bulk-Verarbeitung nutzen.
- Response-Semantik für ähnliche Endpunkte vereinheitlichen.

## Phase 3: Simulation reparieren
- Notifications-, Status-, Clear- und Seed-Endpunkte stabilisieren.
- Disabled-Mode bewusst fail-closed (404) behandeln.
- Kanalzählungen und Statusausgaben testbar machen.

## Phase 4: Service-Refactoring
- Trigger-/Notification-/Worker-Logik in klarere Teilverantwortungen schneiden.
- Event-Dispatch normalisieren und Altpfade schrittweise reduzieren.

## Phase 5: Engineering-QoL
- Ruff-Schulden abbauen (Importe, lange Zeilen, ungenutzte Symbole).
- Typing und Signaturen konsolidieren.
- Logging-Konventionen und Settings-Zugriff vereinheitlichen.

## Phase 6: UI-Verbesserungen
- Admin-UI: robuste Quick-Actions, Fehlermeldungen, Suche, Modal-Details.
- ACK-UI: klare Statusdarstellung, verbesserte Guidance bei bereits bearbeiteten Alarmen.
- Simulation-Panel im Admin-UI für Status/Clear/Seed-Hinweise.

## Phase 7: QA und Abnahme
- Regressionstests für Notes, Simulation, Alarm-Lifecycle und UI-Basiselemente.
- Lint/Test/Smoke als Merge-Voraussetzung.

## Aktueller Fokus (laufender Integrationszyklus)

- API-Konsistenz und Simulation-Stabilität priorisiert.
- UI-Härtung für Admin/ACK mit Fokus auf Fehlerresistenz.
- Dokumentationskonsolidierung abgeschlossen halten und künftig nur in `docs/` planen.

## Backlog (nach Stabilitätsfokus)

1. Weiteres internes Refactoring großer Module (`alarms.py`, `notification_service.py`, `trigger_service.py`, `worker/tasks.py`) in kleinere Unterbausteine.
2. Erweiterte Such-/Filteroptionen für Admin-Operationen.
3. Zusätzliche UX-Verbesserungen (z. B. bessere leere Zustände, feinere Bulk-Rückmeldungen).

## Definition of Done für den Umbau

- `plans/` entfernt und Inhalte in `docs/` integriert.
- Einheitliche Notes-Route und stabile Simulation-Endpunkte mit Tests.
- UI-Flows ohne bekannte Laufzeitfehler in den Kernpfaden.
- Vollständiger grüner Qualitätssatz (Lint + Tests).
