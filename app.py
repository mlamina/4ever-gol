from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import threading
import time
import json
import asyncio

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize SQLite database
conn = sqlite3.connect('grid.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS grid (x INTEGER, y INTEGER, state INTEGER)''')
conn.commit()

# Initialize grid
grid_size = 1000
grid = [[0 for _ in range(grid_size)] for _ in range(grid_size)]

# Load grid state from database
cursor.execute('SELECT * FROM grid')
rows = cursor.fetchall()
for row in rows:
    x, y, state = row
    grid[x][y] = state

# WebSocket manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# Background thread to update the grid
def update_grid():
    while True:
        new_grid = [[0 for _ in range(grid_size)] for _ in range(grid_size)]
        for i in range(grid_size):
            for j in range(grid_size):
                live_neighbors = sum([
                    grid[i-1][j-1], grid[i-1][j], grid[i-1][j+1],
                    grid[i][j-1], grid[i][j+1],
                    grid[i+1][j-1], grid[i+1][j], grid[i+1][j+1]
                ])
                if grid[i][j] == 1 and live_neighbors in [2, 3]:
                    new_grid[i][j] = 1
                elif grid[i][j] == 0 and live_neighbors == 3:
                    new_grid[i][j] = 1
        global grid
        grid = new_grid
        time.sleep(0.1)

threading.Thread(target=update_grid, daemon=True).start()

# Background thread to send grid state to clients
def send_grid_state():
    while True:
        grid_state = json.dumps(grid)
        asyncio.run(manager.broadcast(grid_state))
        time.sleep(0.5)

threading.Thread(target=send_grid_state, daemon=True).start()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            x, y = message['x'], message['y']
            grid[x][y] = 1 - grid[x][y]
            cursor.execute('REPLACE INTO grid (x, y, state) VALUES (?, ?, ?)', (x, y, grid[x][y]))
            conn.commit()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
