# env.py - TFT Gymnasium Environment
#
# Interface chuẩn Gymnasium để train RL agent.
#
# Observation space (96 features, tất cả normalize về [0, 1]):
#   [0]     gold / 50
#   [1]     hp / 100
#   [2]     level / 10
#   [3]     xp / 68
#   [4]     win_streak / 6
#   [5]     loss_streak / 6
#   [6-15]  shop: 5 slots × (champ_id/N, cost/5)
#   [16-33] bench: 9 slots × (champ_id/N, star/3)
#   [34-89] board: 28 ô × (champ_id/N, star/3)
#   [90-96] opponents: 7 players × hp/100
#
# Action space (269 actions discrete):
#   0-4   : mua slot shop 0-4
#   5     : reroll
#   6     : mua XP
#   7-15  : bán bench slot 0-8
#   16-268: đặt bench[i] lên board[j] = 16 + i*28 + j  (i=0..8, j=0..27)
#   269   : pass (không làm gì)
#
# Reward:
#   Mỗi round: delta HP so với round trước (âm khi thua, dương khi thắng)
#   Cuối game: +10 nếu top 4, +20 nếu top 1, -5 nếu bottom 4

import numpy as np
import gymnasium as gym
from gymnasium import spaces
import sys
import os

# Thêm path để import engine — tìm cả thư mục hiện tại lẫn thư mục cha
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in [_THIS_DIR, os.path.join(_THIS_DIR, "engine")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from game import Game
from econ import ChampionPool
from traits import TraitManager, DEFAULT_TRAITS


# ==================
# HẰNG SỐ
# ==================

# Board positions: 4 hàng × 7 cột = 28 ô
BOARD_POSITIONS = [(r, c) for r in range(4) for c in range(7)]
BOARD_POS_INDEX = {pos: i for i, pos in enumerate(BOARD_POSITIONS)}

N_SHOP_SLOTS  = 5
N_BENCH_SLOTS = 9
N_BOARD_SLOTS = 28      # 4×7
N_OPPONENTS   = 7       # 8 players - 1
N_TRAITS      = 22      # số traits trong DEFAULT_TRAITS (không tính _meta)

# Action indices
ACTION_BUY_SHOP   = list(range(0, 5))          # 0-4
ACTION_REROLL     = 5
ACTION_BUY_XP     = 6
ACTION_SELL_BENCH = list(range(7, 16))          # 7-15
ACTION_PLACE_BASE = 16                          # 16 + i*28 + j
ACTION_PASS       = 16 + N_BENCH_SLOTS * N_BOARD_SLOTS   # 269

TOTAL_ACTIONS = ACTION_PASS + 1                 # 270

# Observation size
OBS_SIZE = (
    6 +                         # player state
    N_SHOP_SLOTS  * 2 +         # shop (id + cost)
    N_BENCH_SLOTS * 2 +         # bench (id + star)
    N_BOARD_SLOTS * 2 +         # board (id + star)
    N_OPPONENTS   +             # opponents hp
    N_TRAITS                    # active trait levels (0=inactive, 1/2/3=level)
)   # = 6 + 10 + 18 + 56 + 7 + 22 = 119


# ==================
# CHAMPION ID MAP
# ==================

def build_champion_id_map(champion_data):
    """
    Tạo mapping: tên champion → ID số (bắt đầu từ 1, 0 = trống)
    """
    names = sorted(champion_data.keys())
    return {name: i + 1 for i, name in enumerate(names)}


def get_cost(champion_data, name, default=1):
    """Helper: lấy cost từ champion_data dù là dict đầy đủ hay {name: cost}"""
    val = champion_data.get(name, default)
    return val['cost'] if isinstance(val, dict) else val


# ==================
# TFT ENVIRONMENT
# ==================

class TFTEnv(gym.Env):
    """
    TFT Single-agent environment.
    Agent điều khiển 1 player, 7 player còn lại là bot (random/rule-based).

    Một "episode" = 1 ván game hoàn chỉnh (đến khi agent thắng hoặc thua).
    Mỗi "step" = 1 action trong planning phase của 1 round.

    Planning phase: agent có thể thực hiện nhiều actions trong 1 round
    (mua, bán, reroll, đặt tướng) cho đến khi gọi action PASS → kết thúc round.
    """

    metadata = {"render_modes": ["ansi"]}

    def __init__(self, champion_data, item_data=None,
                 augment_data=None, render_mode=None):
        super().__init__()

        self.champion_data  = champion_data
        self.item_data      = item_data or {}
        self.augment_data   = augment_data or {}
        self.render_mode    = render_mode

        # Champion ID map (dùng để encode observation)
        self.champ_id_map   = build_champion_id_map(champion_data)
        self.n_champions    = len(champion_data)

        # Trait ID map để encode observation
        trait_names = [k for k in DEFAULT_TRAITS.keys() if not k.startswith("_")]
        self.trait_id_list  = sorted(trait_names)   # thứ tự cố định
        self.n_trait_levels = 3                      # max level
        self._trait_mgr     = TraitManager(None)     # reuse, tránh tạo mới mỗi step

        # ==================
        # Spaces
        # ==================
        self.action_space = spaces.Discrete(TOTAL_ACTIONS)

        self.observation_space = spaces.Box(
            low   = 0.0,
            high  = 1.0,
            shape = (OBS_SIZE,),
            dtype = np.float32,
        )

        # Game state (khởi tạo trong reset)
        self.game        = None
        self.agent       = None     # Player object của agent
        self._prev_hp    = 100
        self._round_done = False    # True khi agent đã PASS round này

    # ==================
    # RESET
    # ==================

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)

        # Tạo game mới
        player_names = ["Agent", "Bot1", "Bot2", "Bot3",
                        "Bot4", "Bot5", "Bot6", "Bot7"]
        self.game = Game(
            player_names  = player_names,
            champion_data = self.champion_data,
            item_data     = self.item_data,
            augment_data  = self.augment_data,
        )

        # Agent là player đầu tiên
        self.agent    = self.game.players[0]
        self._prev_hp = 100
        self._round_done = False

        # Bắt đầu từ stage 2-1 (skip stage 1 carousel/PvE)
        self.game.stage     = 2
        self.game.round_num = 1
        for p in self.game.players:
            p.econ.current_stage = 2
            p.econ.current_round = 1
            p.econ.gold = 4     # Starting gold

        # Roll shop đầu tiên cho agent
        self.agent.econ.shop.roll(self.agent.level)

        # Reward shaping trackers
        self._prev_board_size  = 0      # số tướng trên board round trước
        self._prev_level       = 1      # level round trước
        self._prev_trait_count = 0      # số traits active round trước
        self._prev_gold        = 4      # gold round trước

        # Bot đặt champions ngẫu nhiên lên board
        self._setup_bots()

        obs  = self._get_obs()
        info = {}
        return obs, info

    # ==================
    # STEP
    # ==================

    def step(self, action):
        assert self.game is not None, "Gọi reset() trước"

        reward     = 0.0
        terminated = False
        truncated  = False
        info       = {}

        # Kiểm tra action có hợp lệ không
        valid = self._is_valid_action(action)

        if not valid:
            # Phạt nhẹ nếu chọn action không hợp lệ
            reward -= 0.1
        else:
            reward += self._apply_action(action)

        # Nếu agent PASS hoặc invalid liên tục → kết thúc planning phase
        if action == ACTION_PASS or not valid:
            reward += self._run_round()
            self._round_done = True

            # Kiểm tra game kết thúc
            if self.game.is_game_over() or not self.agent.is_alive:
                terminated = True
                reward    += self._end_game_reward()
            else:
                self._round_done = False
                # Roll shop mới cho round tiếp
                self.agent.econ.shop.roll(self.agent.level)

        obs = self._get_obs()
        return obs, reward, terminated, truncated, info

    # ==================
    # ACTIONS
    # ==================

    def _is_valid_action(self, action):
        """Kiểm tra action có thể thực hiện không"""
        econ = self.agent.econ

        # Mua shop
        if action in ACTION_BUY_SHOP:
            slot = action
            name = econ.shop.slots[slot]
            if name is None:
                return False
            cost = get_cost(self.champion_data, name)
            return econ.gold >= cost

        # Reroll
        if action == ACTION_REROLL:
            return econ.gold >= 2

        # Mua XP
        if action == ACTION_BUY_XP:
            return econ.gold >= 4 and econ.level < 10

        # Bán bench
        if action in ACTION_SELL_BENCH:
            idx = action - 7
            return self.agent.bench[idx] is not None

        # Đặt bench lên board
        if ACTION_PLACE_BASE <= action < ACTION_PASS:
            relative = action - ACTION_PLACE_BASE
            bench_idx = relative // N_BOARD_SLOTS
            board_idx = relative % N_BOARD_SLOTS
            if bench_idx >= N_BENCH_SLOTS:
                return False
            if self.agent.bench[bench_idx] is None:
                return False
            row, col = BOARD_POSITIONS[board_idx]
            if not self.agent.board.is_empty(row, col):
                return False
            return self.agent.can_place_more() or True  # Swap cũng ok

        # Pass
        if action == ACTION_PASS:
            return True

        return False

    def _apply_action(self, action):
        """Thực hiện action, trả về immediate reward"""
        reward = 0.0
        econ   = self.agent.econ

        # Mua shop
        if action in ACTION_BUY_SHOP:
            slot  = action
            name  = econ.shop.slots[slot]
            cost  = get_cost(self.champion_data, name)
            champ = self.game.make_champion(name)
            bought = econ.buy_champion(slot, cost)
            if bought:
                added = self.agent.add_to_bench(champ)
                if not added:
                    econ.gold += cost
                    self.game.pool.return_champ(name)

        # Reroll
        elif action == ACTION_REROLL:
            econ.reroll()

        # Mua XP
        elif action == ACTION_BUY_XP:
            before = econ.level
            econ.buy_xp()
            if econ.level > before:
                reward += 0.5   # Gợi ý nhẹ khi level up

        # Bán bench
        elif action in ACTION_SELL_BENCH:
            idx   = action - 7
            champ = self.agent.bench[idx]
            if champ:
                self.agent.sell(champ)

        # Đặt bench lên board
        elif ACTION_PLACE_BASE <= action < ACTION_PASS:
            relative  = action - ACTION_PLACE_BASE
            bench_idx = relative // N_BOARD_SLOTS
            board_idx = relative % N_BOARD_SLOTS
            row, col  = BOARD_POSITIONS[board_idx]
            # Lấy champ từ bench trước khi đặt
            placed_champ = self.agent.bench[bench_idx]
            before_board = len(self.agent.get_board_champions())
            self.agent.place_on_board(bench_idx, row, col)
            after_board = len(self.agent.get_board_champions())

            if after_board > before_board:
                # Gợi ý nhẹ khi đặt tướng
                reward += 0.05

                # Thưởng nhẹ nếu tạo trait mới
                board_champs = self.agent.get_board_champions()
                bonuses      = self._trait_mgr.calc_bonuses(board_champs)
                active_now   = len(bonuses)
                if active_now > self._prev_trait_count:
                    reward += 0.05 * (active_now - self._prev_trait_count)
                    self._prev_trait_count = active_now

                # Thưởng positioning: tank hàng đầu, carry hàng sau
                reward += self._positioning_reward(placed_champ, row)

        return reward

    def _positioning_reward(self, champ, row):
        """
        Thưởng nhỏ khi đặt đúng vị trí:
        - Tank (HP cao) ở row 0-1 (hàng đầu)
        - Carry/Mage (AD/mana cao) ở row 2-3 (hàng sau)
        """
        if champ is None:
            return 0.0

        hp  = getattr(champ, 'max_hp', 0)
        ad  = getattr(champ, 'ad', 0)
        mana= getattr(champ, 'max_mana', 0)

        # Xác định role dựa theo chỉ số cao nhất
        # Normalize: HP thường 500-1200, AD 50-150, mana 50-150
        hp_score   = hp / 1000.0
        ad_score   = ad / 100.0
        mana_score = mana / 100.0

        is_tank  = hp_score > ad_score and hp_score > mana_score
        is_carry = ad_score >= hp_score or mana_score >= hp_score

        front_row = row <= 1   # row 0-1 = hàng đầu
        back_row  = row >= 2   # row 2-3 = hàng sau

        if is_tank and front_row:
            return 0.05     # tank đứng đúng chỗ
        elif is_carry and back_row:
            return 0.05     # carry đứng đúng chỗ
        elif is_tank and back_row:
            return -0.05    # tank đứng sai chỗ
        elif is_carry and front_row:
            return -0.05    # carry đứng sai chỗ

        return 0.0

    # ==================
    # RUN ROUND
    # ==================

    def _run_round(self):
        """
        Kết thúc planning phase, chạy combat round.
        Bots thực hiện actions đơn giản trước khi combat.
        Trả về reward từ kết quả round.
        """
        # Snapshot trước combat
        board_champs = self.agent.get_board_champions()
        board_size   = len(board_champs)

        # Bots reroll và mua tướng đơn giản
        self._run_bots()

        # Chạy 1 round
        results = self.game.simulate_round(verbose=False)

        # ── Base: phạt tồn tại mỗi round ─────────────
        reward = -1.0

        # ── 1. HP delta ──────────────────────────────
        hp_now   = self.agent.hp
        hp_delta = hp_now - self._prev_hp
        self._prev_hp = hp_now
        reward += hp_delta * 0.2    # mỗi HP mất = -0.2

        # ── 2. Thắng/thua round ──────────────────────
        if hp_delta == 0 and self.game.is_pvp():
            reward += 3.0           # Thắng → bù được phạt tồn tại + dư
        elif hp_delta < 0:
            reward -= 2.0           # Thua → phạt nặng thêm

        # ── 3. Board trống → phạt nặng ───────────────
        if board_size == 0:
            reward -= 3.0
        self._prev_board_size = board_size

        # ── 4. Trait active → gợi ý nhẹ ─────────────
        bonuses     = self._trait_mgr.calc_bonuses(board_champs)
        trait_count = len(bonuses)
        reward += 0.05 * trait_count
        self._prev_trait_count = trait_count

        # ── 5. Interest / economy ────────────────────
        gold_now = self.agent.econ.gold
        interest = min(gold_now // 10, 5)
        reward += 0.02 * interest   # +0.02 mỗi lợi tức

        return reward

    # ==================
    # END GAME REWARD
    # ==================

    def _end_game_reward(self):
        """Reward cuối game dựa trên vị trí"""
        standings = self.game.get_standings()
        rank = next((i for i, p in enumerate(standings)
                     if p is self.agent), 7)

        # rank 0 = 1st, rank 7 = 8th
        PLACEMENT_REWARD = {
            0:  20.0,   # 1st
            1:  10.0,   # 2nd
            2:   5.0,   # 3rd
            3:   1.0,   # 4th
            4:  -5.0,   # 5th
            5: -10.0,   # 6th
            6: -15.0,   # 7th
            7: -30.0,   # 8th
        }
        return PLACEMENT_REWARD.get(rank, -30.0)

    # ==================
    # OBSERVATION
    # ==================

    def _get_obs(self):
        """Encode game state thành vector float32 [0, 1]"""
        obs  = np.zeros(OBS_SIZE, dtype=np.float32)
        econ = self.agent.econ
        idx  = 0

        # Player state (6)
        obs[idx] = econ.gold / 50.0;                idx += 1
        obs[idx] = self.agent.hp / 100.0;           idx += 1
        obs[idx] = econ.level / 10.0;               idx += 1
        obs[idx] = econ.xp / 68.0;                  idx += 1
        obs[idx] = econ.win_streak / 6.0;           idx += 1
        obs[idx] = econ.loss_streak / 6.0;          idx += 1

        # Shop (10)
        for slot in econ.shop.slots:
            if slot is not None:
                obs[idx]     = self.champ_id_map.get(slot, 0) / self.n_champions
                obs[idx + 1] = get_cost(self.champion_data, slot) / 5.0
            idx += 2

        # Bench (18)
        for champ in self.agent.bench:
            if champ is not None:
                obs[idx]     = self.champ_id_map.get(champ.name, 0) / self.n_champions
                obs[idx + 1] = champ.star / 3.0
            idx += 2

        # Board (56) — duyệt theo thứ tự BOARD_POSITIONS
        for row, col in BOARD_POSITIONS:
            champ = self.agent.board.get(row, col)
            if champ is not None:
                obs[idx]     = self.champ_id_map.get(champ.name, 0) / self.n_champions
                obs[idx + 1] = champ.star / 3.0
            idx += 2

        # Opponents HP (7)
        opponents = [p for p in self.game.players if p is not self.agent]
        for i, opp in enumerate(opponents[:N_OPPONENTS]):
            obs[idx] = opp.hp / 100.0
            idx += 1

        # Active traits (22) — level của từng trait / max_level
        board_champs = self.agent.get_board_champions()
        trait_counts = self._trait_mgr.count_traits(board_champs)
        for trait_name in self.trait_id_list:
            trait = self._trait_mgr.traits.get(trait_name)
            if trait:
                level, _ = trait.get_active_level(trait_counts.get(trait_name, 0))
                obs[idx] = level / self.n_trait_levels
            idx += 1

        return obs

    # ==================
    # BOT LOGIC
    # ==================

    def _setup_bots(self):
        """Đặt champions ngẫu nhiên lên board cho bots lúc đầu game"""
        champ_names = list(self.champion_data.keys())
        for player in self.game.players[1:]:
            random_names = np.random.choice(champ_names,
                                            size=min(3, len(champ_names)),
                                            replace=False)
            for col, name in enumerate(random_names):
                champ = self.game.make_champion(name)
                try:
                    player.board.place(champ, 0, col + 2)
                except Exception:
                    pass

    def _run_bots(self):
        """
        Bot đơn giản: nếu đủ gold thì mua champion đầu tiên trong shop.
        Sau này có thể thay bằng rule-based bot thông minh hơn.
        """
        for player in self.game.players[1:]:
            if not player.is_alive:
                continue
            econ = player.econ
            # Reroll nếu nhiều gold
            if econ.gold >= 10:
                econ.reroll()
            # Mua slot đầu tiên có thể mua được
            for i, slot in enumerate(econ.shop.slots):
                if slot is None:
                    continue
                cost = get_cost(self.champion_data, slot)
                if econ.gold >= cost:
                    name = econ.buy_champion(i, cost)
                    if name:
                        champ = self.game.make_champion(name)
                        # Đặt lên board nếu còn chỗ
                        placed = False
                        for r in range(4):
                            for c in range(7):
                                if player.board.is_empty(r, c) and player.can_place_more():
                                    try:
                                        player.board.place(champ, r, c)
                                        placed = True
                                    except Exception:
                                        pass
                                    if placed:
                                        break
                            if placed:
                                break
                        if not placed:
                            player.add_to_bench(champ)
                    break   # Chỉ mua 1 champion mỗi round

    # ==================
    # RENDER
    # ==================

    def render(self):
        if self.render_mode != "ansi":
            return
        econ = self.agent.econ
        print(f"\n[Stage {self.game.stage}-{self.game.round_num}]")
        print(self.agent.status())
        print(f"Shop: {econ.shop.slots}")
        board_champs = self.agent.get_board_champions()
        print(f"Board: {[f'{c.name}({c.star}★)' for c in board_champs]}")

    def close(self):
        self.game = None