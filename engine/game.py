# game.py - Game loop chính TFT Set 16
#
# Kết nối tất cả các module:
#   champion.py  - Champion class
#   board.py     - HexBoard + TileEffect
#   combat.py    - CombatSimulator
#   econ.py      - PlayerEconomy + ChampionPool + Shop
#   items.py     - Item + ItemRegistry
#   augments.py  - Augment + AugmentRegistry + AugmentManager
#
# Cấu trúc game:
#   Game
#   └── Player (x8)
#         ├── PlayerEconomy   (gold, XP, level, shop, streak)
#         ├── AugmentManager  (augments đã chọn)
#         ├── HexBoard        (4x7, hàng 0-3)
#         ├── bench           (9 slots)
#         └── roster          (dict name -> Champion instance đang có)
#
# Round structure (Set 16):
#   Stage 1: 1-1 (carousel), 1-2, 1-3, 1-4 (PvE wolves)
#   Stage 2: 2-1 (augment), 2-2, 2-3, 2-4, 2-5 (PvE)
#   Stage 3: 3-1, 3-2 (augment), 3-3, 3-4, 3-5 (PvE)
#   Stage 4: 4-1, 4-2 (augment), 4-3, 4-4, 4-5 (PvE)
#   Stage 5+: PvP liên tục
#
# Trạng thái round:
#   "carousel"   - vòng chọn trang bị (1-1, đầu mỗi stage)
#   "pve"        - đánh creep
#   "pvp"        - đánh player
#   "augment"    - chọn augment

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
    """
    Load champions.json và trả về 2 thứ:
      - champion_data_full : dict { name: {cost, hp, armor, ...} }  → dùng cho make_champion()
      - champion_cost_map  : dict { name: cost }                    → dùng cho ChampionPool / Shop

    Ví dụ dùng:
        full, cost_map = load_champions_json("data/champions.json")
        game = Game(player_names, champion_data=full)
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Lọc bỏ key _meta nếu có
    champion_data_full = {k: v for k, v in raw.items()
                          if not k.startswith("_") and isinstance(v, dict)}
    champion_cost_map  = {k: v["cost"] for k, v in champion_data_full.items()}

    return champion_data_full, champion_cost_map


# ==================
# ROUND SCHEDULE
# ==================

# (stage, round) -> type
# PvE rounds là creep rounds (Wolves, Golems, ...)
PVE_ROUNDS = {
    (1, 1), (1, 2), (1, 3), (1, 4),    # Stage 1: all PvE
    (2, 5), (3, 5), (4, 5),             # Cuối stage 2-4: PvE
}

CAROUSEL_ROUNDS = {(1, 1)}              # Carousel đầu game


# ==================
# PLAYER
# ==================

class Player:
    """
    Đại diện 1 người chơi trong game.
    Gom tất cả state của 1 player vào 1 chỗ.
    """

    def __init__(self, name, pool, augment_registry):
        self.name             = name

        # Economy
        self.econ             = PlayerEconomy(name, pool)

        # Board & bench
        self.board            = HexBoard()
        self.bench            = [None] * 9      # Tên champion hoặc None

        # Champions đang có (board + bench): name -> Champion instance
        # Nếu có nhiều bản cùng tên thì dùng list
        self.roster           = {}              # name -> list[Champion]

        # Augments
        self.augment_manager  = AugmentManager(augment_registry)

        # Trạng thái
        self.place            = 8               # Vị trí hiện tại (1-8)
        self._pool_ref        = pool            # Tham chiếu tới shared pool

    # ==================
    # CHAMPION MANAGEMENT
    # ==================

    def add_to_bench(self, champion):
        """Thêm champion vào bench (slot trống đầu tiên)"""
        for i, slot in enumerate(self.bench):
            if slot is None:
                self.bench[i] = champion
                if champion.name not in self.roster:
                    self.roster[champion.name] = []
                self.roster[champion.name].append(champion)
                return True
        return False    # Bench đầy

    def remove_from_bench(self, bench_index):
        """Lấy champion ra khỏi bench"""
        champ = self.bench[bench_index]
        if champ:
            self.bench[bench_index] = None
            if champ.name in self.roster:
                self.roster[champ.name].remove(champ)
                if not self.roster[champ.name]:
                    del self.roster[champ.name]
        return champ

    def place_on_board(self, bench_index, row, col):
        """Di chuyển champion từ bench lên board"""
        champ = self.bench[bench_index]
        if champ is None:
            return False
        try:
            self.board.place(champ, row, col)
            self.bench[bench_index] = None
            return True
        except ValueError:
            return False

    def move_to_bench(self, row, col):
        """Di chuyển champion từ board xuống bench"""
        champ = self.board.get(row, col)
        if champ is None:
            return False
        self.board.remove(champ)
        return self.add_to_bench(champ)

    def get_board_champions(self):
        """Lấy tất cả champion đang trên board (hàng 0-3)"""
        result = []
        for row in range(4):
            for col in range(7):
                champ = self.board.get(row, col)
                if champ:
                    result.append(champ)
        return result
    def get_all_champions(self):
            """
            Trả về danh sách tất cả Champion instance mà người chơi đang sở hữu
            bao gồm cả trên bàn cờ (board) và hàng chờ (bench). 🏆
            """
            # Lấy tướng từ hàng chờ (loại bỏ các vị trí None)
            bench_champs = [c for c in self.bench if c is not None]
            
            # Lấy tướng từ bàn cờ
            board_champs = self.get_board_champions()
            
            return bench_champs + board_champs

    def count_on_board(self):
        return len(self.get_board_champions())

    def can_place_more(self):
        return self.count_on_board() < self.econ.board_size

    # ==================
    # SHOP ACTIONS
    # ==================

    def buy_from_shop(self, slot_index, champion_factory):
        """
        Mua champion từ shop.
        champion_factory: hàm f(name) -> Champion instance (từ JSON data)
        Trả về Champion hoặc None.
        """
        slot = self.econ.shop.slots[slot_index]
        if slot is None:
            return None

        cost = champion_factory(slot).cost  # Lấy cost từ data
        name = self.econ.buy_champion(slot_index, cost)
        if name is None:
            return None

        champ = champion_factory(name)

        # Kiểm tra upgrade (3 bản cùng tên + sao)
        upgraded = self._try_upgrade(champ)
        if upgraded:
            return upgraded

        # Bench có trống không
        if not self.add_to_bench(champ):
            # Bench đầy → trả lại gold và champion
            self.econ.gold += champ.cost
            self._pool_ref.return_champ(name)
            return None

        return champ

    def sell(self, champion):
        """Bán champion (từ bench hoặc board)"""
        # Xóa khỏi board nếu đang ở đó
        if champion.position:
            self.board.remove(champion)

        # Xóa khỏi bench nếu đang ở đó
        for i, slot in enumerate(self.bench):
            if slot is champion:
                self.bench[i] = None
                break

        # Xóa khỏi roster
        if champion.name in self.roster:
            try:
                self.roster[champion.name].remove(champion)
            except ValueError:
                pass
            if not self.roster[champion.name]:
                del self.roster[champion.name]

        gold = self.econ.sell_champion(
            champion.name, champion.cost, champion.star, self._pool_ref)
        return gold

    def _try_upgrade(self, new_champ):
        """
        Kiểm tra nếu có đủ 3 bản 1-sao → ghép thành 1 bản 2-sao
        hoặc 3 bản 2-sao → ghép thành 1 bản 3-sao.
        Trả về champion đã upgrade hoặc None.
        """
        name = new_champ.name
        existing = self.roster.get(name, [])
        same_star = [c for c in existing if c.star == new_champ.star]
        same_star.append(new_champ)  # Cộng thêm cái vừa mua

        if len(same_star) >= 3:
            # Xóa 2 bản cũ
            for champ in same_star[:2]:
                if champ.position:
                    self.board.remove(champ)
                for i, slot in enumerate(self.bench):
                    if slot is champ:
                        self.bench[i] = None
                        break
                if name in self.roster:
                    try:
                        self.roster[name].remove(champ)
                    except ValueError:
                        pass

            # Upgrade bản thứ 3
            new_champ.upgrade_star()
            # Thêm vào roster
            if name not in self.roster:
                self.roster[name] = []
            self.roster[name].append(new_champ)

            # Tự động thử upgrade tiếp (3★)
            self._try_upgrade(new_champ)
            return new_champ

        return None

    # ==================
    # STATUS
    # ==================

    @property
    def hp(self):
        return self.econ.hp

    @property
    def is_alive(self):
        return self.econ.is_alive

    @property
    def level(self):
        return self.econ.level

    def status(self):
        board_champs = self.get_board_champions()
        bench_filled = sum(1 for s in self.bench if s)
        return (
            f"[{self.name}] "
            f"HP:{self.hp} | "
            f"Gold:{self.econ.gold} | "
            f"Lv{self.level} | "
            f"Board:{len(board_champs)}/{self.econ.board_size} | "
            f"Bench:{bench_filled}/9 | "
            f"Augments: {self.augment_manager.summary()}"
        )

    def __repr__(self):
        return self.status()


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
        self.round_count = 0       # Tổng số round đã chơi

        # Match history
        self.match_log   = []

    # ==================
    # ROUND TYPE
    # ==================

    def get_round_type(self):
        key = (self.stage, self.round_num)
        if key in CAROUSEL_ROUNDS:
            return "carousel"
        if key in PVE_ROUNDS:
            return "pve"
        if key in {r for r in AUGMENT_ROUNDS}:
            return "augment_pvp"   # Augment offer + PvP round
        return "pvp"

    def is_pvp(self):
        return self.get_round_type() in {"pvp", "augment_pvp"}

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

        # Round structure: Stage 1 có 4 rounds, stage 2+ có 5 rounds
        max_round = 4 if self.stage == 1 else 5
        if self.round_num >= max_round:
            self.stage    += 1
            self.round_num = 1
        else:
            self.round_num += 1

        # Update econ round tracking cho tất cả players
        for player in self.players:
            player.econ.current_stage = self.stage
            player.econ.current_round = self.round_num

    # ==================
    # CHAMPION FACTORY
    # ==================

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
            # PvE: tất cả "thắng" (placeholder — sau này dùng PvE creep)
            for player in self.players:
                if player.is_alive:
                    combat_results.append((player, None, {
                        "winner": "team_a", "survivors_a": [], "survivors_b": [],
                        "duration": 0, "events": []
                    }))
            if verbose:
                print("  PvE round — all players win")

        elif round_type == "carousel":
            if verbose:
                print("  Carousel round — skipped in simulation")

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