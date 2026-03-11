# board.py - Hex board 4x7 của TFT

import math

# Kích thước bàn
ROWS = 4       # Mỗi bên 4 hàng
COLS = 7       # 7 cột


# ==================
# TILE EFFECT
# ==================

class TileEffect:
    """
    Ô đặc biệt trên board — buff hoặc nerf champion đứng trên đó.
    Data load từ JSON, không hardcode loại tile ở đây.

    stat_bonuses dạng dict, value dương = buff, âm = nerf:
      {"ad_pct": 0.20}           → +20% AD
      {"armor": -15}             → -15 armor (nerf)
      {"ability_power": 20}      → +20 AP
      {"damage_amp": 0.10}       → +10% damage amp
      {"damage_reduction": 0.10} → +10% DR
      {"attack_speed": 0.15}     → +15% AS
      {"max_hp_pct": 0.10}       → +10% max HP
    """

    def __init__(self, tile_type, stat_bonuses, name=None):
        self.tile_type = tile_type          # String tự do, load từ JSON
        self.stat_bonuses = stat_bonuses
        self.name = name or tile_type

    def apply(self, champion):
        """Áp buff lên champion đứng trên ô này"""
        for stat, value in self.stat_bonuses.items():
            if stat == "ad_pct":
                champion.ad = int(champion.ad * (1 + value))
            elif stat == "armor":
                champion.armor += value
            elif stat == "mr":
                champion.mr += value
            elif stat == "ability_power":
                champion.ability_power += value
            elif stat == "damage_amp":
                champion.damage_amp += value
            elif stat == "damage_reduction":
                champion.damage_reduction += value
            elif stat == "attack_speed":
                champion.attack_speed *= (1 + value)
            elif stat == "max_hp_pct":
                bonus = int(champion.max_hp * value)
                champion.max_hp += bonus
                champion.hp += bonus

    def remove(self, champion):
        """Gỡ buff khi champion rời ô (dùng khi champion di chuyển)"""
        for stat, value in self.stat_bonuses.items():
            if stat == "ad_pct":
                champion.ad = int(champion.ad / (1 + value))
            elif stat == "armor":
                champion.armor -= value
            elif stat == "mr":
                champion.mr -= value
            elif stat == "ability_power":
                champion.ability_power -= value
            elif stat == "damage_amp":
                champion.damage_amp -= value
            elif stat == "damage_reduction":
                champion.damage_reduction -= value
            elif stat == "attack_speed":
                champion.attack_speed /= (1 + value)
            elif stat == "max_hp_pct":
                bonus = int(champion.max_hp / (1 + value) * value)
                champion.max_hp -= bonus
                champion.hp = min(champion.hp, champion.max_hp)

    def __repr__(self):
        return f"TileEffect({self.name}: {self.stat_bonuses})"

class HexBoard:
    """
    TFT dùng offset hex grid:
    - Hàng 0-3: phía player
    - Hàng 4-7: phía đối thủ (mirror lại khi combat)
    
    Tọa độ: (row, col) với row 0 = hàng gần nhất phía mình
    
    Hex offset (odd-r):
    Hàng lẻ bị dịch sang phải 0.5 ô so với hàng chẵn
    """

    def __init__(self):
        # Board lưu dạng dict: (row, col) -> Champion hoặc None
        self.cells = {}
        # Tile effects: (row, col) -> TileEffect hoặc None
        self.tile_effects = {}
        self._init_board()

    def _init_board(self):
        """Khởi tạo toàn bộ ô trống cho cả 2 bên (8 hàng x 7 cột)"""
        for row in range(ROWS * 2):
            for col in range(COLS):
                self.cells[(row, col)] = None

    # ==================
    # ĐẶT / DI CHUYỂN CHAMPION
    # ==================

    def place(self, champion, row, col):
        """Đặt champion lên ô (row, col), tự apply tile effect nếu có"""
        if not self.is_valid(row, col):
            raise ValueError(f"Invalid position ({row}, {col})")
        if self.cells[(row, col)] is not None:
            raise ValueError(f"Cell ({row}, {col}) already occupied by {self.cells[(row, col)].name}")

        # Gỡ tile effect ở vị trí cũ
        if champion.position is not None:
            old_tile = self.tile_effects.get(champion.position)
            if old_tile:
                old_tile.remove(champion)
            self.cells[champion.position] = None

        self.cells[(row, col)] = champion
        champion.position = (row, col)

        # Apply tile effect ở vị trí mới
        new_tile = self.tile_effects.get((row, col))
        if new_tile:
            new_tile.apply(champion)

    def remove(self, champion):
        """Xóa champion khỏi board, gỡ tile effect"""
        if champion.position is not None:
            tile = self.tile_effects.get(champion.position)
            if tile:
                tile.remove(champion)
            self.cells[champion.position] = None
            champion.position = None

    def move(self, champion, new_row, new_col):
        """Di chuyển champion, swap tile effects tương ứng"""
        if not self.is_valid(new_row, new_col):
            raise ValueError(f"Invalid position ({new_row}, {new_col})")
        if self.cells[(new_row, new_col)] is not None:
            raise ValueError(f"Cell ({new_row}, {new_col}) is occupied")

        # Gỡ tile effect cũ
        if champion.position:
            old_tile = self.tile_effects.get(champion.position)
            if old_tile:
                old_tile.remove(champion)
            self.cells[champion.position] = None

        self.cells[(new_row, new_col)] = champion
        champion.position = (new_row, new_col)

        # Apply tile effect mới
        new_tile = self.tile_effects.get((new_row, new_col))
        if new_tile:
            new_tile.apply(champion)

    # ==================
    # KIỂM TRA Ô
    # ==================

    def is_valid(self, row, col):
        """Kiểm tra ô có nằm trong board không"""
        return 0 <= row < ROWS * 2 and 0 <= col < COLS

    def is_empty(self, row, col):
        return self.is_valid(row, col) and self.cells[(row, col)] is None

    def get(self, row, col):
        """Lấy champion tại ô, trả về None nếu trống"""
        return self.cells.get((row, col))

    # ==================
    # TÍNH KHOẢNG CÁCH HEX
    # ==================

    def _to_cube(self, row, col):
        """
        Chuyển offset coordinates sang cube coordinates để tính khoảng cách
        TFT dùng odd-r offset: hàng lẻ dịch phải
        """
        x = col - (row - (row & 1)) // 2
        z = row
        y = -x - z
        return x, y, z

    def hex_distance(self, pos1, pos2):
        """Khoảng cách giữa 2 ô tính bằng số bước hex"""
        r1, c1 = pos1
        r2, c2 = pos2
        x1, y1, z1 = self._to_cube(r1, c1)
        x2, y2, z2 = self._to_cube(r2, c2)
        return max(abs(x1 - x2), abs(y1 - y2), abs(z1 - z2))

    def get_neighbors(self, row, col):
        """Lấy các ô kề (6 hướng trong hex grid)"""
        # Offset directions cho odd-r hex grid
        if row % 2 == 0:  # Hàng chẵn
            directions = [(-1,-1),(-1,0),(0,-1),(0,1),(1,-1),(1,0)]
        else:             # Hàng lẻ
            directions = [(-1,0),(-1,1),(0,-1),(0,1),(1,0),(1,1)]

        neighbors = []
        for dr, dc in directions:
            nr, nc = row + dr, col + dc
            if self.is_valid(nr, nc):
                neighbors.append((nr, nc))
        return neighbors

    # ==================
    # TÌM TARGET
    # ==================

    def get_enemies_in_range(self, champion, enemy_team):
        """
        Lấy danh sách kẻ địch trong tầm tấn công của champion
        enemy_team: list các champion địch còn sống
        """
        if champion.position is None:
            return []

        in_range = []
        for enemy in enemy_team:
            if enemy.is_alive and enemy.position is not None:
                dist = self.hex_distance(champion.position, enemy.position)
                if dist <= champion.range:
                    in_range.append(enemy)
        return in_range

    def find_nearest_enemy(self, champion, enemy_team):
        """
        Tìm kẻ địch gần nhất còn sống
        Ưu tiên: gần nhất → HP thấp nhất (nếu cùng khoảng cách)
        """
        if champion.position is None:
            return None

        alive_enemies = [e for e in enemy_team if e.is_alive and e.position is not None]
        if not alive_enemies:
            return None

        return min(
            alive_enemies,
            key=lambda e: (
                self.hex_distance(champion.position, e.position),
                e.hp
            )
        )

    def find_move_toward(self, champion, target):
        """
        Tìm ô trống tốt nhất để di chuyển về phía target
        Trả về (row, col) hoặc None nếu không thể di chuyển
        """
        if champion.position is None or target.position is None:
            return None

        neighbors = self.get_neighbors(*champion.position)
        empty_neighbors = [(r, c) for r, c in neighbors if self.is_empty(r, c)]

        if not empty_neighbors:
            return None

        # Chọn ô gần target nhất
        return min(empty_neighbors,
                   key=lambda pos: self.hex_distance(pos, target.position))

    # ==================
    # SETUP COMBAT
    # ==================

    def setup_enemy_team(self, enemy_champions, mirror=True):
        """
        Đặt đội địch lên board (hàng 4-7)
        mirror=True: mirror lại vị trí đội mình
        """
        for champ in enemy_champions:
            if champ.position is None:
                continue
            orig_row, orig_col = champ.position
            if mirror:
                # Mirror: hàng 0→7, 1→6, 2→5, 3→4
                new_row = (ROWS * 2 - 1) - orig_row
                new_col = (COLS - 1) - orig_col
            else:
                new_row = orig_row + ROWS
                new_col = orig_col
            self.cells[(new_row, new_col)] = champ
            champ.position = (new_row, new_col)

    def get_all_champions(self):
        """Lấy tất cả champion trên board"""
        return [c for c in self.cells.values() if c is not None]

    def get_team(self, max_row):
        """Lấy champion của 1 team theo vùng row"""
        return [c for (r, c_), champ in self.cells.items()
                if champ is not None and r < max_row]

    # ==================
    # TILE EFFECTS
    # ==================

    def set_tile(self, row, col, tile_effect):
        """Đặt tile effect lên ô — apply ngay nếu có champion đứng đó"""
        if not self.is_valid(row, col):
            raise ValueError(f"Invalid position ({row}, {col})")
        self.tile_effects[(row, col)] = tile_effect
        champ = self.cells.get((row, col))
        if champ:
            tile_effect.apply(champ)

    def remove_tile(self, row, col):
        """Xóa tile effect — gỡ buff nếu có champion đứng đó"""
        tile = self.tile_effects.pop((row, col), None)
        if tile:
            champ = self.cells.get((row, col))
            if champ:
                tile.remove(champ)

    def clear_tiles(self):
        """Xóa tất cả tile effects (dùng khi bắt đầu trận mới)"""
        for (row, col), tile in list(self.tile_effects.items()):
            champ = self.cells.get((row, col))
            if champ:
                tile.remove(champ)
        self.tile_effects.clear()

    def get_tile(self, row, col):
        """Lấy tile effect tại ô, None nếu không có"""
        return self.tile_effects.get((row, col))

    def reapply_all_tiles(self):
        """
        Reapply tất cả tile effects lên champion đang đứng trên đó.
        Dùng khi reset combat (sau khi champion.reset_for_combat())
        """
        for (row, col), tile in self.tile_effects.items():
            champ = self.cells.get((row, col))
            if champ and champ.is_alive:
                tile.apply(champ)

    # ==================
    # DEBUG VISUALIZE
    # ==================

    def display(self):
        """In board ra terminal để debug — hiện cả tile effects"""
        print("=" * 55)
        for row in range(ROWS * 2):
            indent = " " if row % 2 == 1 else ""
            line = indent
            for col in range(COLS):
                champ = self.cells.get((row, col))
                tile = self.tile_effects.get((row, col))
                if champ:
                    line += f"[{champ.name[:4]:4}]"
                elif tile:
                    line += f"[{tile.tile_type[:4]:4}]"  # Hiện loại tile
                else:
                    line += "[    ]"
            print(f"R{row}: {line}")
        print("=" * 55)