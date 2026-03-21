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
# Stage 1: 3 rounds PvE | Stage 2+: 7 rounds (X-2 augment, X-4 carousel, X-7 PvE)
PVE_ROUNDS = {(1, 1), (1, 2), (1, 3)}
for _s in range(2, 10):
    PVE_ROUNDS.add((_s, 7))

CAROUSEL_ROUNDS = set()
for _s in range(2, 10):
    CAROUSEL_ROUNDS.add((_s, 4))

# Override AUGMENT_ROUNDS từ augments.py import
AUGMENT_ROUNDS = set()
for _s in range(2, 10):
    AUGMENT_ROUNDS.add((_s, 2))

ROUNDS_PER_STAGE         = {1: 3}
ROUNDS_PER_STAGE_DEFAULT = 7

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

# GAME
# ==================

class Game:
    """
    Game loop chính — quản lý 8 players, round schedule, combat matching.
    """

    def __init__(self, player_names, champion_data,
                 item_data=None, augment_data=None, trait_data=None,
                 item_handlers=None, augment_handlers=None):
        """
        player_names   : list[str] tên 8 players
        champion_data  : dict {name: cost} cho ChampionPool
        item_data      : dict load từ items.json
        augment_data   : dict load từ augments.json
        trait_data     : dict load từ traits.json (None = dùng DEFAULT_TRAITS)
        """
        # Shared champion pool
        self.pool = ChampionPool(champion_data)
        self._trait_data = trait_data or DEFAULT_TRAITS
        self._champion_data = champion_data     # Dùng để tạo Champion instance

        # Item registry
        self.item_registry = ItemRegistry()
        if item_data:
            self.item_registry.load_from_data(item_data, item_handlers or ABILITY_HANDLERS)

        # Augment registry
        self.augment_registry = AugmentRegistry()
        if augment_data:
            self.augment_registry.load_from_data(augment_data, augment_handlers or {})

        # Players
        self.players = [
            Player(name, self.pool, self.augment_registry)
            for name in player_names
        ]

        # Round tracking
        self.stage       = 1
        self.round_num   = 1
        self.round_count = 0

        # Match history
        self.match_log   = []

        # Augment/Encounter data
        import os as _os
        _base = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'data')
        self._augment_pool   = self._load_json(_os.path.join(_base, 'augments.json'), 'augments')
        self._encounter_pool = self._load_json(_os.path.join(_base, 'encounters.json'), 'encounters')

        # Active encounter cho game này
        self._active_encounter = None

        # Carousel state
        self._carousel_slots = []   # list of (champion, item) cho round carousel hiện tại

        # Augment offered per player per round
        self._augment_offers = {}   # player_name -> list of 3 augments

    # ==================
    # HELPERS
    # ==================

    def _load_json(self, path, key):
        """Load list từ JSON file"""
        import os
        if not os.path.exists(path):
            return []
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return [e for e in data.get(key, []) if not str(e.get('id','')).startswith('_')]

    def apply_encounter(self):
        """Apply encounter ngẫu nhiên cho tất cả players — gọi 1 lần đầu game"""
        if not self._encounter_pool:
            return
        enc = random.choice(self._encounter_pool)
        self._active_encounter = enc
        effect = enc.get('effect', {})
        etype  = effect.get('type')

        for player in self.players:
            if etype == 'items' or etype == 'combined':
                count = effect.get('count', effect.get('items', 1))
                if self.item_registry:
                    items = list(self.item_registry._items.values())
                    for _ in range(count):
                        if items:
                            player.add_item_to_bench(random.choice(items))
            if etype == 'gold' or etype == 'combined':
                player.econ.gold += effect.get('amount', effect.get('gold', 0))
            if etype == 'hp':
                player.econ.hp = min(player.econ.hp + 10, effect.get('max_hp', 110))
            if etype == 'xp':
                start_level = effect.get('start_level', 3)
                if player.econ.level < start_level:
                    player.econ.level = start_level

        if self._active_encounter:
            self.match_log.append(f"[Encounter] {enc['name']}: {enc['description']}")

    def generate_carousel(self):
        """Tạo 9 slots carousel theo stage hiện tại"""
        # Xác định cost pool theo stage
        if self.stage <= 2:
            cost_pool = [1, 2, 3]
        elif self.stage == 3:
            cost_pool = [1, 2, 3, 4]
        else:
            cost_pool = [1, 2, 3, 4, 5]

        champ_names = [
            n for n, d in self._champion_data.items()
            if isinstance(d, dict) and d.get('cost', 1) in cost_pool
        ]

        # Tạo 9 slots
        slots = []
        items = list(self.item_registry._items.values()) if self.item_registry else []
        for _ in range(9):
            if champ_names:
                name  = random.choice(champ_names)
                champ = self.make_champion(name)
                item  = random.choice(items) if items else None
                slots.append({'champion': champ, 'item': item})
        self._carousel_slots = slots
        return slots

    def pick_carousel(self, player, slot_idx):
        """
        Player chọn tướng từ carousel.
        Trả về True nếu thành công.
        """
        if slot_idx >= len(self._carousel_slots):
            return False
        slot = self._carousel_slots[slot_idx]
        if slot is None:
            return False   # Đã bị chọn rồi

        champ = slot['champion']
        item  = slot['item']

        # Thêm tướng vào bench
        added = player.add_to_bench(champ)
        if not added:
            player.add_to_board_auto(champ)

        # Thêm item vào bench
        if item:
            player.add_item_to_bench(item)

        # Xóa slot đã chọn
        self._carousel_slots[slot_idx] = None
        return True

    def generate_augment_offers(self):
        """Tạo 3 augments ngẫu nhiên cho mỗi player"""
        if not self._augment_pool:
            return
        for player in self.players:
            if player.is_alive:
                offers = random.sample(
                    self._augment_pool,
                    min(3, len(self._augment_pool))
                )
                self._augment_offers[player.name] = offers

    def pick_augment(self, player, aug_idx):
        """Player chọn augment. Trả về True nếu thành công."""
        offers = self._augment_offers.get(player.name, [])
        if aug_idx >= len(offers):
            return False
        aug    = offers[aug_idx]
        effect = aug.get('effect', {})
        etype  = effect.get('type')

        # Apply effect
        if etype == 'stat':
            board_champs = player.get_board_champions()
            for champ in board_champs:
                self._apply_augment_stat(champ, effect)
        elif etype == 'economy':
            player.econ.gold    += effect.get('instant_gold', 0)
            player.econ.gold    += effect.get('gold_per_round', 0)  # TODO: per round
            count = effect.get('instant_items', 0)
            if self.item_registry and count:
                items = list(self.item_registry._items.values())
                for _ in range(count):
                    if items: player.add_item_to_bench(random.choice(items))
            if effect.get('free_level_up'):
                if player.econ.level < 10:
                    player.econ.level += 1
        elif etype == 'board':
            player.econ.board_size_bonus = getattr(player.econ, 'board_size_bonus', 0)
            player.econ.board_size_bonus += effect.get('extra_board_size', 0)

        self.match_log.append(f"[Augment] {player.name} chọn: {aug['name']}")
        self._augment_offers[player.name] = []
        return True

    def _apply_augment_stat(self, champ, effect):
        """Apply stat buff từ augment lên champion"""
        cond = effect.get('condition', 'all')
        cost = getattr(champ, 'cost', 1)
        star = getattr(champ, 'star', 1)

        if cond == 'has_item' and not champ.items:
            return
        if cond == 'cost_lte_2' and cost > 2:
            return
        if cond == 'star_gte_2' and star < 2:
            return

        if 'hp_flat' in effect:
            champ.max_hp += effect['hp_flat']
            champ.hp     += effect['hp_flat']
        if 'ad_pct' in effect:
            champ.ad = int(champ.ad * (1 + effect['ad_pct']))
        if 'as_pct' in effect:
            champ.attack_speed *= (1 + effect['as_pct'])
        if 'hp_pct' in effect:
            bonus = int(champ.max_hp * effect['hp_pct'])
            champ.max_hp += bonus
            champ.hp     += bonus
        if 'stat' in effect and 'value' in effect:
            stat = effect['stat']
            if hasattr(champ, stat):
                setattr(champ, stat, getattr(champ, stat) + effect['value'])

    # ==================
    # ROUND TYPE
    # ==================

    def get_round_type(self):
        key = (self.stage, self.round_num)
        if key in CAROUSEL_ROUNDS:
            return "carousel"
        if key in PVE_ROUNDS:
            return "pve"
        if key in AUGMENT_ROUNDS:
            return "augment"
        return "pvp"

    def is_pvp(self):
        return self.get_round_type() == "pvp"

    def is_augment_round(self):
        return (self.stage, self.round_num) in AUGMENT_ROUNDS

    # ==================
    # MATCHMAKING
    # ==================

    def make_pvp_pairs(self):
        """
        Ghép cặp PvP ngẫu nhiên.
        Nếu số player lẻ → 1 player đánh lại ghost (trận cũ nhất).
        Trả về list[(player_a, player_b)].
        """
        alive = [p for p in self.players if p.is_alive]
        random.shuffle(alive)
        pairs = []
        for i in range(0, len(alive) - 1, 2):
            pairs.append((alive[i], alive[i + 1]))
        if len(alive) % 2 == 1:
            # Player lẻ đánh ghost — placeholder
            pairs.append((alive[-1], None))
        return pairs

    # ==================
    # COMBAT
    # ==================

    def run_combat(self, player_a, player_b):
        """
        Chạy 1 trận PvP giữa player_a và player_b.
        Trả về dict kết quả.
        """
        team_a = player_a.get_board_champions()
        team_b = player_b.get_board_champions() if player_b else []

        if not team_a:
            return {"winner": "team_b", "survivors_a": [], "survivors_b": [],
                    "duration": 0, "events": []}
        if not team_b:
            return {"winner": "team_a", "survivors_a": team_a, "survivors_b": [],
                    "duration": 0, "events": []}

        # Reset và reapply augment team stats trước combat
        for champ in team_a:
            champ.reset_for_combat()
        for champ in team_b:
            champ.reset_for_combat()

        player_a.augment_manager.apply_team_stats(team_a)
        if player_b:
            player_b.augment_manager.apply_team_stats(team_b)

        # Apply trait bonuses
        trait_mgr_a = TraitManager(self._trait_data)
        trait_mgr_b = TraitManager(self._trait_data)
        trait_mgr_a.apply(team_a)
        if team_b:
            trait_mgr_b.apply(team_b)



        # Dùng board tạm thời để chạy combat
        from board import HexBoard
        combat_board = HexBoard()

        # Đặt team_a lên hàng 0-3, mirror vị trí gốc
        for champ in team_a:
            if champ.position:
                r, c = champ.position
                try:
                    combat_board.place(champ, r, c)
                except ValueError:
                    pass  # Ô đã có người, bỏ qua

        # Đặt team_b lên hàng 4-7 (mirror)
        for champ in team_b:
            if champ.position:
                r, c = champ.position
                mirror_r = 7 - r        # hàng 0→7, 1→6, 2→5, 3→4
                mirror_c = 6 - c
                try:
                    combat_board.place(champ, mirror_r, mirror_c)
                except ValueError:
                    # Tìm ô trống gần đó
                    for rr in range(4, 8):
                        placed = False
                        for cc in range(7):
                            if combat_board.is_empty(rr, cc):
                                combat_board.place(champ, rr, cc)
                                placed = True
                                break
                        if placed:
                            break

        sim = CombatSimulator(combat_board, team_a, team_b)
        result = sim.run(max_seconds=30)

        # Remove trait bonuses sau combat
        trait_mgr_a.remove(team_a)
        if team_b:
            trait_mgr_b.remove(team_b)

        # Restore position của champions về board gốc của mỗi player
        # (combat_board là board tạm, sau combat xóa đi)
        for champ in team_a + team_b:
            # Tìm lại vị trí trên board gốc
            found = False
            src_board = player_a.board if champ in team_a else (player_b.board if player_b else None)
            if src_board:
                for (r, c), occupant in src_board.cells.items():
                    if occupant is champ:
                        champ.position = (r, c)
                        found = True
                        break
            if not found:
                champ.position = None

        # Trigger augment combat end events
        player_a.augment_manager.trigger("on_combat_end", player_a, {
            "result": result, "won": result["winner"] == "team_a"
        })
        if player_b:
            player_b.augment_manager.trigger("on_combat_end", player_b, {
                "result": result, "won": result["winner"] == "team_b"
            })

        return result

    # ==================
    # END OF ROUND
    # ==================

    def process_round_end(self, combat_results):
        """
        Xử lý cuối round cho tất cả players:
        1. Apply player damage cho người thua
        2. Update streak + collect income
        3. Augment per-round bonuses
        4. Advance round counter
        """
        for player_a, player_b, result in combat_results:
            won_a = result["winner"] == "team_a"
            won_b = result["winner"] == "team_b"

            # Player damage
            if not won_a and player_b:
                survivors = len(result["survivors_b"])
                dmg = player_a.econ.take_player_damage(self.stage, survivors)
                self.match_log.append(
                    f"[S{self.stage}R{self.round_num}] {player_a.name} takes {dmg} dmg"
                )

            if player_b and not won_b:
                survivors = len(result["survivors_a"])
                dmg = player_b.econ.take_player_damage(self.stage, survivors)
                self.match_log.append(
                    f"[S{self.stage}R{self.round_num}] {player_b.name} takes {dmg} dmg"
                )

        # Income + XP + augment bonuses cho tất cả
        for player in self.players:
            if not player.is_alive:
                continue

            # Tìm result của player này
            won = None
            for pa, pb, res in combat_results:
                if pa is player:
                    won = res["winner"] == "team_a"
                    break
                if pb is player:
                    won = res["winner"] == "team_b"
                    break

            # Per-round augment bonuses
            player.augment_manager.collect_round_bonuses(player)

            # Econ end of round
            earned = player.econ.end_of_round(
                won_pvp=won if self.is_pvp() else None,
                is_pvp=self.is_pvp()
            )

        # Advance round
        self._advance_round()

    def _advance_round(self):
        """Tăng round counter, set stage mới nếu cần"""
        self.round_count += 1

        max_round = ROUNDS_PER_STAGE.get(self.stage, ROUNDS_PER_STAGE_DEFAULT)
        if self.round_num >= max_round:
            self.stage    += 1
            self.round_num = 1
        else:
            self.round_num += 1

        for player in self.players:
            player.econ.current_stage = self.stage
            player.econ.current_round = self.round_num

    # ==================
    # CHAMPION FACTORY
    # ==================

    def _make_creep(self, stage, round_num):
        """Tạo danh sách creep cho PvE round"""
        data   = PVE_CREEP_DATA.get((stage, round_num), {})
        if not data:
            return []
        creeps = []
        for i in range(data["count"]):
            creep = Champion(
                name         = f"Creep_{stage}_{round_num}_{i}",
                cost         = 0,
                hp           = data["hp"],
                armor        = data["armor"],
                mr           = data["mr"],
                attack_damage= data["ad"],
                attack_speed = 0.75,
                range_       = 1,
                traits       = [],
                mana_max     = 999,   # Creep không cast skill
            )
            creep.role = "fighter"
            creeps.append(creep)
        return creeps

    def _run_pve_combat(self, player, stage, round_num):
        """
        Chạy combat PvE giữa player và creep.
        Trả về dict kết quả tương tự run_combat().
        """
        team_player = player.get_board_champions()
        team_creep  = self._make_creep(stage, round_num)

        if not team_player:
            return {
                "winner": "creep", "survivors_player": [],
                "survivors_creep": team_creep, "duration": 0
            }
        if not team_creep:
            return {
                "winner": "player", "survivors_player": team_player,
                "survivors_creep": [], "duration": 0
            }

        # Reset champions
        for champ in team_player:
            champ.reset_for_combat()

        # Setup combat board
        from board import HexBoard
        combat_board = HexBoard()

        # Đặt player team hàng 0-3
        for i, champ in enumerate(team_player):
            row, col = champ.position if champ.position else (0, i % 7)
            try:
                combat_board.place(champ, row, col)
            except ValueError:
                for r in range(4):
                    for c in range(7):
                        if combat_board.is_empty(r, c):
                            combat_board.place(champ, r, c)
                            break
                    else:
                        continue
                    break

        # Đặt creep hàng 4-7
        for i, creep in enumerate(team_creep):
            row, col = 4 + (i // 7), i % 7
            try:
                combat_board.place(creep, row, col)
            except ValueError:
                pass

        # Apply traits cho player
        trait_mgr = TraitManager(self._trait_data)
        trait_mgr.apply(team_player)

        sim    = CombatSimulator(combat_board, team_player, team_creep)
        result = sim.run(max_seconds=30)

        trait_mgr.remove(team_player)

        # Restore positions
        for champ in team_player:
            for (r, c), occ in player.board.cells.items():
                if occ is champ:
                    champ.position = (r, c)
                    break

        winner = "player" if result["winner"] == "team_a" else "creep"
        return {
            "winner"           : winner,
            "survivors_player" : result["survivors_a"],
            "survivors_creep"  : result["survivors_b"],
            "duration"         : result["duration"],
        }

    def make_champion(self, name):
        """
        Tạo Champion instance từ champion_data (đã load từ champions.json).
        Fallback về stats placeholder nếu tên không tìm thấy.
        """
        data = self._champion_data.get(name)

        if data and isinstance(data, dict):
            # Load từ champions.json đầy đủ
            champ = Champion(
                name         = name,
                cost         = data.get("cost", 1),
                hp           = data.get("hp", 600),
                armor        = data.get("armor", 30),
                mr           = data.get("mr", 30),
                attack_damage= data.get("attack_damage", 50),
                attack_speed = data.get("attack_speed", 0.75),
                range_       = data.get("range", 1),
                traits       = data.get("traits", []),
                mana_start   = data.get("mana_start", 0),
                mana_max     = data.get("mana_max", 80),
            )
            champ.role = data.get("role", "fighter")
        else:
            # Fallback: chỉ biết cost (dùng cho econ/shop)
            cost = data if isinstance(data, int) else 1
            champ = Champion(
                name=name, cost=cost,
                hp=600 + cost * 100, armor=30, mr=30,
                attack_damage=50 + cost * 10,
                attack_speed=0.75, range_=1,
                traits=[], mana_max=80
            )
            champ.role = "fighter"

        return champ

    # ==================
    # LEADERBOARD
    # ==================

    def get_standings(self):
        """Trả về danh sách players sắp xếp theo HP (cao nhất trước)"""
        alive = sorted(
            [p for p in self.players if p.is_alive],
            key=lambda p: p.hp, reverse=True
        )
        dead = [p for p in self.players if not p.is_alive]
        return alive + dead

    def is_game_over(self):
        """Game kết thúc khi còn ≤ 1 player sống"""
        return sum(1 for p in self.players if p.is_alive) <= 1

    def get_winner(self):
        alive = [p for p in self.players if p.is_alive]
        return alive[0] if len(alive) == 1 else None

    # ==================
    # QUICK SIMULATE
    # ==================

    def simulate_round(self, verbose=False):
        """
        Simulate 1 round đầy đủ (dùng để test).
        Trả về list kết quả combat.
        """
        round_type = self.get_round_type()
        combat_results = []

        if verbose:
            print(f"\n=== Stage {self.stage}-{self.round_num} "
                  f"[{round_type.upper()}] ===")

        if round_type in ("pvp", "augment_pvp"):
            pairs = self.make_pvp_pairs()
            for player_a, player_b in pairs:
                if player_b is None:
                    combat_results.append((player_a, None, {
                        "winner": "team_a", "survivors_a": [], "survivors_b": [],
                        "duration": 0, "events": []
                    }))
                    continue

                result = self.run_combat(player_a, player_b)
                combat_results.append((player_a, player_b, result))

                if verbose:
                    winner_name = (player_a.name if result["winner"] == "team_a"
                                   else player_b.name)
                    print(f"  {player_a.name} vs {player_b.name} "
                          f"→ {winner_name} wins ({result['duration']}s)")

        elif round_type == "pve":
            for player in self.players:
                if not player.is_alive:
                    continue

                # Carousel round (1-1): tất cả thắng, nhận item miễn phí
                if (self.stage, self.round_num) == (1, 1):
                    if self.item_registry and random.random() < 1.0:
                        items = list(self.item_registry._items.values())
                        if items:
                            item = random.choice(items)
                            player.add_item_to_bench(item)
                    combat_results.append((player, None, {
                        "winner": "team_a", "survivors_a": [], "survivors_b": [],
                        "duration": 0, "events": [], "pve": True, "pve_won": True
                    }))
                    continue

                # Bots luôn thắng PvE — nhận thưởng ngay
                if player.name != "Agent":
                    data = PVE_CREEP_DATA.get((self.stage, self.round_num), {})
                    player.econ.gold += data.get("gold_reward", 1)
                    if self.item_registry and random.random() < PVE_ITEM_DROP_CHANCE:
                        items = list(self.item_registry._items.values())
                        if items:
                            player.add_item_to_bench(random.choice(items))
                    combat_results.append((player, None, {
                        "winner": "team_a", "survivors_a": [], "survivors_b": [],
                        "duration": 0, "events": [], "pve": True, "pve_won": True
                    }))
                    continue

                # Agent phải đánh PvE thật
                pve_result = self._run_pve_combat(player, self.stage, self.round_num)
                pve_won    = pve_result["winner"] == "player"
                data       = PVE_CREEP_DATA.get((self.stage, self.round_num), {})

                if pve_won:
                    # Thắng: nhận gold + item
                    player.econ.gold += data.get("gold_reward", 1)
                    if self.item_registry and random.random() < PVE_ITEM_DROP_CHANCE:
                        items = list(self.item_registry._items.values())
                        if items:
                            player.add_item_to_bench(random.choice(items))
                else:
                    # Thua: mất HP theo số creep còn sống
                    survivors = len(pve_result["survivors_creep"])
                    dmg = self.stage + survivors
                    player.econ.hp = max(0, player.econ.hp - dmg)

                combat_results.append((player, None, {
                    "winner"    : "team_a" if pve_won else "team_b",
                    "survivors_a": pve_result["survivors_player"],
                    "survivors_b": [],
                    "duration"  : pve_result["duration"],
                    "events"    : [],
                    "pve"       : True,
                    "pve_won"   : pve_won,
                }))

                if verbose:
                    status = "WIN" if pve_won else "LOSE"
                    print(f"  {player.name} PvE {self.stage}-{self.round_num} → {status}")

        elif round_type == "carousel":
            # Tạo carousel và cho bots chọn theo thứ tự HP thấp nhất
            slots = self.generate_carousel()
            alive_sorted = sorted(
                [p for p in self.players if p.is_alive],
                key=lambda p: p.hp   # HP thấp nhất chọn trước
            )
            used = set()
            for player in alive_sorted:
                # Bots chọn slot có item tốt nhất còn lại
                best_idx = None
                for i, slot in enumerate(slots):
                    if slot and i not in used:
                        best_idx = i
                        break
                if best_idx is not None:
                    self.pick_carousel(player, best_idx)
                    used.add(best_idx)

            if verbose:
                print(f"  Carousel — {len(slots)} slots, {len(alive_sorted)} players picked")

            # Agent sẽ chọn qua action trong env.py — không chọn ở đây

        elif round_type == "augment":
            # Generate augment offers và cho bots tự chọn
            self.generate_augment_offers()
            for player in self.players:
                if player.is_alive and player.name != "Agent":
                    offers = self._augment_offers.get(player.name, [])
                    if offers:
                        self.pick_augment(player, 0)  # Bot chọn augment đầu tiên

            if verbose:
                print("  Augment round — offers generated")

        self.process_round_end(combat_results)
        return combat_results

    def simulate_game(self, max_rounds=50, verbose=False):
        """
        Simulate toàn bộ game đến khi có winner.
        Dùng để test engine.
        """
        for _ in range(max_rounds):
            if self.is_game_over():
                break
            self.simulate_round(verbose=verbose)

        winner = self.get_winner()
        standings = self.get_standings()

        if verbose:
            print(f"\n=== GAME OVER ===")
            print(f"Winner: {winner.name if winner else 'Draw'}")
            print("Standings:")
            for i, p in enumerate(standings, 1):
                print(f"  {i}. {p.name} — HP:{p.hp}")

        return {
            "winner":     winner.name if winner else None,
            "rounds":     self.round_count,
            "standings":  [(p.name, p.hp) for p in standings],
            "match_log":  self.match_log,
        }