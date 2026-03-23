# Objetivo: expor métricas via GET /stats usando OpenHardwareMonitor DLL + psutil
# Técnica: OHM via pythonnet (clr) para cpu_temp, cpu_load, mem_load
#          psutil para disk e uptime
#          Flask retorna JSON; OHM inicializado uma vez no módulo

import clr
import psutil
import time
from flask import Flask, jsonify

# --- OHM: inicialização única ---
clr.AddReference(r'C:\OpenHardwareMonitor\OpenHardwareMonitorLib.dll')
from OpenHardwareMonitor import Hardware
from OpenHardwareMonitor.Hardware import SensorType

computer = Hardware.Computer()
computer.CPUEnabled    = True
computer.RAMEnabled    = True   # Generic Memory
computer.MainboardEnabled = False
computer.GPUEnabled    = False
computer.HDDEnabled    = False
computer.Open()

# --- Função de coleta OHM ---
def get_ohm_stats() -> dict:
    # Retorna dict com cpu_temp, cpu_load, mem_load como float
    # Valores None se sensor não encontrado
    cpu_temp = None
    cpu_load = None
    mem_load = None

    for hw in computer.Hardware:
        hw.Update()
        for sensor in hw.Sensors:
            # CPU — ajuste o nome do hardware se necessário
            if 'Intel Core i3-7020U' in hw.Name:
                if (sensor.Name == 'CPU Package'
                        and sensor.SensorType == SensorType.Temperature
                        and cpu_temp is None):
                    cpu_temp = float(sensor.Value)
                elif (sensor.Name == 'CPU Total'
                        and sensor.SensorType == SensorType.Load
                        and cpu_load is None):
                    cpu_load = float(sensor.Value)
            # RAM
            elif 'Generic Memory' in hw.Name:
                if ('Memory' in sensor.Name
                        and sensor.SensorType == SensorType.Load
                        and mem_load is None):
                    mem_load = float(sensor.Value)

    return {
        'cpu_temp': round(cpu_temp, 1) if cpu_temp is not None else 0.0,
        'cpu_load': round(cpu_load, 1) if cpu_load is not None else 0.0,
        'mem_load': round(mem_load, 1) if mem_load is not None else 0.0,
    }

# --- Flask ---
app = Flask(__name__)

@app.route('/stats')
def stats():
    ohm  = get_ohm_stats()
    disk = psutil.disk_usage('C:\\')

    return jsonify({
        'cpu_temp': ohm['cpu_temp'],
        'cpu_load': ohm['cpu_load'],
        'mem_load': ohm['mem_load'],
        'disk':     round(disk.percent, 1),
        'uptime':   int(time.time() - psutil.boot_time()),
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
