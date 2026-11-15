# E-Rechnung — Selbst gehostete ZUGFeRD-App

Kleines, kostenloses Tool, mit dem du eine ganz normale Rechnung schreibst und
automatisch eine **E-Rechnung** daraus bekommst: ein Hybrid-PDF, das visuell
aussieht wie dein gewohntes Layout und zusätzlich das maschinenlesbare
**ZUGFeRD / factur-x / EN-16931**-XML eingebettet enthält. Damit erfüllst du die
ab 2027 geltende B2B-E-Rechnungspflicht in Deutschland.

> **Für wen?** Freiberufler, kleine Unternehmen, Agenturen. Jeder betreibt seine
> eigene Instanz auf dem eigenen Rechner oder Server — **niemand sieht die
> Rechnungen eines anderen**.

## Was macht die App?

- Rechnung über ein Web-Formular erfassen (Positionen, Steuer, Empfänger, …)
- Automatisch PDF **und** ZUGFeRD-XML erzeugen, XML ins PDF einbetten
- Rechnungsliste, Einzel-Downloads (PDF oder XML)
- **Eigene Rechnungsvorlage pro Instanz** — Logo, Farben, Layout anpassbar
- **GoBD-Konformität**: SHA-256-Hash-Chain über alle Rechnungen, Kettenprüfung,
  Audit-Log, ZIP-Export mit Verfahrensdokumentation
- Alles **lokal**: SQLite, Dateisystem, keine Cloud

## Schnellstart (5 Minuten, für dich oder deinen Onkel)

Voraussetzung: **Docker** (unter Windows/macOS: Docker Desktop).

```bash
git clone <dein-fork> erechnung
cd erechnung
```

Firmendaten eintragen in `docker-compose.yml` (die Zeilen unter `environment:`):
Name, Adresse, USt-IdNr., Steuernummer, IBAN, BIC.

Dann:

```bash
docker compose up -d --build
```

Öffne `http://localhost:8080` im Browser. Fertig.

## Für Freunde kopieren

Jeder Freund:
1. Klont das Repo.
2. Trägt in `docker-compose.yml` **seine eigenen** Firmendaten ein.
3. Startet mit `docker compose up -d`.
4. Hat seine komplett getrennte Instanz unter `localhost:8080`, mit eigenem
   `data/`-Ordner (dort landen seine Rechnungen, seine Datenbank, sein Branding).

**Wichtig:** Daten liegen physisch bei jedem im `data/`-Ordner. Es gibt keinen
gemeinsamen Server. Deine Rechnungen sind bei dir, seine bei ihm.

## Eigene Rechnungsvorlage bauen

Nach dem ersten Start liegen unter `data/templates/` zwei fertige Vorlagen:
- `standard.html` — klassische Geschäftsrechnung mit farbigem Kopf
- `minimal.html` — reduziertes Layout

**Für den Onkel anpassen:**
1. Kopie anlegen: `cp data/templates/standard.html data/templates/onkel.html`
2. HTML/CSS nach Bedarf editieren (ganz normales HTML, CSS mit WeasyPrint-Support).
3. In der App unter **Einstellungen** → „Aktive Vorlage" → `onkel.html` wählen.
4. Logo hochladen, Farben setzen, Fußzeile setzen — fertig.

Verfügbare Variablen in der Vorlage: `inv` (Rechnung), `items` (Positionen),
`branding` (Farben, Logo, Fußzeilen). Filter: `|money`, `|date`, `|qty`.

## E-Rechnungs-Compliance

- **Format:** ZUGFeRD 2.x / factur-x, Profil EN 16931 (Comfort). Erfüllt die
  Anforderungen des deutschen Umsatzsteuergesetzes an eine E-Rechnung.
- **Prüfung:** Du solltest jede Vorlage einmal gegen den offiziellen
  [KoSIT-Validator](https://github.com/itplr-kosit/validator) prüfen, bevor du
  produktiv schickst. (Optionale Integration — siehe TODO.)
- **Versand:** E-Mail mit dem PDF im Anhang reicht rechtlich. Empfänger
  extrahiert das XML automatisch aus dem PDF.

## GoBD-Konformität

Das System erfüllt die Kernanforderungen der GoBD an digitale Belege:

- **Unveränderbarkeit:** Jede Rechnung ist mit SHA-256 gehasht. Der Hash wird
  mit dem Hash der vorherigen Rechnung verkettet. Nachträgliche Änderungen
  brechen die Kette und werden auf der Übersichtsseite sofort angezeigt.
- **Vollständigkeit:** PDF + XML landen in `data/invoices/<Jahr>/<Nummer>.*`.
- **Nachvollziehbarkeit:** Audit-Log in der Datenbank, pro Ereignis eine Zeile.
- **Verfahrensdokumentation:** Automatisch im `GoBD-Export` enthalten.
- **10-Jahres-Aufbewahrung:** Du musst den `data/`-Ordner regelmäßig sichern.
  Empfohlen: tägliches Backup auf WORM-Speicher oder in ein versioniertes Archiv.

Für eine Betriebsprüfung: Unter `Einstellungen` → „GoBD-Export" bekommst du ein
ZIP mit allen PDFs, XMLs, dem Hash-Protokoll und der Verfahrensdokumentation.

## Datensicherung — unbedingt lesen

Das Programm archiviert *noch nicht automatisch* in ein WORM-Medium. Du musst
selbst dafür sorgen, dass der `data/`-Ordner über **10 Jahre** erhalten bleibt.
Vorschläge:
- Tägliches Backup via `restic` oder `borg` auf einen externen Speicher.
- Zusätzlich: monatlicher Jahres-Export (GoBD-ZIP) auf ein separates Medium.

## Konfiguration

| Variable (docker-compose.yml) | Zweck |
|---|---|
| `SELLER_*` | Deine Firmendaten. Werden auf jede Rechnung geschrieben. |
| `REQUIRE_AUTH` | `true` setzt Basic-Auth vor die ganze App (falls du sie remote hostest). |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` | Zugangsdaten für den Fall oben. |

Die Rechnungsvorlagen und das Branding sind pro Instanz **in** `data/` —
container-Rebuilds löschen sie nicht.

## Technische Basis

- Python 3.12, FastAPI, SQLAlchemy, SQLite
- [WeasyPrint](https://weasyprint.org/) für PDF-Erzeugung
- [factur-x](https://pypi.org/project/factur-x/) für ZUGFeRD-Einbettung
- Jinja2 für Vorlagen

Alles Open Source unter permissiven Lizenzen. Keine Lizenzkosten, keine
Cloud-Abhängigkeit, keine Datenweitergabe an Dritte.

## TODO / Nächste Schritte

- [ ] KoSIT-Validator in die Pipeline integrieren (Java-JAR im Container).
- [ ] Vorlagen-Vorschau direkt in der Einstellungsseite.
- [ ] Rechnungs-Entwurf (Draft) vor dem Festschreiben.
- [ ] Storno/Gutschrift-Workflow.
- [ ] Automatischer Versand per SMTP an den Empfänger.
- [ ] Jahres-Archiv als signiertes PDF/A-3-Paket.
