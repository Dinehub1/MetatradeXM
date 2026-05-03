import paramiko
import time

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    
    # Kill any existing python processes just in case
    client.exec_command('powershell -Command "Stop-Process -Name python -Force -ErrorAction SilentlyContinue"')
    time.sleep(2)
    
    # Create an interactive scheduled task
    cmd_create = 'schtasks /create /tn "StartWebhook" /tr "C:\\Users\\Administrator\\Desktop\\Start_Webhook.bat" /sc once /st 00:00 /it /rl highest /f'
    stdin, stdout, stderr = client.exec_command(cmd_create)
    print("Create task:", stdout.read().decode('utf-8'))
    
    # Run the task
    cmd_run = 'schtasks /run /tn "StartWebhook"'
    stdin, stdout, stderr = client.exec_command(cmd_run)
    print("Run task:", stdout.read().decode('utf-8'))
    
    # Delete the task
    time.sleep(2)
    client.exec_command('schtasks /delete /tn "StartWebhook" /f')
    print("Task deleted.")
    
finally:
    client.close()
