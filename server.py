import asyncio
import json
import math
import re
import serial
import serial.tools.list_ports
from contextlib import asynccontextmanager
from io import BytesIO
from xml.etree import ElementTree as ET

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
    try:
        yield
    finally:
        reader_task.cancel()
        sender_task.cancel()
        for task in (reader_task, sender_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        for ws in clients[:]:
            try:
                await ws.close()
            except Exception:
                pass
        clients.clear()
        if ser and ser.is_open:
            try:
                ser.close()
                print("Serial port closed.", flush=True)
            except Exception:
                pass


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


# ── SVG preprocessing ──


def parse_svg_path_to_subpaths(d_attr):
    """Parse an SVG path d attribute into a list of subpaths (list of (x,y) tuples).
    Handles M, L, H, V, C, S, Q, T, Z commands (absolute and relative)."""
    subpaths = []
    current = []
    x, y = 0.0, 0.0
    start_x, start_y = 0.0, 0.0

    # Tokenize: split into commands and their numeric arguments
    tokens = re.findall(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d_attr)

    i = 0
    cmd = 'M'

    def next_num():
        nonlocal i
        while i < len(tokens) and tokens[i] in 'MmLlHhVvCcSsQqTtAaZz':
            i += 1
        if i < len(tokens):
            val = float(tokens[i])
            i += 1
            return val
        return 0.0

    while i < len(tokens):
        t = tokens[i]
        if t in 'MmLlHhVvCcSsQqTtAaZz':
            cmd = t
            i += 1
        # else: implicit repeat of last command

        if cmd == 'M':
            if current and len(current) >= 2:
                subpaths.append(current)
            x, y = next_num(), next_num()
            start_x, start_y = x, y
            current = [(x, y)]
            cmd = 'L'  # subsequent coords are line-to
        elif cmd == 'm':
            if current and len(current) >= 2:
                subpaths.append(current)
            x += next_num()
            y += next_num()
            start_x, start_y = x, y
            current = [(x, y)]
            cmd = 'l'
        elif cmd == 'L':
            x, y = next_num(), next_num()
            current.append((x, y))
        elif cmd == 'l':
            x += next_num()
            y += next_num()
            current.append((x, y))
        elif cmd == 'H':
            x = next_num()
            current.append((x, y))
        elif cmd == 'h':
            x += next_num()
            current.append((x, y))
        elif cmd == 'V':
            y = next_num()
            current.append((x, y))
        elif cmd == 'v':
            y += next_num()
            current.append((x, y))
        elif cmd == 'C':
            next_num(); next_num()  # cp1
            next_num(); next_num()  # cp2
            x, y = next_num(), next_num()
            current.append((x, y))
        elif cmd == 'c':
            next_num(); next_num()
            next_num(); next_num()
            dx, dy = next_num(), next_num()
            x += dx; y += dy
            current.append((x, y))
        elif cmd == 'S':
            next_num(); next_num()
            x, y = next_num(), next_num()
            current.append((x, y))
        elif cmd == 's':
            next_num(); next_num()
            dx, dy = next_num(), next_num()
            x += dx; y += dy
            current.append((x, y))
        elif cmd == 'Q':
            next_num(); next_num()
            x, y = next_num(), next_num()
            current.append((x, y))
        elif cmd == 'q':
            next_num(); next_num()
            dx, dy = next_num(), next_num()
            x += dx; y += dy
            current.append((x, y))
        elif cmd == 'T':
            x, y = next_num(), next_num()
            current.append((x, y))
        elif cmd == 't':
            dx, dy = next_num(), next_num()
            x += dx; y += dy
            current.append((x, y))
        elif cmd == 'A':
            next_num(); next_num(); next_num(); next_num(); next_num()
            x, y = next_num(), next_num()
            current.append((x, y))
        elif cmd == 'a':
            next_num(); next_num(); next_num(); next_num(); next_num()
            dx, dy = next_num(), next_num()
            x += dx; y += dy
            current.append((x, y))
        elif cmd in ('Z', 'z'):
            if current:
                current.append((start_x, start_y))
                if len(current) >= 2:
                    subpaths.append(current)
                current = []
            x, y = start_x, start_y
        else:
            i += 1  # skip unknown

    if current and len(current) >= 2:
        subpaths.append(current)

    return subpaths


def apply_transform(subpaths, transform_str):
    """Apply a simple SVG transform string (translate, scale) to subpaths."""
    if not transform_str:
        return subpaths

    result = subpaths
    # Process transforms right-to-left (inner-most first)
    transforms = re.findall(r'(translate|scale|matrix)\(([^)]+)\)', transform_str)
    for ttype, args in reversed(transforms):
        nums = [float(x) for x in re.findall(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', args)]
        if ttype == 'translate':
            tx = nums[0] if len(nums) > 0 else 0
            ty = nums[1] if len(nums) > 1 else 0
            result = [[(x + tx, y + ty) for x, y in sp] for sp in result]
        elif ttype == 'scale':
            sx = nums[0] if len(nums) > 0 else 1
            sy = nums[1] if len(nums) > 1 else sx
            result = [[(x * sx, y * sy) for x, y in sp] for sp in result]
        elif ttype == 'matrix' and len(nums) == 6:
            a, b, c, d, e, f = nums
            result = [[(a*x + c*y + e, b*x + d*y + f) for x, y in sp] for sp in result]

    return result


def douglas_peucker(points, tolerance):
    """Simplify a polyline using Douglas-Peucker algorithm."""
    if len(points) <= 2 or tolerance <= 0:
        return points

    max_dist = 0
    max_idx = 0
    first, last = points[0], points[-1]
    dx, dy = last[0] - first[0], last[1] - first[1]
    len_sq = dx * dx + dy * dy

    for i in range(1, len(points) - 1):
        px, py = points[i]
        if len_sq == 0:
            dist = math.sqrt((px - first[0])**2 + (py - first[1])**2)
        else:
            t = max(0, min(1, ((px - first[0]) * dx + (py - first[1]) * dy) / len_sq))
            proj_x, proj_y = first[0] + t * dx, first[1] + t * dy
            dist = math.sqrt((px - proj_x)**2 + (py - proj_y)**2)
        if dist > max_dist:
            max_dist = dist
            max_idx = i

    if max_dist > tolerance:
        left = douglas_peucker(points[:max_idx + 1], tolerance)
        right = douglas_peucker(points[max_idx:], tolerance)
        return left[:-1] + right
    return [first, last]


@app.post("/api/preprocess-svg")
async def preprocess_svg(file: UploadFile = File(...), simplify: float = Form(0.0)):
    """Parse SVG server-side, split mega-paths into subpaths, return polylines."""
    content = await file.read()
    try:
        tree = ET.parse(BytesIO(content))
    except Exception as e:
        return JSONResponse({"error": f"SVG parse error: {e}"}, status_code=400)

    root = tree.getroot()

    # Collect all paths with their accumulated transforms
    all_subpaths = []

    def walk(el, parent_transform=""):
        tag = el.tag.split('}')[-1]
        el_transform = el.get('transform', '')
        combined = (parent_transform + " " + el_transform).strip()

        if tag == 'path':
            d = el.get('d', '')
            if d:
                subpaths = parse_svg_path_to_subpaths(d)
                if combined:
                    subpaths = apply_transform(subpaths, combined)
                all_subpaths.extend(subpaths)
        elif tag in ('line', 'polyline', 'polygon', 'rect'):
            # Convert simple shapes to points
            pts = []
            if tag == 'line':
                pts = [(float(el.get('x1', 0)), float(el.get('y1', 0))),
                       (float(el.get('x2', 0)), float(el.get('y2', 0)))]
            elif tag == 'rect':
                x, y = float(el.get('x', 0)), float(el.get('y', 0))
                w, h = float(el.get('width', 0)), float(el.get('height', 0))
                pts = [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]
            elif tag in ('polyline', 'polygon'):
                raw = el.get('points', '').strip()
                nums = re.findall(r'[-+]?(?:\d+\.?\d*|\.\d+)', raw)
                for j in range(0, len(nums) - 1, 2):
                    pts.append((float(nums[j]), float(nums[j+1])))
                if tag == 'polygon' and pts:
                    pts.append(pts[0])

            if len(pts) >= 2:
                sp = [pts]
                if combined:
                    sp = apply_transform(sp, combined)
                all_subpaths.extend(sp)

        for child in el:
            walk(child, combined)

    walk(root)

    # Auto-compute tolerance if not provided: 0.1% of the bbox diagonal
    if simplify <= 0:
        min_x0 = min(p[0] for sp in all_subpaths for p in sp) if all_subpaths else 0
        min_y0 = min(p[1] for sp in all_subpaths for p in sp) if all_subpaths else 0
        max_x0 = max(p[0] for sp in all_subpaths for p in sp) if all_subpaths else 0
        max_y0 = max(p[1] for sp in all_subpaths for p in sp) if all_subpaths else 0
        diag = math.sqrt((max_x0 - min_x0)**2 + (max_y0 - min_y0)**2)
        simplify = diag * 0.001  # 0.1% of diagonal

    simplified = []
    for sp in all_subpaths:
        s = douglas_peucker(sp, simplify)
        if len(s) >= 2:
            # Round to 2 decimal places to reduce JSON size
            simplified.append([[round(x, 2), round(y, 2)] for x, y in s])

    # Compute bbox
    min_x = min_y = 0.0
    max_x = max_y = 0.0
    total_pts = 0
    if simplified:
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        for sp in simplified:
            for x, y in sp:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
            total_pts += len(sp)

    return {
        "paths": simplified,
        "bbox": {"minX": min_x, "minY": min_y, "maxX": max_x, "maxY": max_y},
        "stats": {
            "original_paths": len(all_subpaths),
            "simplified_paths": len(simplified),
            "total_points": total_pts,
        }
    }


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["."],
        reload_includes=["*.py", "*.html", "*.js", "*.css"],
    )
