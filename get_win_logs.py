import paramiko
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("206.72.198.54", username="Administrator", password="q3XtM9f%")
stdin, stdout, stderr = client.exec_command(r'type C:\Users\Administrator\Desktop\Start_Webhook.bat')
print(stdout.read().decode('utf-8'))
client.close()
