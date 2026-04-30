import paramiko

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, password=password)

stdin, stdout, stderr = ssh.exec_command('type C:\\Users\\Administrator\\webhook_err.log')
print(stdout.read().decode())
ssh.close()
