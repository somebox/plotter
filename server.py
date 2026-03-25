import asyncio
import json
import serial
import serial.tools.list_ports
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

BAUD_RATE = 115200
SERIAL_PORT = "/dev/ttyUSB0"

ser = None
clients: list[WebSocket] = []
command_queue: asyncio.Queue = None
stop_event: asyncio.Event = None
ok_event: asyncio.Event = None


def find_printer_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or "").lower()
        if any(kw in desc for kw in ("marlin", "printer", "ch340", "cp210", "arduino", "usb serial", "ft232")):
            return p.device
    if ports:
        return ports[0].device
    return None


async def broadcast(msg_dict):
    text = json.dumps(msg_dict)
    for ws in clients[:]:
        try:
            await ws.send_text(text)
        except Exception:
            if ws in clients:
                clients.remove(ws)


async def serial_reader():
    """Single reader for the serial port. Routes 'ok' to unblock command_sender."""
    loop = asyncio.get_event_loop()
    while True:
        if ser and ser.is_open:
            try:
                line = await loop.run_in_executor(None, ser.readline)
                if line:
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if not text:
                        continue
                    # Signal ok to the command sender
                    if text.startswith("ok"):
                        ok_event.set()
                    # Skip noisy "wait" lines
                    if text == "wait":
                        continue
                    await broadcast({"type": "serial", "data": text})
            except Exception as e:
                await broadcast({"type": "error", "data": f"Serial read error: {e}"})
                await asyncio.sleep(1)
        else:
            await asyncio.sleep(1)


async def command_sender():
    """Send queued commands one at a time, waiting for 'ok' from Marlin."""
    loop = asyncio.get_event_loop()
    while True:
        cmd = await command_queue.get()

        if stop_event.is_set():
            # Drain remaining commands
            while not command_queue.empty():
                try:
                    command_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            continue

        if ser and ser.is_open:
            line = cmd.strip() + "\n"
            ok_event.clear()
            try:
                await loop.run_in_executor(None, ser.write, line.encode("utf-8"))
                await broadcast({"type": "sent", "data": cmd.strip()})
            except Exception as e:
                await broadcast({"type": "error", "data": f"Serial write error: {e}"})
                continue

            # Wait for ok_event (set by serial_reader) or timeout
            try:
                await asyncio.wait_for(ok_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                await broadcast({"type": "error", "data": f"Timeout waiting for ok: {cmd.strip()}"})


async def do_emergency_stop():
    global ser
    stop_event.set()

    while not command_queue.empty():
        try:
            command_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    if ser and ser.is_open:
        try:
            ser.reset_output_buffer()
            ser.reset_input_buffer()
        except Exception:
            pass
        try:
            ser.write(b"M112\n")
            await broadcast({"type": "sent", "data": "M112"})
        except Exception:
            pass
        try:
            ser.write(b"M410\n")
            await broadcast({"type": "sent", "data": "M410"})
        except Exception:
            pass

    await broadcast({"type": "stopped", "data": "Emergency stop triggered"})


async def do_reset():
    global ser
    stop_event.clear()
    ok_event.clear()

    port = SERIAL_PORT or find_printer_port()
    if ser:
        try:
            ser.close()
        except Exception:
            pass

    await asyncio.sleep(1)

    if port:
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=1)
            await broadcast({"type": "status", "connected": True, "port": port})
            await broadcast({"type": "serial", "data": f"Reconnected to {port}"})
        except Exception as e:
            ser = None
            await broadcast({"type": "error", "data": f"Could not reopen {port}: {e}"})
            await broadcast({"type": "status", "connected": False, "port": None})


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ser, command_queue, stop_event, ok_event
    command_queue = asyncio.Queue()
    stop_event = asyncio.Event()
    ok_event = asyncio.Event()

    port = SERIAL_PORT or find_printer_port()
    if port:
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=1)
            print(f"Connected to {port} at {BAUD_RATE} baud", flush=True)
        except Exception as e:
            print(f"Could not open {port}: {e}", flush=True)
            ser = None
    else:
        print("No serial port found. Running in demo mode.", flush=True)

    reader_task = asyncio.create_task(serial_reader())
    sender_task = asyncio.create_task(command_sender())
    yield
    reader_task.cancel()
    sender_task.cancel()
    if ser and ser.is_open:
        ser.close()


app = FastAPI(lifespan=lifespan)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)

    port_name = ser.port if ser and ser.is_open else None
    await ws.send_text(json.dumps({
        "type": "status",
        "connected": ser is not None and ser.is_open,
        "port": port_name,
    }))

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "command":
                await command_queue.put(msg["data"].strip())
            elif msg.get("type") == "emergency_stop":
                await do_emergency_stop()
            elif msg.get("type") == "reset":
                await do_reset()

    except WebSocketDisconnect:
        if ws in clients:
            clients.remove(ws)


@app.get("/api/ports")
async def list_ports():
    ports = serial.tools.list_ports.comports()
    return [{"device": p.device, "description": p.description} for p in ports]


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")
