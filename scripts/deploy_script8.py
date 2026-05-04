import paramiko
import time

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    
    # Kill existing
    client.exec_command('powershell -Command "Stop-Process -Name python -Force -ErrorAction SilentlyContinue"')
    time.sleep(2)
    
    # Start it using pythonw (which runs without a console window)
    client.exec_command('powershell -Command "Start-Process pythonw -ArgumentList \'C:\\Users\\Administrator\\win_webhook_mt5.py\'"')
    print("Started using pythonw")
    
finally:
    client.close()
