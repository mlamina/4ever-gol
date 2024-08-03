import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import threading
import time
import json
import asyncio
from typing import List
import random


logging.basicConfig(level=logging.INFO)

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
    def __init__(self, db_path: str, grid_size: int = 100):
        self.grid_size = grid_size
        self.grid_semaphore = threading.Semaphore()
        self.grid = [[0 for _ in range(grid_size)] for _ in range(grid_size)]
        self.colors = [[None for _ in range(grid_size)] for _ in range(grid_size)]
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._initialize_db()
        self._load_grid_state()

    def _initialize_db(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS grid (x INTEGER, y INTEGER, state INTEGER, color TEXT)''')
        self.conn.commit()

    def _load_grid_state(self):
        self.cursor.execute('SELECT * FROM grid')
        rows = self.cursor.fetchall()
        for row in rows:
            x, y, state, color = row
            self.grid[x][y] = state
            self.colors[x][y] = color

    def update_grid(self):
        while True:
            self.grid_semaphore.acquire()
            new_grid = [[0 for _ in range(self.grid_size)] for _ in range(self.grid_size)]
            new_colors = [[None for _ in range(self.grid_size)] for _ in range(self.grid_size)]
            for i in range(self.grid_size):
                for j in range(self.grid_size):
                    live_neighbors = 0
                    neighbor_colors = []
                    for dx in [-1, 0, 1]:
                        for dy in [-1, 0, 1]:
                            if dx == 0 and dy == 0:
                                continue
                            ni, nj = i + dx, j + dy
                            if 0 <= ni < self.grid_size and 0 <= nj < self.grid_size:
                                live_neighbors += self.grid[ni][nj]
                                if self.grid[ni][nj] == 1:
                                    neighbor_colors.append(self.colors[ni][nj])
                    if self.grid[i][j] == 1 and live_neighbors in [2, 3]:
                        new_grid[i][j] = 1
                        new_colors[i][j] = self.colors[i][j]
                    elif self.grid[i][j] == 0 and live_neighbors == 3:
                        new_grid[i][j] = 1
                        new_colors[i][j] = random.choice(neighbor_colors) if neighbor_colors else None

            self.grid = new_grid
            self.colors = new_colors
            self.grid_semaphore.release()
            time.sleep(1)

    def save_cell_state(self, x: int, y: int):
        self.cursor.execute('REPLACE INTO grid (x, y, state, color) VALUES (?, ?, ?, ?)', (x, y, self.grid[x][y], self.colors[x][y]))
        self.conn.commit()

    def get_grid_state(self) -> str:
        return json.dumps(self.grid)

    def flip_cell(self, x: int, y: int, color: str = None):
        self.grid_semaphore.acquire()
        self.grid[x][y] = 1 - self.grid[x][y]
        if self.grid[x][y] == 1:
            self.colors[x][y] = color
        else:
            self.colors[x][y] = None
        self.grid_semaphore.release()

    def get_live_cells(self) -> List[List[int]]:
        live_cells = []
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                if self.grid[i][j] == 1:
                    live_cells.append([i, j, self.colors[i][j]])
        return live_cells

    def spawn(self, topLeftX, topLeftY, cells, color):
        logger.info(f"Spawning cells at ({topLeftX}, {topLeftY})")
        for (x, y) in cells:
            self.grid_semaphore.acquire()
            self.grid[topLeftX + x][topLeftY + y] = 1
            self.colors[topLeftX + x][topLeftY + y] = color
            self.grid_semaphore.release()


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
    user_color = "#%06x" % random.randint(0, 0xFFFFFF)
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if message['type'] == 'spawn':
                grid_manager.spawn(message['x'], message['y'], message['cells'], user_color)
            elif message['type'] == 'flip':
                grid_manager.flip_cell(message['x'], message['y'], user_color)
            print(message)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
