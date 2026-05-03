import paramiko

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    print("Connected successfully.")
    
    # Check running processes to see how it's being run and the working directory
    stdin, stdout, stderr = client.exec_command('wmic process where "name=\'python.exe\'" get commandline,processid')
    print("Running python processes:")
    print(stdout.read().decode('utf-8', errors='ignore'))
    
    # Check for the file in Administrator's folder
    stdin, stdout, stderr = client.exec_command("dir /s /b C:\\Users\\Administrator\\win_webhook_mt5.py")
    output = stdout.read().decode('utf-8', errors='ignore')
    print("Search results:")
    print(output)
    
finally:
    client.close()
