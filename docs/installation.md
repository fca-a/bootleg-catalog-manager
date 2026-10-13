# Installation

The application runs from a clone of the repository in a virtual
environment. The steps are the same on Windows, Linux, and macOS; only the
activation command differs.

## 1. Clone the repository

```bash
git clone https://github.com/fca-a/bootleg-catalog-manager.git
cd bootleg-catalog-manager
```

## 2. Create a virtual environment

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

## 3. Install the dependencies

```bash
pip install -e ".[dev]"
```

The `dev` extra installs the test runner and PyInstaller alongside the
application's own dependencies (PySide6, fpdf2, openpyxl, Pillow).

## FFmpeg and FFprobe

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

## Running

```bash
python -m bootleg_catalog.app.main
```

Continue with [First steps](first-steps.md).

## Building a standalone executable (optional)

```bash
pyinstaller bootleg-catalog-manager.spec
```

The executable is written under `dist/`. Helper scripts for the same build
exist as `scripts/build.bat` (Windows) and `scripts/build.sh` (Linux and
macOS).
