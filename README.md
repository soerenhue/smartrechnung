# SmartRechnung — Doppelklick, loslegen

Kleine, kostenlose Desktop-App für Freiberufler und kleine Unternehmen: Du
schreibst eine ganz normale Rechnung, die App erzeugt automatisch ein
**ZUGFeRD-E-Rechnung-PDF** (gesetzlich gültig in Deutschland ab 2027).
Alles läuft lokal auf deinem Rechner — es gibt keinen Server, keine Cloud,
niemand sieht deine Daten.

> **Für wen?** Freiberufler, Handwerker, kleine GmbHs — alle, die ab
> Januar 2027 (bzw. 2028 für Kleinunternehmen) E-Rechnungen stellen müssen
> und keine teure Buchhaltungssoftware kaufen wollen.

## So einfach ist es

1. **Datei runterladen** für dein System
   (Windows: `SmartRechnung-Windows.zip`, Mac: `SmartRechnung-macOS.zip`,
   Linux: `SmartRechnung-Linux.tar.gz`).
2. **Entpacken** und `SmartRechnung` doppelklicken. Es öffnet sich ein Fenster.
3. **Firmendaten eintragen** (einmalig). Das sieht aus wie ein normales Formular.
4. **Rechnung schreiben** — Empfänger, Positionen, Preise rein.
5. Button „Rechnung erzeugen" → fertig. Du bekommst ein PDF, das gleichzeitig
   eine E-Rechnung ist.

Das war's. Kein Terminal, keine Installation von irgendwas anderem, keine
Konfigurationsdateien.

## Zwei Wege zum PDF — Umschalter im Formular

- **Modus „In der App erzeugen"** *(Empfehlung)*: Du tippst die Daten ein,
  die App baut ein hübsches PDF **und** hängt das XML dran. Ein Schritt.
- **Modus „Eigenes PDF hochladen"**: Du hast deine eigene schöne Vorlage
  (aus Word, InDesign, egal was) und willst nur das E-Rechnungs-XML
  reingehängt haben. Dann tippst du daneben die gleichen Zahlen wie im PDF
  und lädst dein PDF hoch. Die App bettet das XML ein.

## Eigene Rechnungsvorlage — dein Design

Nach dem ersten Start liegen in deinem Ordner `E-Rechnung/templates/` zwei
fertige Vorlagen zum Umschalten:
- `standard.html` — klassische Geschäftsrechnung mit farbigem Kopf
- `minimal.html` — reduziertes Layout

Du kannst eigene daneben legen — das ist normales HTML/CSS, lässt sich mit
jedem Texteditor bearbeiten. In den Einstellungen wählst du dann aus, welche
Vorlage für deine Rechnungen genutzt wird. Logo, Farben und Fußzeilen stellst
du auch dort ein — ohne eine Datei anfassen zu müssen.

## Wo liegen deine Daten?

Alles liegt lokal auf deinem Rechner in einem Ordner namens `SmartRechnung`:

- **Windows:** `C:\Users\<DeinName>\Documents\SmartRechnung`
- **macOS:** `~/Documents/SmartRechnung`
- **Linux:** `~/.local/share/SmartRechnung`

Darin findest du:
- `invoices/<Jahr>/<Nummer>.pdf` — deine erzeugten E-Rechnungen
- `invoices/<Jahr>/<Nummer>.xml` — das XML zusätzlich einzeln (Backup)
- `erechnung.db` — die kleine Datenbank
- `templates/` — Rechnungsvorlagen zum Anpassen
- `branding/` — dein Logo
- `seller.json`, `branding.json` — deine Einstellungen

**Dieser Ordner ist dein Backup-Ziel.** Sichere ihn regelmäßig auf einen
USB-Stick oder in die Cloud deiner Wahl (10 Jahre Aufbewahrungspflicht).

## GoBD-konform

Die App erfüllt die wichtigsten Punkte der deutschen GoBD-Vorschriften:

- **Unveränderbar**: Jede Rechnung ist mit SHA-256-Hash gesichert. Hashes
  werden verkettet. Wird nachträglich eine alte Rechnung manipuliert, bricht
  die Kette — die App zeigt dir das sofort als Warnung an.
- **Vollständig**: Jede Rechnung landet als PDF + XML im Ordner.
- **Nachvollziehbar**: Audit-Log mit Zeitstempel in der Datenbank.
- **Prüfer-tauglich**: Unter „GoBD-Export" bekommst du ein fertiges ZIP
  mit allen Rechnungen, Hash-Protokoll und Verfahrensdokumentation.

## Für Entwickler — selbst bauen

Falls du den Installer selbst bauen willst:

```bash
# Linux-System-Libs (nur auf Linux-Build-Maschinen)
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libcairo2 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# App direkt starten (zum Entwickeln)
python launcher.py

# Installer bauen (Ergebnis in dist/)
pyinstaller erechnung.spec --clean
```

Der Workflow in `.github/workflows/build.yml` baut bei einem Git-Tag `v*`
automatisch Installer für Windows, macOS und Linux und hängt sie an das
GitHub-Release.

## Freunden geben

1. Du taggst einmal `git tag v1.0.0 && git push --tags`.
2. GitHub baut die drei Installer.
3. Du schickst deinem Onkel den Link zu `SmartRechnung-Windows.zip`.
4. Er entpackt, doppelklickt, trägt seine Firmendaten ein, schreibt Rechnungen.

**Deine Rechnungen bleiben bei dir. Seine bleiben bei ihm.** Die Apps reden
nie miteinander, kein Server dazwischen.

## Lizenz & Basis

Open Source, permissive Lizenzen. Verwendete Bibliotheken:
- [FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy](https://sqlalchemy.org/), [Jinja2](https://palletsprojects.com/p/jinja/)
- [WeasyPrint](https://weasyprint.org/) für PDF-Erzeugung
- [factur-x](https://pypi.org/project/factur-x/) für ZUGFeRD-Einbettung
- [pywebview](https://pywebview.flowrl.com/) für das Desktop-Fenster
- [PyInstaller](https://pyinstaller.org/) für das Packaging

Keine Abo-Kosten, keine Cloud, keine Datenweitergabe.
