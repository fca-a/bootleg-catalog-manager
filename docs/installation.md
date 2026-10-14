# Installation

There are two ways to get the application. On Windows the quickest path is
the installer; on Linux and macOS you run from source. The application
itself works on all three platforms.

## Install on Windows (installer)

1. Open the [Releases page](https://github.com/fca-a/bootleg-catalog-manager/releases).
2. Download the latest `BootlegCatalogManagerSetup-*.exe` (the version in
   the filename matches the release).
3. Run it. If Windows shows a SmartScreen warning ("Windows protected your
   PC"), click "More info" and then "Run anyway". The warning appears
   because the installer is not code-signed.
4. Choose whether to install for the current user (no administrator rights
   needed) or for all users (Windows asks for administrator rights).
5. Follow the wizard. The application is installed and a Start Menu
   shortcut is created.

### FFmpeg and FFprobe are still required

The installer does not bundle FFmpeg or FFprobe. The catalog reads
technical metadata with FFprobe, and screenshots are generated with
FFmpeg, so install both on Windows as well:

- Download a release build from <https://ffmpeg.org/download.html>.
- Unpack it somewhere stable, for example `C:\ffmpeg`.
- Either add its `bin\` folder to `PATH`, or set the full paths to the
  executables in the application's Settings after the first launch — see
  [Settings](settings.md).

### How to uninstall

Open Windows Settings → Apps → Installed apps, select "Bootleg Catalog
Manager", and choose Uninstall.

## Run from source (Linux, macOS, and developers)

The steps are the same on every platform; only the activation command
differs.

### 1. Clone the repository

```bash
git clone https://github.com/fca-a/bootleg-catalog-manager.git
cd bootleg-catalog-manager
```

### 2. Create a virtual environment

=== "Windows"

    ```bash
    python -m venv .venv
    .venv\Scripts\activate
    ```

=== "Linux / macOS"

    ```bash
    python -m venv .venv
    source .venv/bin/activate
    ```

### 3. Install the dependencies

```bash
pip install -e ".[dev]"
```

The `dev` extra installs the test runner and PyInstaller alongside the
application's own dependencies (PySide6, fpdf2, openpyxl, Pillow).

### FFmpeg and FFprobe

The catalog reads technical metadata with FFprobe, and screenshots are
generated with FFmpeg. Both tools must either be available on `PATH` or be
configured in the application:

- Linux: install the distribution package, for example
  `sudo apt install ffmpeg`.
- macOS: install with Homebrew, `brew install ffmpeg`.
- Windows: download a release build, unpack it, and add its `bin` directory
  to `PATH`.

If the executables are not on `PATH`, set their full paths in the settings
dialog after the first start — see [Settings](settings.md).

### Running

```bash
python -m bootleg_catalog.app.main
```

Continue with [First steps](first-steps.md).

### Building a standalone executable (optional)

```bash
pyinstaller bootleg-catalog-manager.spec
```

The executable is written under `dist/`. Helper scripts for the same build
exist as `scripts/build.bat` (Windows) and `scripts/build.sh` (Linux and
macOS).
