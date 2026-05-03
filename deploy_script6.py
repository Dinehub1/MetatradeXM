import paramiko

host = "206.72.198.54"
user = "Administrator"
password = "q3XtM9f%"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password)
    
    # Check for .bat files on desktop or documents
    stdin, stdout, stderr = client.exec_command('powershell -Command "Get-ChildItem -Path C:\\Users\\Administrator\\Desktop, C:\\Users\\Administrator\\Documents -Filter *.bat -ErrorAction SilentlyContinue"')
    print("Batch files:")
    print(stdout.read().decode('utf-8'))
    
finally:
    client.close()
