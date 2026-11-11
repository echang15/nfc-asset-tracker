# NFC Asset Tracker

A self-hosted web app for checking assets (laptops, tablets, cameras, tools…) in
and out by tapping NFC tags on a USB reader attached to a laptop. Administrators
manage users, assets and settings, and run reports.

It runs locally (Python + SQLite) with no internet connection, build step or
external database.

## Features

**Scan station (all users)**
- Tap an asset's NFC tag to check it out; tap it again to check it in.
- Modes: *Auto* (toggle), *Check out only*, *Check in only* (e.g. a returns desk).
- Sign in by tapping your ID badge or with a username and password.
- Shared-laptop mode: tap a different badge to switch user. The station signs out
  automatically after a configurable idle time.
- Sound and colour feedback, a log of this session's scans, and a list of the
  items you hold with due dates.
- **My items** page: what you have, what is overdue, and your history.

**Administration**
- **Dashboard**: totals by status, overdue items, items due within 48h,
  activity in the last 24h, recent transactions.
- **Users**: create, edit, deactivate or delete; admin/user roles; assign badges
  by scanning; reset passwords; see what each person holds and their history.
- **Assets / devices**: create and edit; assign tags by scanning; category,
  location, serial, model, manufacturer, condition, purchase date and cost,
  warranty date, notes; statuses *available / checked out / maintenance / lost /
  retired*; search and filter; full history per asset.
- Manual check-out on behalf of a user (with a due date), check-in with
  condition and notes, and due-date extension.
- **CSV import** of assets (creates new assets or updates existing ones).
- **Categories** with their own loan periods, and **locations**.
- **Settings**: organisation name, default loan period, idle sign-out time,
  badge sign-in on/off, whether users may return items someone else holds,
  and the reader API key.
- **Audit log** of admin actions and sign-ins.
- Unknown tag scanned by an admin → a one-click "Register this tag" link.
- **Program NFC tags**: write an asset ID (`asset:A-00001`) or user ID
  (`user:alice`) onto a blank tag and link it in one step. Use it for a single
  asset or user, or work through all untagged assets in a row.

**Reports** (each one exports to CSV and prints)
| Report | What it answers |
|---|---|
| Currently checked out | Who has what, since when, and when it is due |
| Overdue items | What is late and by how many days |
| Transaction history | Every check-in/out, filtered by date, user, asset and action |
| Asset utilization | Check-outs, distinct users, time out and % utilization per asset |
| User activity | Check-outs, returns, late returns, current and overdue holdings per user |
| Inventory summary | Counts and value by category/location × status |
| Idle assets | Assets nobody has checked out in N days |
| Warranty expiring | Warranties that have expired or expire within N days |

## Quick start

### Windows

1. Install **Python 3.12** from <https://www.python.org/downloads/windows/>.
   In the installer, tick **"Add python.exe to PATH"**.
2. Download this project (green **Code** button → *Download ZIP*) and unzip it,
   e.g. to `Documents\nfc-asset-tracker`.
3. Plug in the NFC reader. Windows installs its standard smart-card driver
   automatically.
4. Double-click **`start-windows.bat`**.

### macOS

1. Install **Python 3.12** from <https://www.python.org/downloads/macos/>
   (the built-in `python3` also works if it is 3.9 or newer).
2. Download and unzip this project, e.g. into `Documents`.
3. Plug in the NFC reader. No driver is needed.
4. Double-click **`start-mac.command`**. If macOS says it is from an
   unidentified developer, right-click it → **Open** → **Open** (once only). If
   it opens in a text editor instead, run this in Terminal from the project folder:
   ```bash
   chmod +x start-mac.command
   ```

### What the start script does

The first run needs internet for a minute. The script creates a private Python
environment in `.venv`, installs the packages (including the smart-card library
`pyscard`, prebuilt for Windows and macOS), starts the app **with the reader
bridge** and opens <http://127.0.0.1:5050> in your browser. Keep the window open
while you use the app. Close it or press Ctrl+C to stop.

On first launch the app asks you to create the administrator account. To start
automatically at login, put a shortcut to the start script in the Windows
Startup folder (`Win+R` → `shell:startup`) or add it to macOS **System Settings
→ General → Login Items**.

### Manual / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-reader.txt
python run.py --pcsc --open
```

Linux needs `sudo apt install pcscd libpcsclite-dev swig` first. Or use
`./start-linux.sh`.

To try it with sample data (creates `admin` / `adminadmin` if there is no admin
yet, plus users `alice`, `bob`, `carol` and `dave` with password `password`):

```bash
flask --app run seed-demo
```

Other useful commands:

```bash
flask --app run create-admin           # add another admin from the terminal
python run.py --pcsc --host 0.0.0.0    # make it reachable from other PCs on the LAN
```

The database and the generated secret key are stored in `instance/`. Back up
`instance/assets.db` to back up everything. Updates add new database columns
automatically.

## Connecting the NFC reader

USB NFC readers come in two kinds. Both are supported.

### Smart-card (PC/SC) readers: recommended, required for programming tags

This includes 2-in-1 **CAC / smart card + NFC** readers (e.g. Arsandyn and
similar "Contact and Contactless IC/ID/PIV" readers), ACR122U, ACR1252U, HID
Omnikey and Identiv. They show up as standard smart-card readers on Windows and
macOS, so no driver is needed. The start scripts run the **reader bridge**,
which sends each tapped tag to the app.

Check it: **Settings → General** shows *Reader bridge connected* with the
reader's name. Under *Test a tag*, click **Read via bridge** and tap a tag.

**2-in-1 contact + contactless readers** appear as two readers. The bridge
listens to the NFC antenna and ignores cards pushed into the slot (CAC, bank
cards). If it picks the wrong one, list the readers with
`.venv/bin/python reader_bridge.py --list`
(Windows: `.venv\Scripts\python reader_bridge.py --list`), then pass part of
the right name: `run.py --pcsc --reader "Contactless"`.

### Keyboard-emulation (HID) readers

Some inexpensive readers "type" the tag's UID followed by Enter. They work
without the bridge: open the **Scan** page and tap a tag. Set the reader to
output the UID in hex, and set every station the same way. These readers
**cannot program tags**.

### Which tags to buy

13.56 MHz **NTAG213, NTAG215 or NTAG216** tags (stickers, key fobs or cards).
These are the only types the app can program; MIFARE Classic and DESFire tags
can be linked by UID but not written. Use **on-metal / anti-metal** stickers on
laptops and metal tools, because ordinary stickers don't read on metal. Phones
(Apple/Google Pay) show a random ID each time, so they can't be used as badges.
125 kHz proximity badges (older HID Prox / EM4100) can't be read by a 13.56 MHz
reader.

## Programming tags

You need a smart-card reader with the bridge running, and NTAG213/215/216 tags.

- **One asset**: open the asset → **Program NFC tag** → place a blank tag on
  the reader. The app writes `asset:<asset tag>`, reads it back to verify, and
  links the tag to the asset. Any old tag for that asset stops working.
- **One user badge**: Users → open the user → **Program new badge** → place a
  tag or card on the reader. It is written with `user:<username>` and becomes
  that person's sign-in badge.
- **Many assets**: Assets → **Program tags**. Press *Start* and place a new tag
  for each asset in turn. The page moves to the next asset by itself, and you
  can *Retry* or *Skip* any asset.

How it works and why it is safe:
- While a programming request is waiting (up to 90 seconds), the next tap is
  used for programming instead of a check-in/out.
- A tag that already belongs to another asset or user is refused **before
  anything is written**. Remove it from the other item first.
- The written text is for people and phones: any NFC phone app shows
  `asset:A-00001`. The app itself still identifies tags by their factory UID,
  which cannot be changed. So writing `user:admin` onto a random tag does
  **not** let anyone sign in as admin.
- If you scan a tag that carries `asset:...` text but isn't linked (for example
  after restoring an old backup), the scan station offers **Link tag to …**
  with one click.
- Tags are left rewritable. To lock one permanently, use a phone app such as
  NXP *TagWriter*. A locked tag can't be reprogrammed.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Reader bridge not detected" | Start the app with the start script (or `run.py --pcsc`); check the reader is plugged in; look at the start window for messages. |
| Start window says *pyscard is not installed* | Make sure you installed Python from python.org (3.9–3.14), delete the `.venv` folder and run the start script again. |
| Windows pops up "installing device driver software" when a tag is tapped | Harmless. To silence it: `gpedit.msc` → Computer Configuration → Administrative Templates → Windows Components → Smart Card → *Turn on Smart Card Plug and Play service* → **Disabled**. |
| "Smart card service" error on Windows | `Win+R` → `services.msc` → **Smart Card** → Start (Automatic). |
| Tag reads but programming fails | Use NTAG213/215/216 tags and hold the tag still until the beep. Tags locked as read-only can't be reprogrammed. |
| "Tag is already assigned to …" | That tag is linked to another asset or user. Use a fresh tag, or clear the UID on the other item first. |
| The app opens on a different port than 5050 | Another program uses port 5050, so the app took the next free one. The start window shows the address. Use it, or close the other program. |

## Typical workflow

1. Admin creates **categories** (with loan periods) and **locations**.
2. Admin adds **assets** (one by one or by CSV import), then uses
   **Assets → Program tags** to write and stick a tag on each one. Tags that
   are already on items can be linked with *Scan tag* instead.
3. Admin adds **users** and gives each person a badge with **Program new
   badge**, or links an existing badge with *Scan badge*. This is optional;
   users can also sign in with a password.
4. Leave the laptop on the **Scan** page. People tap their badge, then tap
   items to take or return them.
5. Use the **Dashboard** and **Reports** to chase overdue items and review usage.

## Project layout

```
app/
  __init__.py     app factory, CSRF / first-run checks, CLI commands
  models.py       User, Asset, Category, Location, Transaction, AuditLog, Setting
  services.py     check-out / check-in rules and scan handling
  scan.py         scan station page and JSON APIs (scan, reader bridge)
  auth.py         sign-in (password and badge), setup wizard, password change
  main.py         dashboard, my items, asset detail
  admin.py        user / asset / category / location / settings / audit admin
  reports.py      reports and CSV export
  tags.py         tag programming jobs and reader-bridge status
  templates/, static/
nfc_tags.py       NTAG / Ultralight read & write (NDEF) over PC/SC
reader_bridge.py  PC/SC reader -> app bridge (reads, programs tags)
run.py            entry point
start-windows.bat, start-mac.command, start-linux.sh   one-click launchers
tests/            pytest suite
```

## Tests

```bash
pip install pytest
pytest
```

## Security notes

- Passwords are hashed (Werkzeug PBKDF2/scrypt). All forms are CSRF-protected.
- A badge works like a password-less key. Turn off *Allow sign-in by tapping a
  badge* if that is not acceptable for your admins.
- By default the app listens on `127.0.0.1` only. If you expose it on a network,
  put it behind HTTPS. Bridge-based badge sign-in is accepted only from browsers
  on the same machine as the app.
