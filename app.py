import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import threading
import time
import json
import asyncio
from typing import List


logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GridManager:
    def __init__(self, db_path: str, grid_size: int = 1000):
        self.grid_size = grid_size
        self.grid_semaphore = threading.Semaphore()
        self.grid = [[0 for _ in range(grid_size)] for _ in range(grid_size)]
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._initialize_db()
        self._load_grid_state()

    def _initialize_db(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS grid (x INTEGER, y INTEGER, state INTEGER)''')
        self.conn.commit()

    def _load_grid_state(self):
        self.cursor.execute('SELECT * FROM grid')
        rows = self.cursor.fetchall()
        for row in rows:
            x, y, state = row
            self.grid[x][y] = state

    def update_grid(self):
        while True:
            self.grid_semaphore.acquire()
            new_grid = [[0 for _ in range(self.grid_size)] for _ in range(self.grid_size)]
            for i in range(self.grid_size):
                for j in range(self.grid_size):
                    live_neighbors = 0
                    for dx in [-1, 0, 1]:
                        for dy in [-1, 0, 1]:
                            if dx == 0 and dy == 0:
                                continue
                            ni, nj = i + dx, j + dy
                            if 0 <= ni < self.grid_size and 0 <= nj < self.grid_size:
                                live_neighbors += self.grid[ni][nj]
                    if self.grid[i][j] == 1 and live_neighbors in [2, 3]:
                        new_grid[i][j] = 1
                    elif self.grid[i][j] == 0 and live_neighbors == 3:
                        new_grid[i][j] = 1

            self.grid = new_grid
            self.grid_semaphore.release()
            logger.info("Grid updated")
            time.sleep(1)

    def save_cell_state(self, x: int, y: int):
        self.cursor.execute('REPLACE INTO grid (x, y, state) VALUES (?, ?, ?)', (x, y, self.grid[x][y]))
        self.conn.commit()

    def get_grid_state(self) -> str:
        return json.dumps(self.grid)

    def flip_cell(self, x: int, y: int):
        self.grid_semaphore.acquire()
        self.grid[x][y] = 1 - self.grid[x][y]
        self.grid_semaphore.release()

    def get_live_cells(self) -> List[List[int]]:
        live_cells = []
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                if self.grid[i][j] == 1:
                    live_cells.append([i, j])
        return live_cells



grid_manager = GridManager('grid.db')

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

def update_grid_thread():
    grid_manager.update_grid()

def send_grid_state_thread():
    while True:
        live_cells = grid_manager.get_live_cells()
        grid_state = json.dumps(live_cells)
        asyncio.run(manager.broadcast(grid_state))
        time.sleep(1)

threading.Thread(target=update_grid_thread, daemon=True).start()
threading.Thread(target=send_grid_state_thread, daemon=True).start()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            print(message)
            x, y = message['x'], message['y']
            grid_manager.flip_cell(x, y)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
