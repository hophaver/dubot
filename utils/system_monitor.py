import psutil
import socket
import platform
import subprocess
import time

def get_system_status():
    """Get comprehensive system status"""
    try:
        # CPU usage
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # Memory usage
        memory = psutil.virtual_memory()
        
        # Disk usage
        disk = psutil.disk_usage('/')
        
        # Network info
        hostname = socket.gethostname()
        try:
            ip_address = socket.gethostbyname(hostname)
        except:
            ip_address = "Unknown"
        
        # GPU temperature and usage (if available)
        gpu_temp = get_gpu_temperature()
        gpu_util = get_gpu_utilization()
        
        # Uptime
        uptime_seconds = time.time() - psutil.boot_time()
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        uptime_str = f"{days}d {hours}h {minutes}m"
        
        return {
            "cpu_percent": cpu_percent,
            "memory_used": memory.used // (1024 ** 2),  # MB
            "memory_total": memory.total // (1024 ** 2),  # MB
            "memory_percent": memory.percent,
            "disk_used": disk.used // (1024 ** 3),  # GB
            "disk_total": disk.total // (1024 ** 3),  # GB
            "disk_percent": disk.percent,
            "hostname": hostname,
            "ip_address": ip_address,
            "gpu_temp": gpu_temp,
            "gpu_util": gpu_util,
            "uptime": uptime_str,
            "os": f"{platform.system()} {platform.release()}",
            "python_version": platform.python_version()
        }
    except Exception as e:
        return {"error": str(e)}

def get_gpu_temperature():
    """Get GPU temperature if available"""
    try:
        # Try nvidia-smi first
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            temp = result.stdout.strip()
            if temp.isdigit():
                return f"{temp}°C"
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
        pass
    
    try:
        # Try AMD GPU
        result = subprocess.run(
            ['rocm-smi', '--showtemp', '--csv'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                temp = lines[1].split(',')[1].strip()
                if temp.isdigit():
                    return f"{temp}°C"
    except:
        pass
    
    return "N/A"

def get_gpu_utilization():
    """Get GPU utilization % if available"""
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            util = result.stdout.strip()
            if util.isdigit():
                return f"{util}%"
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
        pass
    return "N/A"
