import paramiko

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    
    # Check scheduled tasks containing 'python' or 'webhook'
    stdin, stdout, stderr = client.exec_command('schtasks /query /v /fo list | findstr /i "TaskName Webhook python MT5"')
    print("Scheduled tasks:")
    print(stdout.read().decode('utf-8', errors='ignore'))
    
finally:
    client.close()
