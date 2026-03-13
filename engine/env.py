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
N_SHOP_SLOTS  = 5
N_BENCH_SLOTS = 9
N_BOARD_SLOTS = 28
N_OPPONENTS   = 7

ACTION_BUY_SHOP   = list(range(0, 5))
ACTION_REROLL     = 5
ACTION_BUY_XP     = 6
ACTION_SELL_BENCH = list(range(7, 16))
ACTION_PLACE_BASE = 16
ACTION_PASS       = 16 + N_BENCH_SLOTS * N_BOARD_SLOTS
TOTAL_ACTIONS = ACTION_PASS + 1
OBS_SIZE = 119

def get_cost(champion_data, name, default=1):
    val = champion_data.get(name, default)
    return val['cost'] if isinstance(val, dict) else val

class TFTEnv(gym.Env):
    def __init__(self, champion_data, item_data=None, augment_data=None, render_mode=None):
        super().__init__()
        self.champion_data = champion_data
        self.champ_id_map = {name: i + 1 for i, name in enumerate(sorted(champion_data.keys()))}
        self.n_champions = len(champion_data)
        self.trait_id_list = sorted([k for k in DEFAULT_TRAITS.keys() if not k.startswith("_")])
        self._trait_mgr = TraitManager(None)

        self.action_space = spaces.Discrete(TOTAL_ACTIONS)
        self.observation_space = spaces.Box(low=0.0, high=1.0, shape=(OBS_SIZE,), dtype=np.float32)

        self.game = None
        self.agent = None
        self._prev_hp = 100
        self._prev_combat_power = 0.0

    def _get_player_combat_power(self, player):
        """Tính toán sức mạnh thực tế của đội hình cho bất kỳ người chơi nào 🛡️"""
        if not player: return 0.0
        power = 0.0
        board_champs = player.get_board_champions()
        for champ in board_champs:
            base_cost = get_cost(self.champion_data, champ.name)
            # Công thức: Tiền gốc * (Sao bình phương)
            power += base_cost * (champ.star ** 2)
        
        trait_counts = self._trait_mgr.count_traits(board_champs)
        for trait_name, count in trait_counts.items():
            trait = self._trait_mgr.traits.get(trait_name)
            if trait:
                level, _ = trait.get_active_level(count)
                power += level * 2.0 
        return power

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        # Khởi tạo HardBot ở vị trí người chơi thứ 2
        player_names = ["Agent", "HardBot", "Bot2", "Bot3", "Bot4", "Bot5", "Bot6", "Bot7"]
        self.game = Game(player_names, self.champion_data, {}, {})
        self.agent = self.game.players[0]
        self._prev_hp = 100
        self._prev_combat_power = 0.0

        self.game.stage, self.game.round_num = 2, 1
        for p in self.game.players:
            p.econ.current_stage, p.econ.current_round = 2, 1
            # HardBot được ưu tiên khởi đầu với 10 vàng 💰
            p.econ.gold = 10 if "HardBot" in p.name else 4 
        
        self._setup_initial_units()
        return self._get_obs(), {}

    def step(self, action):
        terminated = truncated = False
        reward = -0.005 # Phạt thời gian để tránh AI đứng yên

        valid = self._is_valid_action(action)
        if not valid:
            reward -= 0.1
        else:
            reward += self._apply_action(action)

        if action == ACTION_PASS or not valid:
            # Di chuyển sang round mới
            self._run_bot_logic()
            self.game.simulate_round(verbose=False)
            
            hp_delta = self.agent.hp - self._prev_hp
            self._prev_hp = self.agent.hp
            
            # Phạt mất máu lũy tiến theo Stage 🩸
            if hp_delta < 0:
                reward += hp_delta * (0.3 * self.game.stage)
                reward -= 5.0 # Hình phạt thua round
            
            if not self.agent.is_alive or self.game.is_game_over():
                terminated = True
                standings = self.game.get_standings()
                rank = next(i for i, p in enumerate(standings) if p is self.agent)
                
                # Tính toán các chỉ số sức mạnh cuối trận 📊
                agent_p = self._get_player_combat_power(self.agent)
                hard_bot = next((p for p in self.game.players if "HardBot" in p.name), None)
                hard_p = self._get_player_combat_power(hard_bot)
                
                other_bots = [p for p in self.game.players if "Bot" in p.name and "HardBot" not in p.name]
                avg_other_p = sum(self._get_player_combat_power(b) for b in other_bots) / len(other_bots) if other_bots else 0.0

                print(f"--- 🏁 Episode Kết Thúc: Stage {self.game.stage}-{self.game.round_num} ---")
                print(f"🏆 Xếp hạng (Top): {rank + 1}/8")
                print(f"💪 Sức mạnh Model: {agent_p:.1f}")
                print(f"🔥 Sức mạnh HardBot: {hard_p:.1f}")
                print(f"🤖 Sức mạnh TB Bot khác: {avg_other_p:.1f}")
                print("-" * 40)
                
                reward += {0:50, 1:30, 2:20, 3:10, 4:-10, 5:-25, 6:-40, 7:-80}.get(rank, -80)
            else:
                self.agent.econ.shop.roll(self.agent.level)

        return self._get_obs(), float(reward), terminated, truncated, {}

    def _is_valid_action(self, action):
        econ = self.agent.econ
        if action in ACTION_BUY_SHOP:
            name = econ.shop.slots[action]
            return name is not None and econ.gold >= get_cost(self.champion_data, name)
        if action == ACTION_REROLL: return econ.gold >= 2
        if action == ACTION_BUY_XP: return econ.gold >= 4 and econ.level < 10
        if action in ACTION_SELL_BENCH: return self.agent.bench[action - 7] is not None
        if ACTION_PLACE_BASE <= action < ACTION_PASS:
            rel = action - ACTION_PLACE_BASE
            b_idx = rel // N_BOARD_SLOTS
            return b_idx < N_BENCH_SLOTS and self.agent.bench[b_idx] is not None
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

        curr_p = self._get_player_combat_power(self.agent)
        reward += (curr_p - self._prev_combat_power) * 0.1 # Thưởng khi đội hình mạnh lên
        self._prev_combat_power = curr_p
        return reward

    def _run_bot_logic(self):
        """Nâng cấp trí tuệ nhân tạo cho đối thủ 🧠"""
        for player in self.game.players[1:]:
            if not player.is_alive: continue
            is_hard = "HardBot" in player.name
            econ = player.econ

            # HardBot biết dùng tiền để chiếm ưu thế cấp độ
            if is_hard and econ.gold >= 10 and econ.level < 9:
                econ.buy_xp()

            # Logic mua tướng thông minh hơn: Ưu tiên tướng đã có để nâng sao ⭐
            owned_names = [c.name for c in player.get_all_champions()]
            for i, slot_name in enumerate(econ.shop.slots):
                if slot_name:
                    cost = get_cost(self.champion_data, slot_name)
                    # Mua nếu đủ tiền VÀ (là HardBot HOẶC tướng đã có sẵn)
                    if econ.gold >= cost and (is_hard or slot_name in owned_names):
                        name = econ.buy_champion(i, cost)
                        if name:
                            champ = self.game.make_champion(name)
                            if not player.add_to_board_auto(champ):
                                player.add_to_bench(champ)

    def _setup_initial_units(self):
        """Khởi tạo tướng ban đầu cho Bot, HardBot được ưu tiên 👑"""
        champ_names = list(self.champion_data.keys())
        for player in self.game.players[1:]:
            if "HardBot" in player.name:
                # Tặng HardBot 1 tướng 2 sao bất kỳ
                name = champ_names[0]
                c = self.game.make_champion(name)
                c.star = 2
                player.board.place(c, 0, 3)
            else:
                # Bot thường lấy 1 tướng 1 sao
                name = np.random.choice(champ_names)
                c = self.game.make_champion(name)
                player.board.place(c, 0, 3)

    def _get_obs(self):
        # Giữ nguyên logic lấy Observation cũ để khớp với model đã train
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