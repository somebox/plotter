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
pause_event: asyncio.Event = None  # clear = paused, set = running
ok_event: asyncio.Event = None
direct_lock: asyncio.Lock = None  # serialize direct commands vs queued commands
job_total = 0
job_sent = 0


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


async def broadcast_progress():
    if job_total > 0:
        pct = (job_sent / job_total) * 100
        await broadcast({"type": "progress", "sent": job_sent, "total": job_total, "pct": round(pct, 2)})


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
                    if text.startswith("ok"):
                        ok_event.set()
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
    global job_sent
    loop = asyncio.get_event_loop()
    while True:
        cmd = await command_queue.get()

        if stop_event.is_set():
            while not command_queue.empty():
                try:
                    command_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            continue

        # Wait if paused
        await pause_event.wait()

        if stop_event.is_set():
            continue

        if ser and ser.is_open:
            async with direct_lock:
                line = cmd.strip() + "\n"
                ok_event.clear()
                try:
                    await loop.run_in_executor(None, ser.write, line.encode("utf-8"))
                    job_sent += 1
                    await broadcast({"type": "sent", "data": cmd.strip()})
                    await broadcast_progress()
                except Exception as e:
                    await broadcast({"type": "error", "data": f"Serial write error: {e}"})
                    continue

                try:
                    await asyncio.wait_for(ok_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    await broadcast({"type": "error", "data": f"Timeout waiting for ok: {cmd.strip()}"})


async def do_emergency_stop():
    global ser, job_total, job_sent
    stop_event.set()
    pause_event.set()  # unblock sender so it can drain

    while not command_queue.empty():
        try:
            command_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    job_total = 0
    job_sent = 0

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
    await broadcast_progress()


async def send_gcode(cmd):
    """Send a G-code command directly, bypassing the queue.
    Uses direct_lock to prevent interference with the queued command sender."""
    loop = asyncio.get_event_loop()
    async with direct_lock:
        if ser and ser.is_open:
            ok_event.clear()
            try:
                await loop.run_in_executor(None, ser.write, (cmd.strip() + "\n").encode("utf-8"))
                await broadcast({"type": "sent", "data": cmd.strip()})
            except Exception:
                return
            try:
                await asyncio.wait_for(ok_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass


async def do_pause(pen_up_z, z_speed):
    pause_event.clear()
    # Lift pen
    await send_gcode("G90")
    await send_gcode(f"G1 Z{pen_up_z} F{z_speed}")
    await broadcast({"type": "paused"})


async def do_resume(pen_down_z, z_speed):
    # Lower pen
    await send_gcode("G90")
    await send_gcode(f"G1 Z{pen_down_z} F{z_speed}")
    await broadcast({"type": "resumed"})
    pause_event.set()


async def do_reset():
    global ser, job_total, job_sent
    stop_event.clear()
    pause_event.set()
    ok_event.clear()
    job_total = 0
    job_sent = 0

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

    await broadcast_progress()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ser, command_queue, stop_event, pause_event, ok_event, direct_lock
    command_queue = asyncio.Queue()
    stop_event = asyncio.Event()
    pause_event = asyncio.Event()
    pause_event.set()  # start unpaused
    ok_event = asyncio.Event()
    direct_lock = asyncio.Lock()

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
            elif msg.get("type") == "direct_command":
                # Bypass queue — for use while paused
                await send_gcode(msg["data"].strip())
            elif msg.get("type") == "job_start":
                global job_total, job_sent
                job_total = msg.get("total", 0)
                job_sent = 0
                stop_event.clear()
                pause_event.set()
                await broadcast_progress()
            elif msg.get("type") == "pause":
                await do_pause(msg.get("pen_up_z", 5), msg.get("z_speed", 300))
            elif msg.get("type") == "resume":
                await do_resume(msg.get("pen_down_z", 0), msg.get("z_speed", 300))
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
