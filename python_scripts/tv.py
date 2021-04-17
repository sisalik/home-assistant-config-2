import socket

# create an INET, STREAMing socket
s = socket.socket(
    socket.AF_INET, socket.SOCK_STREAM)
# now connect to the web server on port 80
# - the normal http port
s.connect(("192.168.1.117", 9761))
s.send(b'xb 1 A1\r')
# print(s.recv(1024).decode())
