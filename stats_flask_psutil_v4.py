# stats_flask_psutil_v4.py
# Serves GET /stats JSON via Flask + two tray icons (cpu_temp or cpu_load, ram_load)
# OHM requires admin; if not admin, falls back to psutil for cpu_load only
# Tray icons: left=CPU, right=RAM — both have "Exit" menu item
# Dependencies: pythonnet, psutil, pystray, Pillow, flask

import ctypes
import sys
import time
import threading
import psutil
import pystray
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, jsonify

# ── admin check ──────────────────────────────────────────────────────────────
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

ADMIN = is_admin()

# ── OHM (only when admin) ────────────────────────────────────────────────────
computer = None
SensorType = None

if ADMIN:
    try:
        import clr
        clr.AddReference(r'C:\OpenHardwareMonitor\OpenHardwareMonitorLib.dll')
        from OpenHardwareMonitor import Hardware
        from OpenHardwareMonitor.Hardware import SensorType as _ST

        SensorType = _ST
        computer = Hardware.Computer()
        computer.CPUEnabled       = True
        computer.RAMEnabled       = True
        computer.MainboardEnabled = True
        computer.GPUEnabled       = True
        computer.HDDEnabled       = True
        computer.Open()
    except Exception as e:
        print(f"[OHM] init failed: {e}")
        computer = None

# ── OHM stats ────────────────────────────────────────────────────────────────
def get_ohm_stats() -> dict:
    result = {
        'cpu_temp':       None,
        'cpu_load':       None,
        'cpu_fan':        None,
        'cpu_power_w':    None,
        'gpu_temp':       None,
        'gpu_load':       None,
        'gpu_fan':        None,
        'gpu_power_w':    None,
        'gpu_mem_load':   None,
        'ram_load_ohm':   None,
        'mainboard_temp': None,
        'hdd_temp':       {},
    }

    if computer is None or SensorType is None:
        return result

    try:
        for hw in computer.Hardware:
            try:
                hw.Update()
            except Exception:
                continue

            name = hw.Name or ''

            for sensor in hw.Sensors:
                try:
                    sname = sensor.Name or ''
                    stype = sensor.SensorType
                    raw   = sensor.Value
                    if raw is None:
                        continue
                    val = float(raw)

                    # CPU
                    if hw.HardwareType in (
                        Hardware.HardwareType.CPU,
                    ) or 'Core' in name or 'Intel' in name or ('AMD' in name and 'GPU' not in name):
                        if stype == SensorType.Temperature and 'Package' in sname and result['cpu_temp'] is None:
                            result['cpu_temp'] = round(val, 1)
                        elif stype == SensorType.Load and 'Total' in sname and result['cpu_load'] is None:
                            result['cpu_load'] = round(val, 1)
                        elif stype == SensorType.Fan and result['cpu_fan'] is None:
                            result['cpu_fan'] = round(val)
                        elif stype == SensorType.Power and 'Package' in sname and result['cpu_power_w'] is None:
                            result['cpu_power_w'] = round(val, 1)

                    # GPU
                    elif 'NVIDIA' in name or 'Radeon' in name or (hw.HardwareType == Hardware.HardwareType.GpuNvidia) or (hw.HardwareType == Hardware.HardwareType.GpuAti):
                        if stype == SensorType.Temperature and result['gpu_temp'] is None:
                            result['gpu_temp'] = round(val, 1)
                        elif stype == SensorType.Load and 'Core' in sname and result['gpu_load'] is None:
                            result['gpu_load'] = round(val, 1)
                        elif stype == SensorType.Fan and result['gpu_fan'] is None:
                            result['gpu_fan'] = round(val)
                        elif stype == SensorType.Power and result['gpu_power_w'] is None:
                            result['gpu_power_w'] = round(val, 1)
                        elif stype == SensorType.Load and 'Memory' in sname and result['gpu_mem_load'] is None:
                            result['gpu_mem_load'] = round(val, 1)

                    # RAM
                    elif 'Generic Memory' in name or hw.HardwareType == Hardware.HardwareType.RAM:
                        if stype == SensorType.Load and result['ram_load_ohm'] is None:
                            result['ram_load_ohm'] = round(val, 1)

                    # HDD/SSD
                    elif hw.HardwareType in (Hardware.HardwareType.HDD,):
                        if stype == SensorType.Temperature:
                            result['hdd_temp'][name] = round(val, 1)

                    # Mainboard
                    elif hw.HardwareType == Hardware.HardwareType.Mainboard or 'Mainboard' in name:
                        if stype == SensorType.Temperature and result['mainboard_temp'] is None:
                            result['mainboard_temp'] = round(val, 1)

                except Exception:
                    continue
    except Exception:
        pass

    if not result['hdd_temp']:
        result['hdd_temp'] = None

    return result

# ── psutil helpers ───────────────────────────────────────────────────────────
def get_psutil_stats() -> dict:
    try:
        disk_c = round(psutil.disk_usage('C:\\').percent, 1)
    except Exception:
        disk_c = None

    try:
        uptime = int(time.time() - psutil.boot_time())
    except Exception:
        uptime = None

    try:
        mem = psutil.virtual_memory()
        mem_load    = round(mem.percent, 1)
        ram_total_gb = round(mem.total / 1e9, 2)
        ram_used_gb  = round(mem.used  / 1e9, 2)
    except Exception:
        mem_load = ram_total_gb = ram_used_gb = None

    try:
        cpu_load_ps = round(psutil.cpu_percent(interval=None), 1)
    except Exception:
        cpu_load_ps = None

    return {
        'disk_c_pct':   disk_c,
        'uptime':       uptime,
        'mem_load':     mem_load,
        'ram_total_gb': ram_total_gb,
        'ram_used_gb':  ram_used_gb,
        'cpu_load_ps':  cpu_load_ps,
    }

# ── shared state for tray icons ───────────────────────────────────────────────
state = {
    'cpu_val':  0,
    'cpu_label': 'CPU%' if not ADMIN else 'CPU°',
    'ram_val':  0,
}

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route('/stats')
def stats():
    ohm = get_ohm_stats()
    ps  = get_psutil_stats()

    # cpu display value for tray
    cpu_display = ohm['cpu_temp'] if ohm['cpu_temp'] is not None else (
                  ohm['cpu_load'] if ohm['cpu_load'] is not None else
                  ps['cpu_load_ps'])
    ram_display = ohm['ram_load_ohm'] if ohm['ram_load_ohm'] is not None else ps['mem_load']

    state['cpu_val'] = int(cpu_display) if cpu_display is not None else 0
    state['ram_val'] = int(ram_display) if ram_display is not None else 0
    state['cpu_label'] = 'CPU°' if (ADMIN and ohm['cpu_temp'] is not None) else 'CPU%'

    payload = {
        # OHM-sourced (None if not admin or sensor missing)
        'cpu_temp':       ohm['cpu_temp'],
        'cpu_load':       ohm['cpu_load'],
        'cpu_fan':        ohm['cpu_fan'],
        'cpu_power_w':    ohm['cpu_power_w'],
        'gpu_temp':       ohm['gpu_temp'],
        'gpu_load':       ohm['gpu_load'],
        'gpu_fan':        ohm['gpu_fan'],
        'gpu_power_w':    ohm['gpu_power_w'],
        'gpu_mem_load':   ohm['gpu_mem_load'],
        'mainboard_temp': ohm['mainboard_temp'],
        'hdd_temp':       ohm['hdd_temp'],
        # psutil-sourced
        'mem_load':       ps['mem_load'],
        'ram_total_gb':   ps['ram_total_gb'],
        'ram_used_gb':    ps['ram_used_gb'],
        'disk':           ps['disk_c_pct'],
        'uptime':         ps['uptime'],
    }

    return jsonify(payload)

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# ── tray icon helpers ─────────────────────────────────────────────────────────
def make_icon_image(value: int, hot: bool = False) -> Image.Image:
    size = 64
    img  = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg = (180, 0, 0) if hot else (0, 80, 160)
    draw.ellipse((0, 0, size - 1, size - 1), fill=bg)

    try:
        font = ImageFont.truetype('arialbd.ttf', 26)
    except Exception:
        font = ImageFont.load_default()

    text = str(value)
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = 20, 20

    draw.text(((size - tw) / 2, (size - th) / 2 - 2), text, font=font, fill=(255, 255, 255))
    return img

def make_tray_cpu(exit_fn):
    icon = pystray.Icon(
        'cpu',
        make_icon_image(0),
        title='CPU',
        menu=pystray.Menu(
            pystray.MenuItem('Exit', exit_fn)
        )
    )
    return icon

def make_tray_ram(exit_fn):
    icon = pystray.Icon(
        'ram',
        make_icon_image(0),
        title='RAM',
        menu=pystray.Menu(
            pystray.MenuItem('Exit', exit_fn)
        )
    )
    return icon

def updater(icon_cpu, icon_ram):
    """Polls state and redraws tray icons every 3 s."""
    while True:
        try:
            cpu = state['cpu_val']
            ram = state['ram_val']
            lbl = state['cpu_label']

            icon_cpu.icon  = make_icon_image(cpu, hot=(cpu > 75))
            icon_cpu.title = f'{lbl}: {cpu}'

            if icon_ram is not None:
                icon_ram.icon  = make_icon_image(ram, hot=(ram > 85))
                icon_ram.title = f'RAM%: {ram}'
        except Exception:
            pass
        time.sleep(3)

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    # Start Flask in daemon thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Warm-up psutil cpu_percent (first call returns 0.0)
    psutil.cpu_percent(interval=None)

    icons = []

    def stop_all(icon=None, item=None):
        for ic in icons:
            try:
                ic.stop()
            except Exception:
                pass
        sys.exit(0)

    icon_cpu = make_tray_cpu(stop_all)
    icons.append(icon_cpu)

    # Second icon only makes sense when admin (OHM gives real RAM load)
    # but we also show it without admin using psutil mem_load
    icon_ram = make_tray_ram(stop_all)
    icons.append(icon_ram)

    # Updater thread
    threading.Thread(
        target=updater,
        args=(icon_cpu, icon_ram),
        daemon=True
    ).start()

    # Run RAM icon in its own thread; CPU icon blocks main thread (pystray requirement)
    threading.Thread(target=icon_ram.run, daemon=True).start()
    icon_cpu.run()   # blocks until stop()

if __name__ == '__main__':
    main()
