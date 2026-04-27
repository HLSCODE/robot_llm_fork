import socket
import json
import threading


class TCPClient:
    def __init__(self, host='192.168.1.216', port=12345, bind_port=None):
        self.host = host
        self.port = port
        self.bind_port = bind_port
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # 允许端口复用
        # Bind to a specific port if provided
        if self.bind_port is not None:
            self.client_socket.bind(('', self.bind_port))  # Bind to all interfaces on the specified port
            print(f"Client bound to port {self.bind_port}")

    def connect(self):
        try:
            self.client_socket.connect((self.host, self.port))
            print(f"Connected to server at {self.host}:{self.port}")
        except Exception as e:
            print(f"Connection failed: {e}")
            raise

    def send_command(self, command):
        try:
            self.client_socket.send(json.dumps(command).encode('utf-8'))
            print(f"Command sent: {command}")
        except Exception as e:
            print(f"Failed to send command: {e}")

    def listen_for_responses(self):
        while True:
            try:
                data = self.client_socket.recv(1024).decode('utf-8')
                if data:
                    response = json.loads(data)
                    print(f"Received response: {response}")
                    if 'result' in response:
                        print("Command execution completed.")
                        return response  # 返回响应而不是直接break
            except Exception as e:
                print(f"Error receiving response: {e}")
                return None

    def close(self):
        try:
            self.client_socket.close()
            print("Client socket closed.")
        except Exception as e:
            print(f"Error closing client socket: {e}")


if __name__ == "__main__":
    # Specify the server's IP and port
    server_host = '192.168.1.216'
    server_port = 12345

    # Specify the client's bind port
    client_bind_port = 54321  # Example: Bind the client to port 54321

    # Create the client instance with the bind port
    client = TCPClient(host=server_host, port=server_port, bind_port=client_bind_port)

    # Connect to the server
    client.connect()

    # Example command
    command = {"cmd": 1, "id": 2, "cid": 0}

    # Send command
    client.send_command(command)

    # Listen for responses
    response = client.listen_for_responses()

    # Close connection
    client.close()