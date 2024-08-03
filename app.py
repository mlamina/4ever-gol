import logging
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import threading
import time
import json
import asyncio
from typing import List, Optional
import random
from fastapi.responses import HTMLResponse


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

class Cell:
    def __init__(self, x: int, y: int, color: Optional[str] = None):
        self.x = x
        self.y = y
        self.color = color

class GridManager:
    def __init__(self, db_path: str, grid_size: int = 100):
        self.grid_size = grid_size
        self.grid_semaphore = threading.Semaphore()
        self.grid = [[None for _ in range(grid_size)] for _ in range(grid_size)]
        self.conn = sqlite3.connect(db_path)
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
            if state == 1:
                self.grid[x][y] = Cell(x, y, color)

    def update_grid(self):
        """
        Update the grid based on the rules of Conway's Game of Life.

        This method calculates the next state of the grid and updates the cell colors based on their neighbors.
        """
        while True:
            self.grid_semaphore.acquire()
            new_grid = self._calculate_next_state()
            self.grid = new_grid
            self.grid_semaphore.release()
            time.sleep(1)

    def _calculate_next_state(self):
        """
        Calculate the next state of the grid.

        Returns:
            new_grid (List[List[Optional[Cell]]]): The new state of the grid.
        """
        new_grid = [[None for _ in range(self.grid_size)] for _ in range(self.grid_size)]

        for i in range(self.grid_size):
            for j in range(self.grid_size):
                live_neighbors, neighbor_colors = self._count_live_neighbors_and_colors(i, j)

                if self.grid[i][j] is not None and live_neighbors in [2, 3]:
                    new_grid[i][j] = Cell(i, j, self.grid[i][j].color)
                elif self.grid[i][j] is None and live_neighbors == 3:
                    new_grid[i][j] = Cell(i, j, random.choice(neighbor_colors) if neighbor_colors else None)

        return new_grid

    def _count_live_neighbors_and_colors(self, x: int, y: int):
        """
        Count the live neighbors and their colors for a given cell.

        Args:
            x (int): The x-coordinate of the cell.
            y (int): The y-coordinate of the cell.

        Returns:
            live_neighbors (int): The number of live neighbors.
            neighbor_colors (List[Optional[str]]): The colors of the live neighbors.
        """
        live_neighbors = 0
        neighbor_colors = []

        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue
                ni, nj = x + dx, y + dy
                if 0 <= ni < self.grid_size and 0 <= nj < self.grid_size:
                    if self.grid[ni][nj] is not None:
                        live_neighbors += 1
                        neighbor_colors.append(self.grid[ni][nj].color)

        return live_neighbors, neighbor_colors

    def save_cell_state(self, x: int, y: int):
        cell = self.grid[x][y]
        state = 1 if cell is not None else 0
        color = cell.color if cell is not None else None
        self.cursor.execute('REPLACE INTO grid (x, y, state, color) VALUES (?, ?, ?, ?)', (x, y, state, color))
        self.conn.commit()

    def get_grid_state(self) -> str:
        return json.dumps([[1 if cell is not None else 0 for cell in row] for row in self.grid])

    def flip_cell(self, x: int, y: int, color: str = None):
        self.grid_semaphore.acquire()
        if self.grid[x][y] is None:
            self.grid[x][y] = Cell(x, y, color)
        else:
            self.grid[x][y] = None
        self.grid_semaphore.release()

    def get_live_cells(self) -> List[List[int]]:
        live_cells = []
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                if self.grid[i][j] is not None:
                    live_cells.append([i, j, self.grid[i][j].color])
        return live_cells

    def spawn(self, topLeftX, topLeftY, cells, color):
        logger.info(f"Spawning cells at ({topLeftX}, {topLeftY})")
        for (x, y) in cells:
            self.grid_semaphore.acquire()
            self.grid[topLeftX + x][topLeftY + y] = Cell(topLeftX + x, topLeftY + y, color)
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

@app.get("/")
async def serve_index(request: Request):
    with open("index.html") as f:
        return HTMLResponse(content=f.read(), status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
