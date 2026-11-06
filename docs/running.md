# Running on Windows and Linux

Both platforms use the same Python package, SQLite format, TOML settings, and
browser dashboard. BLE is the default transport. Start with a computer that has
a working Bluetooth adapter; the radio must have Bluetooth enabled. Close the
phone control app and other loggers before connecting.

## Windows

Install Python 3.11 or later and Git, then open Command Prompt in the checkout:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[bluetooth]"
scan-radios.cmd
start-mobile.cmd
```

The scanner lets you choose a radio and mobile/fixed profile. After selection,
use `start-mobile.cmd` for mobile or `start-fixed.cmd` for fixed. You can also
double-click these files. Bluetooth must be enabled in Windows Settings; follow
the radio's pairing instructions if Windows requests pairing. The user has
confirmed UV-PRO logging on Windows.

In PowerShell, prefix local commands with `./` (or `.\`):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[bluetooth]"
.\scan-radios.cmd
.\start-mobile.cmd
```

These commands do not require virtual-environment activation or PowerShell
execution-policy changes. For direct CLI commands without activation:

```powershell
.\.venv\Scripts\python.exe -m gps_rssi.cli --help
```

## Linux, including Raspberry Pi

Use Python 3.11+ on Debian 12+, Ubuntu 24.04+, or Raspberry Pi OS Bookworm 64-bit.
On these distributions, install the prerequisites on the station computer:

```sh
sudo apt update
sudo apt install python3 python3-venv python3-pip git bluez rfkill
sudo systemctl enable --now bluetooth
python3 --version
```

BlueZ and its system D-Bus service provide Bluetooth access. Other Linux
distributions need equivalent packages and service setup. WSL and containers
need explicit access to a Bluetooth adapter and BlueZ; a normal Windows Python
installation is simpler when that access is unavailable.

From the checkout, create an environment and install the package:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[bluetooth]"
./scan-radios.sh
```

Choose the appropriate profile during selection, then launch it:

```sh
./start-mobile.sh
# Or, on the fixed-site computer:
./start-fixed.sh
```

Use a terminal for the Linux launchers; file-manager double-click behavior varies.
Git preserves their executable permissions. If a ZIP extraction loses those
permissions, restore them with:

```sh
chmod +x start-mobile.sh start-fixed.sh start-uvpro.sh start-uv50pro.sh scan-radios.sh scripts/run-gps-rssi.sh
```

Run the application as your normal user. If scanning cannot find a controller,
check `bluetoothctl show`, `systemctl status bluetooth`, and
`rfkill list bluetooth`. A soft block can be cleared with
`sudo rfkill unblock bluetooth`; a hard block needs the hardware switch or
firmware setting changed. If D-Bus reports access denied, check your
distribution's BlueZ user/session policy rather than running the logger as root.

Some radio/OS combinations require pairing and trusting first. If needed, use
`bluetoothctl` interactively:

```text
power on
agent on
default-agent
scan on
```

After the radio appears, enter `scan off`, then `pair RADIO_ADDRESS` and
`trust RADIO_ADDRESS`, replacing `RADIO_ADDRESS` with the scanned address.
Follow any prompts and the radio manual, then `quit`. Let GPS-RSSI establish
the connection. Do not keep another Bluetooth controller app connected.

Physical-radio BLE testing on Linux is still needed. The automated Linux tests
exercise simulated radio connections and application shutdown. RFCOMM is an
advanced alternative requiring the radio's actual command channel and OS socket
support; it is not the recommended cross-platform starting configuration.

## CLI commands and settings

The launchers do not install or update dependencies. Refresh an existing
installation using its OS-specific Python path with `-m pip install -e
".[bluetooth]"`. Existing personal TOML settings and data are retained.

For the short `gps-rssi` command used throughout the README, activate first:

```sh
# Linux
. .venv/bin/activate
gps-rssi --help
```

```bat
REM Windows Command Prompt
.venv\Scripts\activate.bat
gps-rssi --help
```

Without activation, Linux can run `.venv/bin/python -m gps_rssi.cli --help`;
Windows can use `.venv\Scripts\python.exe -m gps_rssi.cli --help`.

Both platforms create `mobile.toml` or `fixed.toml` beside the root launcher,
with the supplied Bluetooth addresses in the profile defaults. The selected
address is saved there, not in launcher code. Database paths are relative to
that settings file. Launchers also work when invoked by absolute path from
another directory. Optional arguments can be appended to Linux launchers, for
example `./start-mobile.sh --no-browser`.

Existing `uvpro.toml` and `uv50pro.toml` files are copied to missing role-named
files on first launch or selection. Originals remain intact; existing
`mobile.toml`/`fixed.toml` files take precedence. Database paths stay unchanged.
The old `start-uvpro` and `start-uv50pro` launchers forward to `start-mobile`
and `start-fixed`. Once migrated, edit the role-named settings. Any supported
radio can be selected for either role.

## Headless fixed-site Raspberry Pi

On the Pi, use:

```sh
./start-fixed.sh --no-browser
```

The dashboard stays on the Pi's loopback interface. On your laptop, open an SSH
tunnel (replace `pi-host` and `user` with your actual host and account):

```sh
ssh -L 8766:127.0.0.1:8766 user@pi-host
```

While that SSH session remains open, browse to `http://127.0.0.1:8766` on the
laptop. Use port 8765 instead for the mobile profile, or the port in your setup.

For a long recording, run the launcher inside `tmux` on the Pi so it survives
an SSH disconnect: install `tmux`, run `tmux new -s gps-rssi`, then launch the
logger. Detach with Ctrl-B followed by D; return with `tmux attach -t gps-rssi`.
The HTTP tunnel still needs its own live SSH session when viewing remotely.

On either OS, **Exit app** in the dashboard or **Ctrl-C once** in the launcher
console shuts down both services and disconnects the radio. Closing a browser
tab does not stop recording. Use **Reset database** only when you want a fresh
log; it creates a verified backup before clearing measurements.

## Verification

GitHub Actions is configured to run Python tests, dashboard tests, and launcher
help checks on Windows and Linux with Python 3.11 and 3.12. These checks do not
use a real radio. Before a long field run on each new computer, scan and select
the radio, launch the chosen profile, confirm new RSSI samples and (for mobile)
GPS points, then exit and relaunch to confirm the Bluetooth connection recovers.
