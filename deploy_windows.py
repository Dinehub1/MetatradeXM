import paramiko
import os

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"
local_file = "services/webhooks/win_webhook_mt5.py"
remote_file = "C:\\Users\\Administrator\\Desktop\\win_webhook_mt5.py"

print("Connecting to Windows VM...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, password=password)

print("Uploading file...")
sftp = ssh.open_sftp()
sftp.put(local_file, remote_file)
sftp.close()

print("Restarting webhook server...")
# Find process and kill it, then start
kill_cmd = 'taskkill /F /IM python.exe /T'
ssh.exec_command(kill_cmd)

# Allow some time for kill
import time
time.sleep(2)

start_cmd = 'powershell.exe -Command "Start-Process python -ArgumentList \'C:\\Users\\Administrator\\Desktop\\win_webhook_mt5.py\' -RedirectStandardOutput \'C:\\Users\\Administrator\\webhook_out.log\' -RedirectStandardError \'C:\\Users\\Administrator\\webhook_err.log\' -WindowStyle Hidden"'
ssh.exec_command(start_cmd)

print("Done!")
ssh.close()
