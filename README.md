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

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open <http://127.0.0.1:5000>. On first run you will be asked to create the
administrator account.

To try it with sample data instead (creates `admin` / `adminadmin` if there is
no admin yet, plus users `alice`, `bob`, `carol` and `dave` with password
`password`):

```bash
flask --app run seed-demo
```

Other useful commands:

```bash
flask --app run create-admin           # add another admin from the terminal
python run.py --host 0.0.0.0           # make it reachable from other PCs on the LAN
pip install waitress && python run.py  # use a production WSGI server (used automatically when installed)
```

The database and the generated secret key are stored in `instance/`. Back up
`instance/assets.db` to back up everything. Set `DATABASE_URL` to use a
different database (e.g. PostgreSQL).

## Connecting the NFC reader

USB NFC readers come in two kinds. Both are supported.

### 1. Keyboard-emulation (HID) readers: nothing to install

Many inexpensive USB readers "type" the tag's UID followed by Enter, as if they
were a keyboard. Open the **Scan** page and tap a tag. The page keeps its scan
field focused, and it also detects reader input when focus is elsewhere.

Tips:
- Set the reader to output the UID in **hex** if it can, and set it up the same
  way on every station. A reader set to decimal gives different UIDs for the
  same tag. Colons, spaces and dashes are removed and letters are upper-cased
  automatically.
- Use **Settings → Test a tag** to see exactly what the app receives.

### 2. PC/SC (smart-card) readers

This includes 2-in-1 **CAC / smart card + NFC** readers (e.g. Arsandyn and
similar "Contact and Contactless IC/ID/PIV" readers), ACR122U, ACR1252U,
HID Omnikey and Identiv. They do not type anything. A small bridge passes their
tag reads to the app. The easiest way is to start it together with the app:

```bash
pip install -r requirements-reader.txt     # pyscard + requests
python run.py --pcsc
```

To check which reader the bridge will use (marked with `*`):

```bash
python reader_bridge.py --list
```

You can also run the bridge on its own:
`python reader_bridge.py --key <reader API key>`. The key is shown under
**Settings → General**.

**2-in-1 contact + contactless readers** appear as two readers, one for the
card slot and one for the NFC antenna. The bridge listens to the antenna and
ignores cards pushed into the slot (CAC, bank cards). If `--list` picks the
wrong one, choose it yourself with `--reader "<part of the name>"`.

Per operating system:
- **Windows / macOS**: no driver needed (the reader is a standard CCID device).
  `pip install pyscard` installs a prebuilt package.
- **Linux**: `sudo apt install pcscd pcsc-tools` and check the reader with
  `pcsc_scan`. If pip has to build pyscard, also install `libpcsclite-dev swig`.
  For ACR122U-type readers, blacklist the `pn533_usb` kernel module.

**Which tags to buy:** 13.56 MHz ISO 14443A tags. NTAG213/215/216 stickers are
cheap and read reliably. Use **on-metal / anti-metal** NFC stickers on laptops
and metal tools, because ordinary stickers don't read on metal. Phones (Apple/Google
Pay) and some bank cards show a random ID on each tap, so they can't be used as
badges. 125 kHz proximity badges (older HID Prox / EM4100) can't be read by a
13.56 MHz reader. The CAC chip in the contact slot is not used for sign-in.

## Typical workflow

1. Admin creates **categories** (with loan periods) and **locations**.
2. Admin adds **assets**: click *Scan tag* on the asset form and tap the sticker,
   or bulk-import a CSV and then scan an unknown tag at the station and choose
   *Register this tag*.
3. Admin adds **users** and taps each person's badge on *Scan badge* (optional;
   users can also sign in with a password).
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
  templates/, static/
reader_bridge.py  PC/SC reader → app bridge
run.py            entry point
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
