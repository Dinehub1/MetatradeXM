import paramiko

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    
    stdin, stdout, stderr = client.exec_command('powershell -Command "Get-ChildItem -Path C:\\Users -Filter win_webhook_mt5.py -Recurse -ErrorAction SilentlyContinue | Select-Object FullName"')
    output = stdout.read().decode('utf-8', errors='ignore')
    print("Powershell search:")
    print(output)
    
    # Also get the exact command lines of running python processes using powershell
    stdin, stdout, stderr = client.exec_command('powershell -Command "Get-CimInstance Win32_Process | Where-Object Name -eq \'python.exe\' | Select-Object CommandLine"')
    print("Python process commands:")
    print(stdout.read().decode('utf-8', errors='ignore'))
    
finally:
    client.close()
