import numpy as np
import gymnasium as gym
from gymnasium import spaces
import sys
import os
import random

# Thêm path để import engine
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
BOARD_POSITIONS = [(r, c) for r in range(4) for c in range(7)]
N_SHOP_SLOTS  = 5
N_BENCH_SLOTS = 9
N_BOARD_SLOTS = 28
N_ITEM_SLOTS  = 10
N_OPPONENTS   = 7

ACTION_BUY_SHOP   = list(range(0, 5))
ACTION_REROLL     = 5
ACTION_BUY_XP     = 6
ACTION_SELL_BENCH = list(range(7, 16))
ACTION_PLACE_BASE = 16
ACTION_PASS       = 16 + (N_BENCH_SLOTS * N_BOARD_SLOTS) # 268
ACTION_EQUIP_ITEM_BASE = ACTION_PASS + 1               # 269
TOTAL_ACTIONS = ACTION_EQUIP_ITEM_BASE + (N_ITEM_SLOTS * N_BOARD_SLOTS) # 549
OBS_SIZE = 260 

def get_cost(champion_data, name, default=1):
    val = champion_data.get(name, default)
    return val['cost'] if isinstance(val, dict) else val

class TFTEnv(gym.Env):
    def __init__(self, champion_data, item_data=None, augment_data=None, render_mode=None):
        super().__init__()
        self.champion_data = champion_data
        self.item_data = item_data or {}
        self.champ_id_map = {name: i + 1 for i, name in enumerate(sorted(champion_data.keys()))}
        self.n_champions = len(champion_data)
        
        # Mapping Item
        self.item_list = sorted(list(self.item_data.keys())) if self.item_data else []
        self.item_id_map = {name: i + 1 for i, name in enumerate(self.item_list)}

        self.trait_id_list = sorted([k for k in DEFAULT_TRAITS.keys() if not k.startswith("_")])
        self._trait_mgr = TraitManager(None)

        self.action_space = spaces.Discrete(TOTAL_ACTIONS)
        # Để dùng Action Masking, observation thường được bọc trong dict
        self.observation_space = spaces.Dict({
            "action_mask": spaces.Box(0, 1, shape=(TOTAL_ACTIONS,), dtype=np.int8),
            "observation": spaces.Box(low=0.0, high=1.0, shape=(OBS_SIZE,), dtype=np.float32)
        })

        self.game = None
        self.agent = None
        self._prev_hp = 100
        self._prev_combat_power = 0.0

    # ==================
    # ACTION MASKING LOGIC 🎭
    # ==================

    def get_action_mask(self):
        """Tạo mask 1 cho hành động hợp lệ, 0 cho không hợp lệ ✅"""
        mask = np.zeros(TOTAL_ACTIONS, dtype=np.int8)
        econ = self.agent.econ

        # 1. Mask Mua Shop
        for i in range(N_SHOP_SLOTS):
            name = econ.shop.slots[i]
            if name and econ.gold >= get_cost(self.champion_data, name) and None in self.agent.bench:
                mask[i] = 1

        # 2. Mask Reroll & XP
        if econ.gold >= 2: mask[ACTION_REROLL] = 1
        if econ.gold >= 4 and econ.level < 10: mask[ACTION_BUY_XP] = 1

        # 3. Mask Bán Bench
        for i in range(N_BENCH_SLOTS):
            if self.agent.bench[i] is not None:
                mask[7 + i] = 1

        # 4. Mask Xếp Bài (Bench -> Board)
        for b_idx in range(N_BENCH_SLOTS):
            if self.agent.bench[b_idx] is not None:
                start_idx = ACTION_PLACE_BASE + (b_idx * N_BOARD_SLOTS)
                mask[start_idx : start_idx + N_BOARD_SLOTS] = 1

        # 5. Mask Lắp Đồ 🎒
        # Điều kiện: Có đồ trong item_bench AND tướng trên board < 3 đồ
        for i_idx in range(min(len(self.agent.item_bench), N_ITEM_SLOTS)):
            for b_idx in range(N_BOARD_SLOTS):
                r, c = BOARD_POSITIONS[b_idx]
                champ = self.agent.board.get(r, c)
                if champ and len(champ.items) < 3:
                    action_id = ACTION_EQUIP_ITEM_BASE + (i_idx * N_BOARD_SLOTS) + b_idx
                    mask[action_id] = 1

        # 6. Luôn cho phép Pass
        mask[ACTION_PASS] = 1
        return mask

    # ==================
    # CORE ENV FUNCTIONS
    # ==================

    def _get_role(self, name):
        return self.champion_data.get(name, {}).get("role", "fighter")

    def _get_role_position_reward(self, role, row):
        if role == "tank": return 0.2 if row <= 1 else -0.1
        if role in ["marksman", "caster"]: return 0.2 if row >= 2 else -0.2
        if role == "fighter": return 0.1 if 1 <= row <= 2 else 0.0
        return 0.0

    def _get_player_combat_power(self, player):
        if not player: return 0.0
        power = 0.0
        board_champs = player.get_board_champions()
        for champ in board_champs:
            base_cost = get_cost(self.champion_data, champ.name)
            power += base_cost * (champ.star ** 2)
            power += len(champ.items) * 2.0
        
        trait_counts = self._trait_mgr.count_traits(board_champs)
        for t_name, count in trait_counts.items():
            trait = self._trait_mgr.traits.get(t_name)
            if trait:
                level, _ = trait.get_active_level(count)
                power += level * 2.0 
        return power

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        player_names = ["Agent", "HardBot", "Bot2", "Bot3", "Bot4", "Bot5", "Bot6", "Bot7"]
        
        self.game = Game(player_names, self.champion_data, self.item_data, {})
        self.agent = self.game.players[0]
        self._prev_hp = 100
        self._prev_combat_power = 0.0

        self.game.stage, self.game.round_num = 2, 1
        for p in self.game.players:
            p.econ.current_stage, p.econ.current_round = 2, 1
            p.econ.gold = 10 if "HardBot" in p.name else 4 
        
        self._setup_initial_units()
        return self._get_obs_dict(), {}

    def _setup_initial_units(self):
        champ_names = list(self.champion_data.keys())
        for player in self.game.players[1:]:
            name = champ_names[0] if "HardBot" in player.name else np.random.choice(champ_names)
            c = self.game.make_champion(name)
            if "HardBot" in player.name: c.star = 2
            player.board.place(c, 0, 3)

    def _apply_loot(self):
        """Cơ chế rơi đồ ngẫu nhiên 📦"""
        if self.item_list and random.random() < 0.3: # 30% cơ hội nhận đồ mỗi round
            item_name = random.choice(self.item_list)
            # Giả định item_registry đã được khởi tạo trong game.py
            new_item = self.game.item_registry.get(item_name)
            if new_item:
                self.agent.add_to_item_bench(new_item)

    def _is_valid_action(self, action):
        """Hàm kiểm tra nhanh cho step (đã có mask nên hàm này bảo vệ thêm)"""
        mask = self.get_action_mask()
        return mask[action] == 1

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
            if champ:
                # Trả lại đồ về bench khi bán tướng ♻️
                for item in champ.items[:]:
                    self.agent.add_to_item_bench(item)
                    item.unequip(champ)
                self.agent.sell(champ)
        elif ACTION_PLACE_BASE <= action < ACTION_PASS:
            rel = action - ACTION_PLACE_BASE
            b_idx, bd_idx = rel // N_BOARD_SLOTS, rel % N_BOARD_SLOTS
            row, col = BOARD_POSITIONS[bd_idx]
            self.agent.place_on_board(b_idx, row, col)
        elif ACTION_EQUIP_ITEM_BASE <= action < TOTAL_ACTIONS:
            rel = action - ACTION_EQUIP_ITEM_BASE
            item_idx, board_idx = rel // N_BOARD_SLOTS, rel % N_BOARD_SLOTS
            r, c = BOARD_POSITIONS[board_idx]
            target = self.agent.board.get(r, c)
            if target and self.agent.equip_item_to_champ(item_idx, target):
                reward += 0.5 

        curr_p = self._get_player_combat_power(self.agent)
        reward += (curr_p - self._prev_combat_power) * 0.1 
        self._prev_combat_power = curr_p
        return reward

    def step(self, action):
        terminated = truncated = False
        reward = -0.005 

        valid = self._is_valid_action(action)
        if not valid:
            reward -= 0.1
        else:
            reward += self._apply_action(action)

        if action == ACTION_PASS or not valid:
            for r, c in BOARD_POSITIONS:
                champ = self.agent.board.get(r, c)
                if champ: reward += self._get_role_position_reward(self._get_role(champ.name), r)

            self._run_bot_logic()
            self._apply_loot() # Rơi đồ 📦
            self.game.simulate_round(verbose=False)
            
            hp_delta = self.agent.hp - self._prev_hp
            self._prev_hp = self.agent.hp
            
            if hp_delta < 0:
                reward += hp_delta * (0.3 * self.game.stage)
                reward -= 5.0 
            
            if not self.agent.is_alive or self.game.is_game_over():
                terminated = True
                standings = self.game.get_standings()
                rank = next(i for i, p in enumerate(standings) if p is self.agent)
                reward += {0:50, 1:30, 2:20, 3:10, 4:-10, 5:-25, 6:-40, 7:-80}.get(rank, -80)
            else:
                self.agent.econ.shop.roll(self.agent.level)

        return self._get_obs_dict(), float(reward), terminated, truncated, {}

    def _run_bot_logic(self):
        for player in self.game.players[1:]:
            if not player.is_alive: continue
            econ = player.econ
            if "HardBot" in player.name and econ.gold >= 10 and econ.level < 9: econ.buy_xp()
            owned_names = [c.name for c in player.get_all_champions()]
            for i, slot_name in enumerate(econ.shop.slots):
                if slot_name:
                    cost = get_cost(self.champion_data, slot_name)
                    if econ.gold >= cost and ("HardBot" in player.name or slot_name in owned_names):
                        name = econ.buy_champion(i, cost)
                        if name:
                            champ = self.game.make_champion(name)
                            if not player.add_to_board_auto(champ): player.add_to_bench(champ)

    def _get_obs_dict(self):
        """Trả về dict chứa cả Mask và Obs thực tế"""
        return {
            "action_mask": self.get_action_mask(),
            "observation": self._get_obs()
        }

    def _get_obs(self):
        obs = np.zeros(OBS_SIZE, dtype=np.float32)
        econ = self.agent.econ
        idx = 0
        
        # Stats & Econ (6)
        obs[idx:idx+6] = [econ.gold/50, self.agent.hp/100, econ.level/10, econ.xp/68, econ.win_streak/6, econ.loss_streak/6]
        idx += 6
        
        # Shop (10)
        for slot in econ.shop.slots:
            if slot:
                obs[idx] = self.champ_id_map.get(slot, 0)/self.n_champions
                obs[idx+1] = get_cost(self.champion_data, slot)/5.0
            idx += 2
            
        # Bench (18)
        for champ in self.agent.bench:
            if champ:
                obs[idx] = self.champ_id_map.get(champ.name, 0)/self.n_champions
                obs[idx+1] = champ.star/3.0
            idx += 2
            
        # Board (168)
        role_map = {"tank": 0.2, "fighter": 0.4, "marksman": 0.6, "caster": 0.8, "assassin": 1.0}
        for row, col in BOARD_POSITIONS:
            champ = self.agent.board.get(row, col)
            if champ:
                obs[idx] = self.champ_id_map.get(champ.name, 0)/self.n_champions
                obs[idx+1] = champ.star/3.0
                obs[idx+2] = role_map.get(self._get_role(champ.name), 0.1)
                for i in range(3):
                    if i < len(champ.items):
                        obs[idx+3+i] = self.item_id_map.get(champ.items[i].item_id, 0)/(len(self.item_list) + 1)
            idx += 6
            
        # Item Bench (10)
        for i in range(N_ITEM_SLOTS):
            if i < len(self.agent.item_bench):
                obs[idx] = self.item_id_map.get(self.agent.item_bench[i].item_id, 0)/(len(self.item_list) + 1)
            idx += 1
            
        # Opponents (7)
        opponents = [p for p in self.game.players if p is not self.agent]
        for opp in opponents[:N_OPPONENTS]:
            obs[idx] = opp.hp/100.0
            idx += 1
            
        # Traits
        board_champs = self.agent.get_board_champions()
        trait_counts = self._trait_mgr.count_traits(board_champs)
        for t_name in self.trait_id_list:
            if idx < OBS_SIZE:
                trait = self._trait_mgr.traits.get(t_name)
                if trait:
                    level, _ = trait.get_active_level(trait_counts.get(t_name, 0))
                    obs[idx] = level/3.0
                idx += 1
                
        return obs