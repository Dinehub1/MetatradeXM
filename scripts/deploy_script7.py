import paramiko

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    
    stdin, stdout, stderr = client.exec_command('type C:\\Users\\Administrator\\Desktop\\Start_Webhook.bat')
    print("Batch file contents:")
    print(stdout.read().decode('utf-8'))
    
finally:
    client.close()
