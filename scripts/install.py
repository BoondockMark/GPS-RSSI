"""Install or refresh this checkout without activating its virtual environment."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import venv

PROJECT_DIR = Path(__file__).resolve().parents[1]
IS_WINDOWS = sys.platform == "win32"


def run(command, project):
    subprocess.run([str(value) for value in command], cwd=project, check=True)


def install(project, *, basic=False):
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or later is required. Install it, then run this installer again.")
    if shutil.which("git") is None:
        raise RuntimeError("Git is required for the pinned Bluetooth dependency. Install Git, then rerun this installer.")
    environment = project / ".venv"
    python = environment / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    other = environment / ("bin/python" if IS_WINDOWS else "Scripts/python.exe")
    if other.exists() or other.is_symlink():
        raise RuntimeError("This .venv belongs to another operating system. Rename it, then rerun the installer.")
    if not python.is_file():
        print("Creating the project virtual environment...", flush=True)
        try:
            venv.EnvBuilder(with_pip=True).create(environment)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("Could not create .venv. On Debian/Ubuntu/Raspberry Pi OS, install "
                               "python3-venv first, then rerun the installer. " + str(exc)) from exc
    print("Checking the project Python and pip...", flush=True)
    run([python, "-c", "import sys, importlib.util\n"
         "if sys.version_info < (3, 11):\n"
         "    raise SystemExit('Existing .venv needs Python 3.11+. Rename it and rerun the installer.')\n"
         "if importlib.util.find_spec('pip') is None:\n"
         "    import ensurepip; ensurepip.bootstrap(upgrade=True)\n"], project)
    print("Updating pip...", flush=True)
    run([python, "-m", "pip", "install", "--upgrade", "pip"], project)
    extras = ".[bluetooth]" if basic else ".[bluetooth,audio]"
    print("Installing GPS-RSSI with " + ("Bluetooth" if basic else "Bluetooth and audio") + " support...", flush=True)
    run([python, "-m", "pip", "install", "--upgrade", "-e", extras], project)
    print("Checking dependencies, radio APIs, and application assets...", flush=True)
    run([python, "-m", "pip", "check"], project)
    checks = """from importlib.resources import files
from benlink.command import CommandConnection
import tomlkit
for method in ('get_position', 'get_channel'):
    if not callable(getattr(CommandConnection, method, None)):
        raise RuntimeError('Benlink is missing required API: ' + method)
assets = files('gps_rssi').joinpath('assets')
for name in ('workspace.html', 'workspace.js', 'dashboard.html', 'dashboard.js', 'survey.html',
             'survey.js', 'basemap.js', 'survey-review.js', 'visualization.js', 'leaflet/leaflet.js'):
    if not assets.joinpath(name).is_file():
        raise RuntimeError('Missing application asset: ' + name)
from gps_rssi.cli import parser
parser()
"""
    if not basic:
        checks += "import numpy, scipy, av\n"
    run([python, "-c", checks], project)
    print("\nInstallation complete. Start GPS-RSSI with " + ("start-app.cmd" if IS_WINDOWS else "./start-app.sh") + ".")
    print("Restart any running workspace to load the update. Settings, recordings, and cached tiles are retained.")
    print("For updated survey maps, create a new survey in the app's Surveys & exports tab.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install or refresh GPS-RSSI from this checkout. "
                                    "Close the workspace before installing; no virtual-environment activation is needed.")
    parser.add_argument("--basic", action="store_true", help="Install Bluetooth support without the optional audio dependencies")
    args = parser.parse_args(argv)
    try:
        install(PROJECT_DIR, basic=args.basic)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print("Installation failed: " + str(exc), file=sys.stderr)
        print("Resolve the error above, then rerun the installer.", file=sys.stderr)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) and exc.returncode > 0 else 1
    except KeyboardInterrupt:
        print("\nInstallation interrupted. Run the installer again when ready.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
