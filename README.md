# Plotter

Web UI for controlling a 3D printer (Marlin firmware) as a pen plotter over serial.

## Features

- Serial console with real-time output from the printer
- Pen up/down control with adjustable Z heights
- SVG file import with preview, scaling, and fit-to-bed
- Circle drawing with G2 arc commands
- Emergency stop and serial reset
- Command queue with Marlin ok-based flow control

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```bash
.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000

## Configuration

Edit `server.py` to change:
- `SERIAL_PORT` — defaults to `/dev/ttyUSB0`
- `BAUD_RATE` — defaults to `115200`

## Hardware

Developed for a Creality CR-10S Pro (300x300mm bed) but should work with any Marlin-based printer.
