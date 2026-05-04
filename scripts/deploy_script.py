import paramiko

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    print("Connected successfully.")
    
    # Try finding the directory where the bot runs. Usually on Desktop or Documents.
    stdin, stdout, stderr = client.exec_command("dir /s /b C:\\win_webhook_mt5.py")
    output = stdout.read().decode('utf-8', errors='ignore')
    print("Search results:")
    print(output)
    
    # Also check running processes to see how it's being run
    stdin, stdout, stderr = client.exec_command('wmic process where "name=\'python.exe\'" get commandline,processid')
    print("Running python processes:")
    print(stdout.read().decode('utf-8', errors='ignore'))
    
finally:
    client.close()
