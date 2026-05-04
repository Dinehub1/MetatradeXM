import paramiko
import os

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

local_path = "services/webhooks/win_webhook_mt5.py"
remote_path = "C:\\Users\\Administrator\\win_webhook_mt5.py"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    print("Connected via SSH.")
    
    # Upload file
    sftp = client.open_sftp()
    sftp.put(local_path, remote_path)
    sftp.close()
    print("File uploaded successfully.")
    
    # Get PID of the running webhook
    stdin, stdout, stderr = client.exec_command('powershell -Command "Get-CimInstance Win32_Process | Where-Object CommandLine -like \'*win_webhook_mt5.py*\' | Select-Object -ExpandProperty ProcessId"')
    pids = stdout.read().decode('utf-8').strip().split()
    
    for pid in pids:
        if pid.strip():
            print(f"Killing process {pid}...")
            client.exec_command(f'powershell -Command "Stop-Process -Id {pid} -Force"')
    
    # Start the script in the background
    print("Starting new process...")
    client.exec_command('powershell -Command "Start-Process python -ArgumentList \'C:\\Users\\Administrator\\win_webhook_mt5.py\' -WindowStyle Hidden"')
    print("Deployment completed successfully.")
    
finally:
    client.close()
