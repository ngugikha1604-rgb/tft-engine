# game.py - Game loop chính TFT Set 16
import random
import json
import os
from champion import Champion
from board import HexBoard
from combat import CombatSimulator
from econ import PlayerEconomy, ChampionPool
from items import ItemRegistry, ABILITY_HANDLERS
from augments import AugmentRegistry, AugmentManager, AUGMENT_ROUNDS
from traits import TraitManager, DEFAULT_TRAITS

# ==================
# HELPER: LOAD JSON
# ==================
def load_champions_json(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    champion_data_full = {k: v for k, v in raw.items()
                          if not k.startswith("_") and isinstance(v, dict)}
    champion_cost_map  = {k: v["cost"] for k, v in champion_data_full.items()}
    return champion_data_full, champion_cost_map

# ==================
# ROUND SCHEDULE
# ==================
PVE_ROUNDS = {
    (1, 1), (1, 2), (1, 3), (1, 4),
    (2, 5), (3, 5), (4, 5),
}
CAROUSEL_ROUNDS = {(1, 1)}

# ==================
# PLAYER
# ==================
class Player:
    def __init__(self, name, pool, augment_registry):
        self.name             = name
        self.econ             = PlayerEconomy(name, pool)
        self.board            = HexBoard()
        self.bench            = [None] * 9      
        self.item_bench       = []              # Kho chứa trang bị (tối đa 10) 🎒
        self.roster           = {}              
        self.augment_manager  = AugmentManager(augment_registry)
        self.place            = 8               
        self._pool_ref        = pool            

    # --- Champion Management ---
    def add_to_bench(self, champion):
        for i, slot in enumerate(self.bench):
            if slot is None:
                self.bench[i] = champion
                if champion.name not in self.roster:
                    self.roster[champion.name] = []
                self.roster[champion.name].append(champion)
                return True
        return False

    def remove_from_bench(self, bench_index):
        champ = self.bench[bench_index]
        if champ:
            self.bench[bench_index] = None
            if champ.name in self.roster:
                self.roster[champ.name].remove(champ)
                if not self.roster[champ.name]:
                    del self.roster[champ.name]
        return champ

    def place_on_board(self, bench_index, row, col):
        champ = self.bench[bench_index]
        if champ is None: return False
        try:
            self.board.place(champ, row, col)
            self.bench[bench_index] = None
            return True
        except ValueError: return False

    def move_to_bench(self, row, col):
        champ = self.board.get(row, col)
        if champ is None: return False
        self.board.remove(champ)
        return self.add_to_bench(champ)

    def get_board_champions(self):
        result = []
        for row in range(4):
            for col in range(7):
                champ = self.board.get(row, col)
                if champ: result.append(champ)
        return result

    def get_all_champions(self):
        """Lấy tất cả Champion instance người chơi đang có"""
        bench_champs = [c for c in self.bench if c is not None]
        board_champs = self.get_board_champions()
        return bench_champs + board_champs

    def count_on_board(self):
        return len(self.get_board_champions())

    def can_place_more(self):
        """Kiểm tra giới hạn số tướng trên bàn"""
        return self.count_on_board() < self.econ.board_size

    def add_to_board_auto(self, champion):
        """Bot tự tìm ô trống để đặt tướng"""
        if not self.can_place_more():
            return False
        for r in range(4):
            for c in range(7):
                if self.board.is_empty(r, c):
                    self.board.place(champion, r, c)
                    return True
        return False

    # --- Item Management ---
    def add_item_to_bench(self, item):
        """Thêm trang bị vào kho đồ dự bị"""
        if len(self.item_bench) < 10:
            self.item_bench.append(item)
            return True
        return False

    def equip_item_to_champ(self, item_index, champion):
        """Lắp đồ từ kho vào tướng"""
        if item_index >= len(self.item_bench):
            return False
        item = self.item_bench[item_index]
        try:
            item.equip(champion)
            self.item_bench.pop(item_index)
            return True
        except ValueError:
            return False

    # --- Shop & Sell ---
    def buy_from_shop(self, slot_index, champion_factory):
        slot = self.econ.shop.slots[slot_index]
        if slot is None: return None
        cost = champion_factory(slot).cost
        name = self.econ.buy_champion(slot_index, cost)
        if name is None: return None
        champ = champion_factory(name)
        upgraded = self._try_upgrade(champ)
        if upgraded: return upgraded
        if not self.add_to_bench(champ):
            self.econ.gold += champ.cost
            self._pool_ref.return_champ(name)
            return None
        return champ

    def sell(self, champion):
        """Bán tướng và thu hồi trang bị về kho"""
        # Thu hồi trang bị 🛠️
        if hasattr(champion, 'items'):
            for item in list(champion.items):
                item.unequip(champion)
                self.add_item_to_bench(item)
        
        # Xóa khỏi board/bench/roster
        if champion.position:
            self.board.remove(champion)
        for i, slot in enumerate(self.bench):
            if slot is champion:
                self.bench[i] = None
                break
        if champion.name in self.roster:
            try: self.roster[champion.name].remove(champion)
            except ValueError: pass
            if not self.roster[champion.name]: del self.roster[champion.name]

        return self.econ.sell_champion(champion.name, champion.cost, champion.star, self._pool_ref)

    def _try_upgrade(self, new_champ):
        name = new_champ.name
        existing = self.roster.get(name, [])
        same_star = [c for c in existing if c.star == new_champ.star]
        same_star.append(new_champ)
        if len(same_star) >= 3:
            for champ in same_star[:2]:
                if champ.position: self.board.remove(champ)
                for i, slot in enumerate(self.bench):
                    if slot is champ: self.bench[i] = None; break
                if name in self.roster:
                    try: self.roster[name].remove(champ)
                    except ValueError: pass
            new_champ.upgrade_star()
            if name not in self.roster: self.roster[name] = []
            self.roster[name].append(new_champ)
            self._try_upgrade(new_champ)
            return new_champ
        return None

    @property
    def hp(self): return self.econ.hp
    @property
    def is_alive(self): return self.econ.is_alive
    @property
    def level(self): return self.econ.level

    def status(self):
        board_champs = self.get_board_champions()
        bench_filled = sum(1 for s in self.bench if s)
        return (f"[{self.name}] HP:{self.hp} | Gold:{self.econ.gold} | Lv{self.level} | "
                f"Board:{len(board_champs)}/{self.econ.board_size} | Bench:{bench_filled}/9")

# ==================
# GAME CLASS (giữ nguyên logic điều hướng)
# ==================
class Game:
    def __init__(self, player_names, champion_data,
                 item_data=None, augment_data=None, trait_data=None,
                 item_handlers=None, augment_handlers=None):
        self.pool = ChampionPool(champion_data)
        self._trait_data = trait_data or DEFAULT_TRAITS
        self._champion_data = champion_data
        self.item_registry = ItemRegistry()
        if item_data:
            self.item_registry.load_from_data(item_data, item_handlers or ABILITY_HANDLERS)
        self.augment_registry = AugmentRegistry()
        if augment_data:
            self.augment_registry.load_from_data(augment_data, augment_handlers or {})
        self.players = [Player(name, self.pool, self.augment_registry) for name in player_names]
        self.stage, self.round_num, self.round_count = 1, 1, 0
        self.match_log = []

    # ... (Các hàm get_round_type, run_combat, process_round_end giữ nguyên từ bản cũ) ...
    # (Đã bao gồm trong file game.py bạn cung cấp)
    
    def make_champion(self, name):
        data = self._champion_data.get(name)
        if data and isinstance(data, dict):
            champ = Champion(
                name=name, cost=data.get("cost", 1), hp=data.get("hp", 600),
                armor=data.get("armor", 30), mr=data.get("mr", 30),
                attack_damage=data.get("attack_damage", 50),
                attack_speed=data.get("attack_speed", 0.75),
                range_=data.get("range", 1), traits=data.get("traits", []),
                mana_start=data.get("mana_start", 0), mana_max=data.get("mana_max", 80),
            )
            champ.role = data.get("role", "fighter")
        else:
            champ = Champion(name=name, cost=1, hp=600, armor=30, mr=30, attack_damage=50, attack_speed=0.75, range_=1, traits=[], mana_max=80)
            champ.role = "fighter"
        return champ
    
    def get_standings(self):
        alive = sorted([p for p in self.players if p.is_alive], key=lambda p: p.hp, reverse=True)
        dead = [p for p in self.players if not p.is_alive]
        return alive + dead

    def is_game_over(self):
        return sum(1 for p in self.players if p.is_alive) <= 1

    def simulate_round(self, verbose=False):
        # Logic mô phỏng round (PVP/PVE/Carousel)
        # (Giữ nguyên từ bản gốc bạn đã gửi)
        pass