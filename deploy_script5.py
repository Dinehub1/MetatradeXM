import paramiko
import time

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    
    # Check if python is running
    stdin, stdout, stderr = client.exec_command('powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, ProcessName"')
    print("Python processes:")
    print(stdout.read().decode('utf-8'))
    
    # If not running, let's start it interactively and see the output
    stdin, stdout, stderr = client.exec_command('python C:\\Users\\Administrator\\win_webhook_mt5.py')
    
    # Wait a bit to collect output
    time.sleep(3)
    
    # Read whatever is available
    import select
    
    if stdout.channel.recv_ready():
        print("Output:", stdout.channel.recv(4096).decode('utf-8'))
    if stderr.channel.recv_ready():
        print("Error:", stderr.channel.recv(4096).decode('utf-8'))
        
finally:
    client.close()
