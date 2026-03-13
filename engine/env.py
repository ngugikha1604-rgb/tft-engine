import numpy as np
import gymnasium as gym
from gymnasium import spaces
import sys
import os

# Thêm path để import engine
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in [_THIS_DIR, os.path.join(_THIS_DIR, "engine")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from game import Game
from econ import ChampionPool
from traits import TraitManager, DEFAULT_TRAITS

# ==================
# HẰNG SỐ (GIỮ NGUYÊN)
# ==================
BOARD_POSITIONS = [(r, c) for r in range(4) for c in range(7)]
BOARD_POS_INDEX = {pos: i for i, pos in enumerate(BOARD_POSITIONS)}

N_SHOP_SLOTS  = 5
N_BENCH_SLOTS = 9
N_BOARD_SLOTS = 28
N_OPPONENTS   = 7
N_TRAITS      = 22

ACTION_BUY_SHOP   = list(range(0, 5))
ACTION_REROLL     = 5
ACTION_BUY_XP     = 6
ACTION_SELL_BENCH = list(range(7, 16))
ACTION_PLACE_BASE = 16
ACTION_PASS       = 16 + N_BENCH_SLOTS * N_BOARD_SLOTS

TOTAL_ACTIONS = ACTION_PASS + 1
OBS_SIZE = 119

def build_champion_id_map(champion_data):
    names = sorted(champion_data.keys())
    return {name: i + 1 for i, name in enumerate(names)}

def get_cost(champion_data, name, default=1):
    val = champion_data.get(name, default)
    return val['cost'] if isinstance(val, dict) else val

# ==================
# TFT ENVIRONMENT
# ==================
class TFTEnv(gym.Env):
    metadata = {"render_modes": ["ansi"]}

    def __init__(self, champion_data, item_data=None, augment_data=None, render_mode=None):
        super().__init__()
        self.champion_data  = champion_data
        self.item_data      = item_data or {}
        self.augment_data   = augment_data or {}
        self.render_mode    = render_mode

        self.champ_id_map   = build_champion_id_map(champion_data)
        self.n_champions    = len(champion_data)
        trait_names = [k for k in DEFAULT_TRAITS.keys() if not k.startswith("_")]
        self.trait_id_list  = sorted(trait_names)
        self.n_trait_levels = 3
        self._trait_mgr     = TraitManager(None)

        self.action_space = spaces.Discrete(TOTAL_ACTIONS)
        self.observation_space = spaces.Box(low=0.0, high=1.0, shape=(OBS_SIZE,), dtype=np.float32)

        self.game        = None
        self.agent       = None
        self._prev_hp    = 100
        self._round_done = False
        self._prev_combat_power = 0.0 # Theo dõi sức mạnh đội hình

    def _calculate_combat_power(self):
        """Tính toán sức mạnh thực tế của đội hình trên bàn cờ"""
        power = 0.0
        board_champs = self.agent.get_board_champions()
        for champ in board_champs:
            base_cost = get_cost(self.champion_data, champ.name)
            # Tướng 2 sao mạnh gấp 4 lần, 3 sao mạnh gấp 9 lần
            star_multiplier = champ.star ** 2 
            power += base_cost * star_multiplier
        
        # Thưởng thêm cho các mốc Tộc/Hệ kích hoạt
        trait_counts = self._trait_mgr.count_traits(board_champs)
        for trait_name, count in trait_counts.items():
            trait = self._trait_mgr.traits.get(trait_name)
            if trait:
                level, _ = trait.get_active_level(count)
                power += level * 2.0 
        return power

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        player_names = ["Agent", "Bot1", "Bot2", "Bot3", "Bot4", "Bot5", "Bot6", "Bot7"]
        self.game = Game(player_names, self.champion_data, self.item_data, self.augment_data)
        self.agent = self.game.players[0]
        self._prev_hp = 100
        self._round_done = False
        self._prev_combat_power = 0.0

        self.game.stage, self.game.round_num = 2, 1
        for p in self.game.players:
            p.econ.current_stage, p.econ.current_round, p.econ.gold = 2, 1, 4
        
        self.agent.econ.shop.roll(self.agent.level)
        self._setup_bots()
        return self._get_obs(), {}

    def step(self, action):
        assert self.game is not None, "Gọi reset() trước"
        reward = -0.005 # Time penalty cực nhỏ cho mỗi hành động
        terminated = truncated = False

        valid = self._is_valid_action(action)
        if not valid:
            reward -= 0.1 # Phạt hành động lỗi
        else:
            reward += self._apply_action(action)

        if action == ACTION_PASS or not valid:
            reward += self._run_round()
            self._round_done = True
            if self.game.is_game_over() or not self.agent.is_alive:
                terminated = True
                reward += self._end_game_reward()
            else:
                self._round_done = False
                self.agent.econ.shop.roll(self.agent.level)

        return self._get_obs(), float(reward), terminated, truncated, {}

    def _is_valid_action(self, action):
        econ = self.agent.econ
        if action in ACTION_BUY_SHOP:
            name = econ.shop.slots[action]
            if name is None: return False
            return econ.gold >= get_cost(self.champion_data, name)
        if action == ACTION_REROLL: return econ.gold >= 2
        if action == ACTION_BUY_XP: return econ.gold >= 4 and econ.level < 10
        if action in ACTION_SELL_BENCH: return self.agent.bench[action - 7] is not None
        if ACTION_PLACE_BASE <= action < ACTION_PASS:
            rel = action - ACTION_PLACE_BASE
            b_idx, bd_idx = rel // N_BOARD_SLOTS, rel % N_BOARD_SLOTS
            if b_idx >= N_BENCH_SLOTS or self.agent.bench[b_idx] is None: return False
            return True # Cho phép swap/đè lên vị trí cũ
        return action == ACTION_PASS

    def _apply_action(self, action):
        reward = 0.0
        econ = self.agent.econ
        if action in ACTION_BUY_SHOP:
            slot = action
            name = econ.shop.slots[slot]
            cost = get_cost(self.champion_data, name)
            champ = self.game.make_champion(name)
            if econ.buy_champion(slot, cost):
                if not self.agent.add_to_bench(champ):
                    econ.gold += cost
                    self.game.pool.return_champ(name)
        elif action == ACTION_REROLL: econ.reroll()
        elif action == ACTION_BUY_XP: econ.buy_xp()
        elif action in ACTION_SELL_BENCH:
            idx = action - 7
            champ = self.agent.bench[idx]
            if champ: self.agent.sell(champ)
        elif ACTION_PLACE_BASE <= action < ACTION_PASS:
            rel = action - ACTION_PLACE_BASE
            b_idx, bd_idx = rel // N_BOARD_SLOTS, rel % N_BOARD_SLOTS
            row, col = BOARD_POSITIONS[bd_idx]
            self.agent.place_on_board(b_idx, row, col)

        # Thưởng chênh lệch sức mạnh sau hành động
        current_power = self._calculate_combat_power()
        reward += (current_power - self._prev_combat_power) * 0.1
        self._prev_combat_power = current_power
        return reward

    def _run_round(self):
        self._run_bots()
        results = self.game.simulate_round(verbose=False)
        
        # 1. Phạt tồn tại mỗi round
        reward = -1.0 
        # 2. HP Delta (với multiplier tăng theo Stage)
        hp_now = self.agent.hp
        hp_delta = hp_now - self._prev_hp
        self._prev_hp = hp_now
        reward += hp_delta * (0.2 + self.game.stage * 0.1)

        # 3. Bonus thắng round
        if hp_delta == 0 and self.game.is_pvp():
            reward += 3.0
        # 4. Thưởng lợi tức
        reward += min(self.agent.econ.gold // 10, 5) * 0.02
        return reward

    def _end_game_reward(self):
        standings = self.game.get_standings()
        rank = next((i for i, p in enumerate(standings) if p is self.agent), 7)
        REWARDS = {0: 50.0, 1: 30.0, 2: 20.0, 3: 10.0, 4: -10.0, 5: -25.0, 6: -40.0, 7: -80.0}
        return REWARDS.get(rank, -80.0)

    def _get_obs(self):
        obs = np.zeros(OBS_SIZE, dtype=np.float32)
        econ = self.agent.econ
        idx = 0
        obs[idx:idx+6] = [econ.gold/50, self.agent.hp/100, econ.level/10, econ.xp/68, econ.win_streak/6, econ.loss_streak/6]
        idx += 6
        for slot in econ.shop.slots:
            if slot:
                obs[idx] = self.champ_id_map.get(slot, 0)/self.n_champions
                obs[idx+1] = get_cost(self.champion_data, slot)/5.0
            idx += 2
        for champ in self.agent.bench:
            if champ:
                obs[idx] = self.champ_id_map.get(champ.name, 0)/self.n_champions
                obs[idx+1] = champ.star/3.0
            idx += 2
        for row, col in BOARD_POSITIONS:
            champ = self.agent.board.get(row, col)
            if champ:
                obs[idx] = self.champ_id_map.get(champ.name, 0)/self.n_champions
                obs[idx+1] = champ.star/3.0
            idx += 2
        opponents = [p for p in self.game.players if p is not self.agent]
        for opp in opponents[:N_OPPONENTS]:
            obs[idx] = opp.hp/100.0
            idx += 1
        board_champs = self.agent.get_board_champions()
        trait_counts = self._trait_mgr.count_traits(board_champs)
        for t_name in self.trait_id_list:
            trait = self._trait_mgr.traits.get(t_name)
            if trait:
                level, _ = trait.get_active_level(trait_counts.get(t_name, 0))
                obs[idx] = level/3.0
            idx += 1
        return obs

    def _setup_bots(self):
        champ_names = list(self.champion_data.keys())
        for player in self.game.players[1:]:
            random_names = np.random.choice(champ_names, size=min(3, len(champ_names)), replace=False)
            for col, name in enumerate(random_names):
                champ = self.game.make_champion(name)
                try: player.board.place(champ, 0, col + 2)
                except: pass

    def _run_bots(self):
        for player in self.game.players[1:]:
            if not player.is_alive: continue
            econ = player.econ
            if econ.gold >= 10: econ.reroll()
            for i, slot in enumerate(econ.shop.slots):
                if slot:
                    cost = get_cost(self.champion_data, slot)
                    if econ.gold >= cost:
                        name = econ.buy_champion(i, cost)
                        if name:
                            champ = self.game.make_champion(name)
                            placed = False
                            for r in range(4):
                                for c in range(7):
                                    if player.board.is_empty(r,c) and player.can_place_more():
                                        try: 
                                            player.board.place(champ, r, c)
                                            placed = True
                                        except: pass
                                    if placed: break
                                if placed: break
                            if not placed: player.add_to_bench(champ)
                        break

    def render(self):
        if self.render_mode != "ansi": return
        print(f"\n[Stage {self.game.stage}-{self.game.round_num}] HP: {self.agent.hp} Gold: {self.agent.econ.gold}")
        print(f"Board: {[f'{c.name}({c.star}*)' for c in self.agent.get_board_champions()]}")

    def close(self):
        self.game = None