# CoffeePy – Bedienungsanleitung

*[English version](MANUAL.en.md)*

CoffeePy ist eine Strichliste für die gemeinsame Kaffeemaschine – nur ohne Zettel: Wer sich
einen Kaffee nimmt, hält seine Karte an den Leser neben der Maschine und tippt auf dem
Touchscreen an, was er trinkt. CoffeePy rechnet mit, zeigt jedem seinen Kontostand, nimmt
Zahlungen an und hat für die Verwaltung ein Dashboard im Browser. Mitglieder können ihr Handy
verknüpfen und sehen dort ihre Statistik und zahlen.

Diese Anleitung erklärt alle Oberflächen. Technische Details (Aufbau, Entwicklung, Interna)
stehen in der [README](../README.md).

**Inhalt**

1. [Was du brauchst](#1-was-du-brauchst)
2. [Installation](#2-installation)
3. [Erste Schritte](#3-erste-schritte)
4. [Der Kiosk](#4-der-kiosk)
5. [Das Handy-Portal](#5-das-handy-portal)
6. [Das Dashboard](#6-das-dashboard)
7. [Betrieb und Wartung](#7-betrieb-und-wartung)
8. [Wenn etwas nicht klappt](#8-wenn-etwas-nicht-klappt)

---

## 1. Was du brauchst

| Teil | Hinweise |
|---|---|
| Raspberry Pi | **3 Model B oder neuer** (3, 3+, 4, 5, Zero 2 W). Ein Pi 3 mit 1 GB reicht; ein Pi 4 mit 2 GB+ hat mehr Reserven. |
| microSD-Karte | 16 GB oder mehr, möglichst A1/A2 – das ist die Systemplatte. |
| Netzteil | Das offizielle Netzteil für dein Modell. Billige Netzteile sind die häufigste Ursache für wackelige USB-Geräte und Touchscreens. |
| Touchscreen | 1024×600 (z. B. [Adafruit #2396](https://www.adafruit.com/product/2396)). Das offizielle 7"-Display (800×480) funktioniert ebenfalls. |
| NFC-Leser | **ACR122U** von Advanced Card Systems – der einzige unterstützte Leser. |
| Echtzeituhr | **DS3231**-Modul, damit der Pi auch ohne Internet die richtige Uhrzeit hat. Anschluss an I²C-1: VCC→Pin 1, GND→Pin 6, SDA→Pin 3, SCL→Pin 5. |
| Karten | Beliebige MIFARE-Karten oder -Schlüsselanhänger (ISO 14443A) – eine pro Mitglied. Viele Firmen- oder Fahrkarten funktionieren auch. |
| USB-Stick (optional) | Für automatische Backups. Formatiert als `ext4` mit dem Namen `COFFEEBAK` (`mkfs.ext4 -L COFFEEBAK /dev/sdX1`) wird er automatisch eingebunden. |

### Gehäuse

![Gehäuse](pictures/housing.jpg)

Ein 3D-druckbares Gehäuse für Pi, 1024×600-Display und Leser liegt in [housing/](../housing/):
die `LCD_*`-Rahmen und den passenden `Card_*`-Halter aus `housing/3D/` drucken (`_H` quer,
`_V` hochkant, `_V_T1`/`_V_T2` für Drucker, die den Rahmen in zwei Teilen brauchen), die
Aufkleber aus `housing/labels/` runden es ab. Jedes andere Gehäuse geht genauso.

---

## 2. Installation

Die Installation läuft von einem Linux-PC aus, mit dem **Provisioning-Tool**. Auf dem Pi
brauchst du nur ein frisches **Raspberry Pi OS** mit eingeschaltetem SSH.

### Den Pi vorbereiten

1. Mit dem [Raspberry Pi Imager](https://www.raspberrypi.com/software/) **Raspberry Pi OS
   Lite** auf die SD-Karte schreiben (auf einem Pi 3 die 32-Bit-Variante).
2. In den Einstellungen des Imagers einen **Benutzernamen und ein Passwort** festlegen und
   **SSH einschalten**. Für die Installation braucht der Pi Internet – am einfachsten per
   Netzwerkkabel, sonst trägst du dort dein WLAN ein.
3. Karte einsetzen, Display, Leser und Uhr anschließen, Pi starten.

> Ein Netzwerkkabel ist die bessere Wahl: Am Ende richtet CoffeePy auf Wunsch ein eigenes
> WLAN ein, und eine Verbindung über das bisherige WLAN des Pi bricht dann ab.

### Das Provisioning-Tool

Im CoffeePy-Verzeichnis auf deinem PC:

```bash
./scripts/provision.sh
```

Das Tool braucht nur Bash, SSH und tar, und es verändert an deinem PC nichts. Bedient wird
es mit den Pfeiltasten, **Enter** und **Esc**.

**Sprache.** Zuerst wählst du die Sprache des Tools. Sie ist auch der Vorschlag für die
Standardsprache des Geräts.

![Sprachauswahl](pictures/manual/de/provision-language.png)

**Begrüßung**, danach die **Adresse des Pi** (wie dein Router sie anzeigt, oder
`raspberrypi.local`), der **Benutzer** und sein **Passwort** aus dem Imager. Das Passwort
wird nur im Speicher gehalten und nirgends gespeichert.

![Begrüßung](pictures/manual/de/provision-welcome.png)

![Adresse des Pi](pictures/manual/de/provision-host.png)

![Passwort](pictures/manual/de/provision-password.png)

**Pi prüfen.** Das Tool zeigt Modell, System, Speicher, ob der Pi Internet hat und über welche
Verbindung du verbunden bist – und warnt, wenn etwas nicht passt (zu wenig Speicher, kein
Internet, Verbindung über das WLAN des Pi).

![Pi prüfen](pictures/manual/de/provision-check.png)

Ist CoffeePy auf dem Pi schon installiert, sagt das Tool es und übernimmt dessen
Einstellungen als Vorgaben. Datenbankpasswort und interne Schlüssel bleiben dabei immer
erhalten – du kannst das Tool also jederzeit erneut laufen lassen, um Einstellungen zu ändern
oder eine neue Version einzuspielen.

![Bestehende Installation](pictures/manual/de/provision-existing.png)

**Einstellungen.** Alles, was in die Konfiguration des Pi kommt, auf einer Seite. Eine Zeile
auswählen, um sie zu ändern:

![Einstellungen](pictures/manual/de/provision-settings.png)

| Einstellung | Bedeutung |
|---|---|
| Sprache | Die Sprache, in der Kiosk, Dashboard und Portal starten. Jedes Gerät kann trotzdem selbst umschalten. |
| Admin-Konto | Benutzername und Passwort für das Dashboard. Vorgeschlagen wird ein zufälliges Passwort, das am Ende angezeigt wird. Gilt nur beim **ersten** Anlegen – ein bestehendes Konto änderst du im Dashboard. |
| WLAN-Access-Point | Der Pi spannt ein eigenes WLAN auf, über das Handys Dashboard und Portal erreichen. Ausschalten, wenn der Pi in deinem bestehenden Netz bleibt. |
| Adresse für Handys | Die Adresse in den QR-Codes und im Zertifikat. Mit Access Point `192.168.4.1`, sonst die (am besten feste) Adresse des Pi in deinem Netz. |
| Zeitzone | Für die Uhr und die Statistiken nach Stunde und Wochentag. |
| Backups | Regelmäßige Sicherung der Datenbank, standardmäßig auf einen USB-Stick. |
| Display | Bildschirmmodus. Das dokumentierte 1024×600-Display meldet seine Auflösung nicht und braucht deshalb die Vorgabe. |
| Web-Worker | Prozesse für die Web-App: 2 für einen Pi 3, 3–4 für einen Pi 4 oder 5. |
| Installieren aus | Internet oder ein Offline-Paket (für Pis ohne Internet, siehe README). |

Eingaben werden sofort geprüft – zum Beispiel muss ein WLAN-Passwort mindestens 8 Zeichen
haben:

![WLAN-Access-Point](pictures/manual/de/provision-wifi.png)

![Prüfung einer Eingabe](pictures/manual/de/provision-validation.png)

**Installieren.** Mit *CoffeePy installieren* geht es los. Die Installation läuft auf dem Pi
selbst, das Tool zeigt nur den Fortschritt – bricht die Verbindung ab oder schließt du das
Fenster, läuft sie trotzdem weiter. Das dauert 20–40 Minuten, vor allem für den Bau der App.

![Installation](pictures/manual/de/provision-progress.png)

**Fertig.** Zum Schluss startet das Tool den Pi neu (Uhr, Display und Kiosk greifen erst
danach), prüft, ob CoffeePy über HTTPS antwortet, und zeigt die Zugangsdaten. Auf Wunsch
speichert es sie in einer Datei, die nur du lesen kannst.

![Fertig](pictures/manual/de/provision-done.png)

> **Für mehrere Geräte** lässt sich das Tool auch ohne Fragen betreiben:
> `./scripts/provision.sh --answers antworten.txt --unattended`. Welche Schlüssel die Datei
> kennt, steht am Anfang des Scripts.

---

## 3. Erste Schritte

### Mit dem Handy verbinden

Mit Access Point verbindest du dich mit dem WLAN **CoffeePy** (das Passwort zeigt das Tool am
Ende, und der Kiosk zeigt es als QR-Code, siehe [unten](#einrichtung--netzwerk)). Danach
öffnest du `https://192.168.4.1/` – ohne Access Point die Adresse, die du bei der Einrichtung
angegeben hast.

### Das Zertifikat installieren

CoffeePy ist nur über HTTPS erreichbar. Das Zertifikat signiert das Gerät selbst, deshalb
warnt der Browser beim ersten Besuch. Damit die Warnung verschwindet, installierst du einmal
pro Handy oder PC das **Gerätezertifikat**: Es ist auf der Anmeldeseite und im Portal unter
*Zertifikat herunterladen* verlinkt (direkt: `https://192.168.4.1/certificate/`).

- **iPhone/iPad:** Datei öffnen → *Einstellungen › Profil geladen* installieren → danach unter
  *Einstellungen › Allgemein › Info › Zertifikatsvertrauenseinstellungen* das CoffeePy-Zertifikat
  einschalten.
- **Android:** *Einstellungen › Sicherheit › Weitere Einstellungen › Zertifikat installieren ›
  CA-Zertifikat* und die heruntergeladene Datei auswählen.
- **Windows:** Datei öffnen → *Zertifikat installieren* → Speicherort *Vertrauenswürdige
  Stammzertifizierungsstellen*.
- **macOS:** Datei öffnen (Schlüsselbundverwaltung) → Zertifikat doppelklicken → *Vertrauen:
  Immer vertrauen*.

Hast du im Dashboard ein eigenes Zertifikat hinterlegt (siehe [Zertifikat](#zertifikat)),
entfällt das, wenn es von einer Stelle stammt, der deine Geräte ohnehin vertrauen.

### Im Dashboard anmelden

Öffne `https://192.168.4.1/` und melde dich mit dem Admin-Konto an. Die Sprache schaltest du
unter dem Anmeldeformular um, später unten in der linken Leiste.

![Anmeldung](pictures/manual/de/dashboard-login.png)

### Einrichten

Bevor der Kiosk Bestellungen annimmt, braucht er mindestens eine **Kaffeesorte** – solange
keine angelegt ist, zeigt er *Einrichtung nötig* mit QR-Codes zu WLAN und Dashboard.

1. **Kaffeesorten** anlegen: Name, Preis und Bezüge (wie viele Tassen die Maschine dafür
   ausgibt – eine Kanne zählt zwei).
2. Optional **Zahlungsmethoden** anlegen (PayPal, PayPal Pool, WERO oder ein eigener Link).
   Bar geht immer.
3. **Mitglieder** musst du nicht anlegen: Eine unbekannte Karte führt am Kiosk automatisch
   zur Anmeldung mit Spitznamen.

---

## 4. Der Kiosk

Der Touchscreen hat vier Bereiche, links in der Leiste. Oben rechts sitzen zwei runde Knöpfe:
**DE/EN** schaltet die Sprache des Kiosks um, der **Schraubenschlüssel** ist der Admin-Zugang –
für Mitglieder ist er nicht gedacht.

Außer der Rangliste brauchen alle Bereiche eine Karte. Wer eine Karte auflegt, ist angemeldet,
bis er fertig ist oder nach 20 Sekunden ohne Eingabe automatisch abgemeldet wird. Ein Tipp auf
einen Bereich in der Leiste meldet ebenfalls ab.

### Bestellen

Der Startbildschirm. Er erklärt, was nach dem Auflegen passiert, und zeigt die Uhrzeit – geht
sie sichtbar falsch, ist die Batterie der Echtzeituhr leer.

![Bestellen – Karte auflegen](pictures/manual/de/kiosk-wait-order.png)

Nach dem Auflegen begrüßt dich der Kiosk mit Namen. Dein Lieblingskaffee ist vorausgewählt;
tippe einen anderen an, um zu wechseln. **Jetzt bestellen** bucht sofort, **Abbrechen** bricht
ab, und ohne Eingabe wird nach 20 Sekunden automatisch gebucht. Wählst du eine andere Sorte,
entfällt die automatische Buchung – dann bestätigst du selbst. Die Medaillen neben dem Namen
zeigen einen Platz unter den Top 3 – insgesamt und in diesem Monat. Wer aus dem Vormonat noch
etwas schuldet, bekommt hier eine Erinnerung.

![Bestellen](pictures/manual/de/kiosk-order.png)

Danach ein Dankeschön – je nachdem, wie viele Tassen es heute schon waren, mit einer anderen
Animation.

![Danke](pictures/manual/de/kiosk-thank-you.png)

### Zahlen & Statistik

![Zahlen & Statistik – Karte auflegen](pictures/manual/de/kiosk-wait-pay.png)

Zeigt deine Tassen insgesamt (**CCT**) und in diesem Monat (**CCM**) und deinen
**Kontostand**. Ist er negativ, steht der offene Betrag schon im Feld – trag ein, was du
bezahlt hast, wähle die Zahlungsart und tippe **Bezahlen**. Sind Zahlungsmethoden mit Link
eingerichtet, kannst du den offenen Betrag auch direkt per QR-Code mit dem Handy bezahlen.

![Zahlen & Statistik](pictures/manual/de/kiosk-pay.png)

### Einstellungen

![Einstellungen – Karte auflegen](pictures/manual/de/kiosk-wait-settings.png)

Hier änderst du **Spitznamen** und **Lieblingskaffee** und tippst **Speichern**. Getippt wird
direkt oder über **Tastatur** mit der Bildschirmtastatur.

![Einstellungen](pictures/manual/de/kiosk-settings.png)

![Bildschirmtastatur](pictures/manual/de/kiosk-keyboard.png)

Eine Karte, die der Kiosk noch nicht kennt, landet automatisch hier: Mit dem Speichern ist das
neue Mitglied angelegt.

![Neue Karte](pictures/manual/de/kiosk-new-card.png)

**Dieses Handy verknüpfen** zeigt einen QR-Code, mit dem du dein Handy mit deinem Konto
verbindest (siehe [Handy-Portal](#5-das-handy-portal)). Der Code gilt fünf Minuten und nur
einmal. Mit Access Point steht daneben der QR-Code zum WLAN – das Handy muss zuerst dort
verbunden sein.

![Handy verknüpfen](pictures/manual/de/kiosk-link-phone.png)

### Rangliste

Braucht keine Karte. **Weiter** blättert durch die Top 7 insgesamt, die Top 7 dieses Monats
und den **Schuldenpranger** – alle, die bis zum Vormonat noch etwas schulden.

![Rangliste insgesamt](pictures/manual/de/kiosk-ranking-alltime.png)

![Rangliste dieser Monat](pictures/manual/de/kiosk-ranking-month.png)

![Schuldenpranger](pictures/manual/de/kiosk-ranking-pillory.png)

### Admin-Zugang

Der runde Schraubenschlüssel oben rechts ist für die, die das Gerät betreuen: Er zeigt zwei
QR-Codes, einen, um dem WLAN des Geräts beizutreten, und einen, der das Dashboard öffnet – der
Touchscreen selbst hat ja keine Tastatur. Mitglieder brauchen ihn nicht; ihr Handy verknüpfen
sie unter **Einstellungen**.

![Admin-Zugang](pictures/manual/de/kiosk-setup-popup.png)

### „Wir sind gleich wieder da“

Während einer Installation oder eines Updates – und wenn die App kurz nicht antwortet – zeigt
der Kiosk diesen Bildschirm und nimmt keine Eingaben an. Ist die Arbeit erledigt, lädt er sich
von selbst neu.

![Wartung](pictures/manual/de/kiosk-maintenance.png)

---

## 5. Das Handy-Portal

Jedes Mitglied kann sein Handy verknüpfen: am Kiosk unter **Einstellungen** die Karte
auflegen, **Dieses Handy verknüpfen** tippen und den QR-Code mit der Kamera scannen. Das Handy
bleibt danach sechs Monate angemeldet (bei jeder Nutzung verlängert). Die Sprache stellst du
oben rechts um; sie gilt nur für dieses Handy.

**Start** zeigt Tassen, Kontostand und die letzten Bestellungen und Zahlungen. Unten auf der
Seite findest du das Gerätezertifikat.

![Portal Start](pictures/manual/de/portal-home.png)

**Bezahlen** öffnet deine Bezahl-App mit dem offenen Betrag, oder du trägst eine Zahlung ein,
die du schon gemacht hast.

![Portal Bezahlen](pictures/manual/de/portal-pay.png)

**Statistik** vergleicht dich mit den anderen: Tassen pro Monat, Uhrzeit, Wochentag und Sorte.

![Portal Statistik](pictures/manual/de/portal-stats.png)

**Karten** zeigt deine Karten; über *Neue Karte hinzufügen* verknüpfst du eine Ersatzkarte,
indem du sie an den Kiosk hältst. **Geräte** listet deine verknüpften Handys – dort meldest du
ein verlorenes Gerät ab.

![Portal Karten](pictures/manual/de/portal-cards.png)

![Portal Geräte](pictures/manual/de/portal-devices.png)

Ein Link, der abgelaufen ist oder schon benutzt wurde, führt hierher – dann einfach am Kiosk
einen neuen erzeugen.

![Link ungültig](pictures/manual/de/portal-link-invalid.png)

---

## 6. Das Dashboard

Das Dashboard erreichst du unter `https://<Adresse>/dashboard/`. Seiten, die das ganze Gerät
betreffen (System, Backup, Update, Zertifikat, Import, Zurücksetzen, Protokoll), sieht nur ein
**Superuser** – also das Admin-Konto aus der Einrichtung.

**Start** zeigt den Zustand des Kartenlesers, die Adresse für andere Geräte und die Exporte
(alle Bestellungen bzw. Zahlungen als CSV).

![Dashboard Start](pictures/manual/de/dashboard-home.png)

### Statistik und Pranger

**Statistik**: Tassen und Umsatz der letzten 12 Monate, Verteilung der Sorten, Tassen nach
Uhrzeit und Wochentag (die Titel nennen die meistbesuchte Zeit), Preisentwicklung und
Zahlungen nach Methode. Gezählt werden Bezüge – eine Kanne zählt zwei Tassen.

![Statistik](pictures/manual/de/dashboard-statistics.png)

**Pranger**: alle Mitglieder mit negativem Kontostand, gerechnet bis zum Vormonat – dieselbe
Liste wie am Kiosk.

![Pranger](pictures/manual/de/dashboard-pillory.png)

### Kaffeesorten und Zahlungsmethoden

**Kaffeesorten**: die Sorten, die am Kiosk zur Auswahl stehen – Name, Preis und Bezüge.

![Kaffeesorten](pictures/manual/de/dashboard-coffee-sorts.png)

**Zahlungsmethoden**: Für PayPal reicht der paypal.me-Name, für PayPal Pool die URL des
Pools, für WERO die Telefonnummer; *Eigener Link* nimmt jede Adresse, optional mit `{amount}`
für den offenen Betrag. Daraus entstehen die Bezahl-Links und QR-Codes an Kiosk und Handy.

![Zahlungsmethoden](pictures/manual/de/dashboard-payment-methods.png)

![Zahlungsmethode hinzufügen](pictures/manual/de/dashboard-payment-method-form.png)

### Mitglieder

Die Liste aller Mitglieder mit ihren Karten. Pro Mitglied:

- **Bearbeiten**: Spitzname, UID, Lieblingskaffee.
- **Karte hinzufügen**: Die nächste Karte, die am Kiosk aufgelegt wird, gehört danach zu diesem
  Mitglied – so bekommt jemand eine Ersatzkarte, ohne dass ein zweites Konto entsteht.
- **Geräte abmelden**: meldet alle verknüpften Handys ab.
- **Als ausgeschieden markieren**: bucht den Kontostand aus und sperrt die Karten; lässt sich
  mit *Reaktivieren* rückgängig machen.

![Mitglieder](pictures/manual/de/dashboard-members.png)

![Mitglied bearbeiten](pictures/manual/de/dashboard-member-form.png)

**Mitglieder zusammenführen** hilft, wenn doch zwei Konten entstanden sind: Alle Bestellungen,
Zahlungen und Karten wandern auf das Konto, das du behältst, das andere wird gelöscht.

![Mitglieder zusammenführen](pictures/manual/de/dashboard-merge.png)

### System

**System** zeigt CPU, Arbeitsspeicher und Speicherplatz des Geräts.

![System](pictures/manual/de/dashboard-system.png)

**Systemzeit**: nur nötig, wenn die Echtzeituhr fehlt oder falsch geht. Die neue Zeit wird
innerhalb von zehn Minuten auch in die Uhr geschrieben.

![Systemzeit](pictures/manual/de/dashboard-system-time.png)

**Backup**: sofort sichern oder vorhandene Backups herunterladen.

![Backup](pictures/manual/de/dashboard-backup.png)

**Update**: ein Update-Paket hochladen (siehe [Updates](#updates)).

![Update](pictures/manual/de/dashboard-update.png)

#### Zertifikat

Zeigt, welches Zertifikat gerade verwendet wird. Du kannst ein **eigenes Zertifikat** (PEM,
mit Kette, und den unverschlüsselten privaten Schlüssel) hochladen; es ist nach wenigen
Sekunden aktiv. CoffeePy prüft, dass Schlüssel und Zertifikat zusammenpassen, und warnt, wenn
es nicht für die Adressen des Geräts gilt. Ein Knopf führt zurück zum selbstsignierten
Zertifikat. Der Kiosk auf dem Gerät nutzt immer das eigene Zertifikat – ein fehlerhafter
Upload sperrt ihn also nie aus.

![Zertifikat](pictures/manual/de/dashboard-certificate.png)

**Alte Datenbank importieren**: übernimmt die Daten aus einem alten CoffeePy (V1) – eine
`coffee.db` oder eines ihrer Backups. Der Import läuft im Hintergrund weiter, auch wenn du die
Seite schließt.

![Import](pictures/manual/de/dashboard-import.png)

**Protokoll**: die letzten 500 Änderungen im Dashboard – wer was wann gemacht hat.

![Protokoll](pictures/manual/de/dashboard-audit-log.png)

**Datenbank zurücksetzen** löscht **alle** Kaffeedaten (Mitglieder, Karten, Bestellungen,
Zahlungen, Sorten, Zahlungsmethoden, Protokoll). Die Anmeldungen am Dashboard bleiben. Zur
Bestätigung musst du `DELETE` eintippen – vorher ein Backup machen!

![Datenbank zurücksetzen](pictures/manual/de/dashboard-reset.png)

Für alles, was das Dashboard nicht abdeckt, gibt es die Django-Verwaltung unter `/admin/`.

---

## 7. Betrieb und Wartung

### Einstellungen ändern

Lass einfach das [Provisioning-Tool](#das-provisioning-tool) erneut laufen: Es liest die
bestehende Konfiguration, du änderst, was du willst, und es spielt die Änderung ein – der Kiosk
zeigt währenddessen „Wir sind gleich wieder da“. Daten, Datenbankpasswort und Zertifikat
bleiben erhalten.

### Backups

Mit eingeschalteten Backups sichert CoffeePy die Datenbank regelmäßig (standardmäßig stündlich,
die letzten 48 Stände) auf den USB-Stick `COFFEEBAK`. Im Dashboard unter **Backup** kannst du
sofort sichern und Backups herunterladen.

### Updates

Das Gerät braucht für Updates kein Internet:

1. Auf einem PC mit Internet ein Update-Paket bauen:
   `./scripts/build_update_bundle.sh linux/arm64 coffeepy-update.tar` (`linux/arm/v7` für ein
   32-Bit-System).
2. Im Dashboard unter **Update** hochladen (per USB-Stick auf einen Laptop im WLAN des Geräts,
   oder direkt).
3. Das Gerät spielt das Update selbst ein. Der Kiosk zeigt solange „Wir sind gleich wieder da“
   mit Fortschrittsbalken, die Update-Seite den Verlauf.

Alternativ geht ein Update auch über das Provisioning-Tool von einem PC aus.

### Sprache

Die Standardsprache legst du bei der Einrichtung fest. Jedes Gerät kann für sich umschalten:
der Kiosk mit dem **DE/EN**-Knopf, Handys und PCs im Portal oben rechts und im Dashboard unten
links (bzw. unter dem Anmeldeformular). Die Wahl merkt sich jedes Gerät selbst.

---

## 8. Wenn etwas nicht klappt

| Problem | Lösung |
|---|---|
| Der Browser warnt vor der Seite. | Das Gerätezertifikat einmal installieren (siehe [oben](#das-zertifikat-installieren)). |
| **Bad Request (400)** im Browser. | Das Gerät kennt die Adresse nicht, mit der du es aufrufst. Nimm die Adresse aus der Einrichtung (`192.168.4.1`, `coffeepy.local`) oder lass das Provisioning-Tool über die neue Adresse laufen – es trägt sie ein. |
| Der Bildschirm ist nach dem Einschalten eine Minute lang schwarz, dann weiß. | Normal: Erst startet der Pi, dann der Browser. Nach etwa einer Minute erscheint der Kiosk. |
| *Kartenleser nicht verfügbar* | Kabel des Lesers prüfen und das Gerät neu starten. |
| Eine Karte wird nicht erkannt. | Karte flach und ruhig auflegen; eine Karte, die als ausgeschieden markiert ist, wird abgewiesen. |
| Die Uhrzeit stimmt nicht. | Batterie der Echtzeituhr tauschen, dann im Dashboard unter **Systemzeit** korrigieren. |
| „Wir sind gleich wieder da“ geht nicht weg. | Läuft gerade ein Update? Den Verlauf zeigt das Dashboard unter **Update**. Ein Status, der älter als zwei Stunden ist, wird ignoriert; im Zweifel das Gerät neu starten. |
| Das Handy erreicht das Dashboard nicht. | Ist es mit dem WLAN des Geräts verbunden? Mobile Daten kurz ausschalten, manche Handys verlassen ein WLAN ohne Internet sonst. |
