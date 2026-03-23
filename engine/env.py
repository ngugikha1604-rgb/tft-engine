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
from logger import TFTLogger

# MaskablePPO import cho ModelBot — optional, không crash nếu chưa cài
try:
    from sb3_contrib import MaskablePPO
    _SB3_CONTRIB = True
except ImportError:
    _SB3_CONTRIB = False

# ==================
# HẰNG SỐ 
# ==================
BOARD_POSITIONS = [(r, c) for r in range(4) for c in range(7)]
N_SHOP_SLOTS  = 5
N_BENCH_SLOTS = 9
N_BOARD_SLOTS = 28
N_ITEM_SLOTS  = 10
N_OPPONENTS   = 7

ACTION_BUY_SHOP        = list(range(0, 5))
ACTION_REROLL          = 5
ACTION_BUY_XP          = 6
ACTION_SELL_BENCH      = list(range(7, 16))
ACTION_PLACE_BASE      = 16
ACTION_PASS            = 16 + (N_BENCH_SLOTS * N_BOARD_SLOTS)  # 268
ACTION_EQUIP_ITEM_BASE = ACTION_PASS + 1                        # 269
ACTION_CAROUSEL_BASE   = ACTION_EQUIP_ITEM_BASE + (N_ITEM_SLOTS * N_BOARD_SLOTS)  # 549
N_CAROUSEL_SLOTS       = 9
ACTION_AUGMENT_BASE    = ACTION_CAROUSEL_BASE + N_CAROUSEL_SLOTS  # 558
N_AUGMENT_OPTIONS      = 3
TOTAL_ACTIONS          = ACTION_AUGMENT_BASE + N_AUGMENT_OPTIONS  # 561

OBS_SIZE = 260 + N_CAROUSEL_SLOTS * 2 + N_AUGMENT_OPTIONS * 2  # 284 

def get_cost(champion_data, name, default=1):
    val = champion_data.get(name, default)
    return val['cost'] if isinstance(val, dict) else val

class TFTEnv(gym.Env):
    def __init__(self, champion_data, item_data=None, augment_data=None,
                 model_bot_path=None, render_mode=None):
        super().__init__()
        self.champion_data = champion_data
        self.item_data     = item_data or {}
        self.champ_id_map  = {name: i+1 for i, name in enumerate(sorted(champion_data.keys()))}
        self.n_champions   = len(champion_data)

        # Mapping Item
        self.item_list    = sorted(list(self.item_data.keys())) if self.item_data else []
        self.item_id_map  = {name: i+1 for i, name in enumerate(self.item_list)}

        self.trait_id_list = sorted([k for k in DEFAULT_TRAITS.keys() if not k.startswith("_")])
        self._trait_mgr    = TraitManager(None)

        self.action_space = spaces.Discrete(TOTAL_ACTIONS)
        self.observation_space = spaces.Dict({
            "action_mask": spaces.Box(0, 1, shape=(TOTAL_ACTIONS,), dtype=np.int8),
            "observation": spaces.Box(low=0.0, high=1.0, shape=(OBS_SIZE,), dtype=np.float32)
        })

        # ModelBot — lưu path để reload mỗi episode
        self._model_bot      = None
        self._model_bot_path = model_bot_path   # path hoặc dir chứa checkpoints
        self._model_bot_reload_every = 10       # reload mỗi 10 episodes
        if model_bot_path and _SB3_CONTRIB:
            self._reload_model_bot(model_bot_path)

        self.game   = None
        self.agent  = None
        self._prev_hp             = 100
        self._prev_combat_power   = 0.0

        # Logger — mặc định train mode
        # Đổi mode='replay' khi muốn ghi đầy đủ
        self.logger = TFTLogger(log_dir='logs', mode='train')

    def _reload_model_bot(self, path=None):
        """Reload ModelBot từ checkpoint mới nhất"""
        if not _SB3_CONTRIB:
            return
        try:
            load_path = path or self._model_bot_path
            if not load_path:
                return
            # Nếu là thư mục → tìm checkpoint mới nhất
            import glob as _glob
            import os
            if os.path.isdir(load_path):
                ckpts = sorted(
                    _glob.glob(os.path.join(load_path, '*.zip')),
                    key=lambda x: int(x.split('_steps')[0].split('_')[-1])
                    if '_steps' in x else 0
                )
                if not ckpts:
                    return
                load_path = ckpts[-1]
            self._model_bot = MaskablePPO.load(load_path, device='cpu')
        except Exception:
            pass   # Giữ model cũ nếu load fail

    # ==================
    # ACTION MASKING LOGIC 🎭
    # ==================

    def get_action_mask(self):
        """Mask cho agent — dùng bởi ActionMasker wrapper"""
        if self.agent is None or self.game is None:
            mask = np.zeros(TOTAL_ACTIONS, dtype=np.int8)
            mask[ACTION_PASS] = 1
            return mask
        mask = self._get_action_mask_for(self.agent)
        # Đảm bảo luôn có ít nhất 1 action hợp lệ
        if mask.sum() == 0:
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

        # 8 players: Agent + 4 loại bot + 3 random
        player_names = [
            "Agent",
            "EconBot",       # giữ gold, level up đúng lúc
            "RerollBot",     # reroll liên tục ghép 3-star
            "ModelBot",      # dùng checkpoint cũ
            "BossBot",       # được buff economy + items
            "ModelBot2",     # model bot thứ 2
            "ModelBot3",     # model bot thứ 3
            "ModelBot4",     # model bot thứ 4
        ]

        self.game  = Game(player_names, self.champion_data, self.item_data, {})
        self.agent = self.game.players[0]
        self._prev_hp           = 100
        self._prev_combat_power = 0.0

        self.game.stage, self.game.round_num = 2, 1
        for p in self.game.players:
            p.econ.current_stage = 2
            p.econ.current_round = 1
            # Gold khởi đầu theo loại bot
            if p.name == "BossBot":
                p.econ.gold = 20
            elif p.name == "EconBot":
                p.econ.gold = 15       # EconBot giữ tiền nhiều hơn
            elif p.name == "RerollBot":
                p.econ.gold = 12
            elif p.name in ("ModelBot", "ModelBot2", "ModelBot3", "ModelBot4"):
                p.econ.gold = 12
            else:
                p.econ.gold = 4

        self._setup_initial_units()

        # Apply encounter đầu game
        self.game.apply_encounter()

        # Roll shop đầu tiên cho agent
        self.agent.econ.shop.roll(self.agent.econ.level)

        # Reload ModelBot mỗi _model_bot_reload_every episodes
        self._episode_count = getattr(self, '_episode_count', 0) + 1
        if (self._model_bot_path and _SB3_CONTRIB and
                self._episode_count % self._model_bot_reload_every == 0):
            self._reload_model_bot()

        # Tracking cho stats in ra
        self._episode_rewards    = []
        self._episode_placements = getattr(self, '_episode_placements', [])
        self._print_every        = 50   # in mỗi 50 game

        # Reset placements nếu đây là episode đầu tiên (mới train)
        if self._episode_count == 1:
            self._episode_placements = []
        self._total_reward     = 0.0
        self._rounds_survived  = 0
        self._alive_when_died  = None   # Reset mỗi episode

        # Logger
        self.logger.on_episode_start(self._episode_count)

        return self._get_obs_dict(), {}

    def _setup_initial_units(self):
        """Đặt tướng ban đầu cho bots"""
        champ_names = list(self.champion_data.keys())
        cost1_champs = [n for n, d in self.champion_data.items()
                        if isinstance(d, dict) and d.get("cost", 1) == 1]

        for player in self.game.players[1:]:
            if player.name == "BossBot":
                # BossBot bắt đầu với 2 tướng 2-star
                for col in [2, 4]:
                    name = random.choice(champ_names)
                    c    = self.game.make_champion(name)
                    c.star = 2
                    player.board.place(c, 0, col)
                # Và 2 items ngẫu nhiên
                self._give_items_to_player(player, 2)

            elif player.name == "EconBot":
                # EconBot: 1 tướng 1-star, không items
                name = random.choice(cost1_champs) if cost1_champs else champ_names[0]
                c    = self.game.make_champion(name)
                player.board.place(c, 0, 3)

            elif player.name == "RerollBot":
                # RerollBot: 2 tướng 1-star để có gì reroll ngay
                for col in [2, 4]:
                    name = random.choice(cost1_champs) if cost1_champs else champ_names[0]
                    c    = self.game.make_champion(name)
                    player.board.place(c, 0, col)

            elif player.name in ("ModelBot", "ModelBot2", "ModelBot3", "ModelBot4"):
                # ModelBot: 1 tướng 2-star + 1 item
                name = random.choice(cost1_champs) if cost1_champs else champ_names[0]
                c    = self.game.make_champion(name)
                c.star = 2
                player.board.place(c, 0, 3)
                self._give_items_to_player(player, 1)
            else:
                name = random.choice(champ_names)
                c    = self.game.make_champion(name)
                player.board.place(c, 0, 3)

    def _give_items_to_player(self, player, count):
        """Cho player một số items ngẫu nhiên"""
        if not self.item_list:
            return
        for _ in range(count):
            item_id  = random.choice(self.item_list)
            item_obj = self.game.item_registry.get(item_id)
            if item_obj:
                player.add_item_to_bench(item_obj)

    def _apply_bot_stage_buff(self):
        """Hướng 1: Bot được buff thêm khi lên stage mới"""
        stage = self.game.stage

        for player in self.game.players[1:]:
            if not player.is_alive:
                continue

            # Stage 3: bot nhận thêm 1 tướng ngẫu nhiên
            if stage == 3 and self.game.round_num == 1:
                champ_names  = list(self.champion_data.keys())
                cost1_champs = [n for n, d in self.champion_data.items()
                                if isinstance(d, dict) and d.get("cost", 1) == 1]
                name = random.choice(cost1_champs) if cost1_champs else champ_names[0]
                c    = self.game.make_champion(name)
                if not player.add_to_board_auto(c):
                    player.add_to_bench(c)

            # Stage 4: bot nhận thêm 1 item + level up miễn phí
            elif stage == 4 and self.game.round_num == 1:
                self._give_items_to_player(player, 1)
                if player.econ.level < 7:
                    player.econ.level = 7

            # Stage 5+: bot nhận thêm gold mỗi round
            elif stage >= 5:
                if player.name == "BossBot":
                    player.econ.gold += 3
                elif player.name in ("ModelBot", "ModelBot2", "ModelBot3", "ModelBot4"):
                    player.econ.gold += 2
                else:
                    player.econ.gold += 1

    def _apply_adaptive_buff(self):
        """Hướng 3: Bot tự động buff khi agent đang thắng quá dễ"""
        win_streak = self.agent.econ.win_streak
        agent_hp   = self.agent.hp

        # Agent win streak >= 3 → tất cả bot nhận thêm gold
        if win_streak >= 3:
            for player in self.game.players[1:]:
                if player.is_alive:
                    player.econ.gold += win_streak  # +3, +4, +5... tùy streak

        # Agent HP vẫn 100 sau stage 3 → bot nhận thêm item
        if self.game.stage >= 4 and agent_hp == 100:
            for player in self.game.players[1:]:
                if player.is_alive and player.name != "BossBot":
                    if random.random() < 0.3:   # 30% chance mỗi bot
                        self._give_items_to_player(player, 1)

    def _apply_loot(self):
        """Rơi đồ ngẫu nhiên mỗi round"""
        if not self.item_list:
            return
        # Agent: 30% chance nhận 1 item
        if random.random() < 0.3:
            item_obj = self.game.item_registry.get(random.choice(self.item_list))
            if item_obj:
                self.agent.add_item_to_bench(item_obj)

        # BossBot: nhận item mỗi 3 rounds
        boss = next((p for p in self.game.players if p.name == "BossBot"), None)
        if boss and boss.is_alive and self.game.round_count % 3 == 0:
            self._give_items_to_player(boss, 1)

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
                # Đếm số tướng cùng tên trước khi thêm
                before_count = sum(
                    1 for c in self.agent.get_all_champions() if c.name == name
                )
                added = self.agent.add_to_bench(champ)
                if not added:
                    econ.gold += cost
                    self.game.pool.return_champ(name)
                else:
                    # Reward trực tiếp khi mua tướng thành công
                    reward += 0.5

                    # Kiểm tra có ghép được 2-star hoặc 3-star không
                    after_count = sum(
                        1 for c in self.agent.get_all_champions() if c.name == name
                    )
                    # Nếu tổng đạt 3 → ghép thành 2-star
                    if before_count == 2 and after_count >= 3:
                        reward += 3.0   # +3 khi ghép được 2-star
                    # Nếu tổng đạt 9 → ghép thành 3-star (3 con 2-star)
                    elif before_count == 8 and after_count >= 9:
                        reward += 10.0  # +10 khi ghép được 3-star
        elif action == ACTION_REROLL: econ.reroll()
        elif action == ACTION_BUY_XP:
            before_level = econ.level
            econ.buy_xp()
            after_level  = econ.level
            if after_level > before_level:
                # Thưởng level up đúng lúc theo stage
                stage = self.game.stage
                if after_level == 4 and stage == 2:
                    reward += 1.0
                elif after_level in (5, 6) and stage == 3:
                    reward += 1.5
                elif after_level in (7, 8) and stage == 4:
                    reward += 2.0
                elif after_level == 9:
                    reward += 10.0  # Rất khó lên
                elif after_level == 10:
                    reward += 20.0  # Cực khó lên
        elif action in ACTION_SELL_BENCH:
            idx = action - 7
            champ = self.agent.bench[idx]
            if champ:
                # Trả lại đồ về bench khi bán tướng ♻️
                for item in champ.items[:]:
                    self.agent.add_item_to_bench(item)
                    item.unequip(champ)
                self.agent.sell(champ)
        elif ACTION_PLACE_BASE <= action < ACTION_PASS:
            rel = action - ACTION_PLACE_BASE
            b_idx, bd_idx = rel // N_BOARD_SLOTS, rel % N_BOARD_SLOTS
            row, col = BOARD_POSITIONS[bd_idx]
            self.agent.place_on_board(b_idx, row, col)
        elif ACTION_EQUIP_ITEM_BASE <= action < ACTION_CAROUSEL_BASE:
            rel = action - ACTION_EQUIP_ITEM_BASE
            item_idx, board_idx = rel // N_BOARD_SLOTS, rel % N_BOARD_SLOTS
            r, c = BOARD_POSITIONS[board_idx]
            target = self.agent.board.get(r, c)
            if target and self.agent.equip_item_to_champ(item_idx, target):
                reward += 0.5

        elif ACTION_CAROUSEL_BASE <= action < ACTION_AUGMENT_BASE:
            slot_idx = action - ACTION_CAROUSEL_BASE
            if self.game.pick_carousel(self.agent, slot_idx):
                reward += 2.0   # Thưởng khi chọn được tướng carousel

        elif ACTION_AUGMENT_BASE <= action < TOTAL_ACTIONS:
            aug_idx = action - ACTION_AUGMENT_BASE
            if self.game.pick_augment(self.agent, aug_idx):
                reward += 3.0   # Thưởng khi chọn augment 

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
            self._apply_loot()          # Rơi đồ 📦
            self._apply_bot_stage_buff()  # Buff bot theo stage
            self._apply_adaptive_buff()   # Buff bot khi agent quá mạnh

            # Track TRƯỚC simulate — số bot còn sống
            alive_before_round = sum(
                1 for p in self.game.players
                if p is not self.agent and p.is_alive
            )

            hp_before = self.agent.hp
            results   = self.game.simulate_round(verbose=False)
            self._rounds_survived += 1

            hp_delta = self.agent.hp - self._prev_hp
            self._prev_hp = self.agent.hp

            # Nếu agent chết round này → lưu số bot sống TRƯỚC round
            if not self.agent.is_alive:
                self._alive_when_died = alive_before_round

            # Penalty nặng khi thua PvE
            for player, _, result in results:
                if player is self.agent:
                    if result.get("pve") and not result.get("pve_won"):
                        reward -= 35.0   # Phạt nặng khi thua PvE
                    break

            # Logger round end (replay mode)
            if self.logger.mode == 'replay':
                board_names = [c.name for c in self.agent.get_board_champions()]
                standing    = sum(
                    1 for p in self.game.players
                    if p is not self.agent and p.hp > self.agent.hp
                ) + 1
                self.logger.on_round_end(
                    stage       = self.game.stage,
                    round_num   = self.game.round_num,
                    board_champs= board_names,
                    gold        = self.agent.econ.gold,
                    hp          = self.agent.hp,
                    placement   = standing,
                )
            
            stage        = self.game.stage
            board_champs = self.agent.get_board_champions()
            board_size   = len(board_champs)
            board_cap    = self.agent.econ.board_size  # số slot tối đa theo level
            bench_filled = sum(1 for c in self.agent.bench if c is not None)

            # ── A. Phạt giữ gold quá nhiều + thưởng giữ đúng mức ──
            gold_now = self.agent.econ.gold
            if stage <= 3:
                # Early game: thưởng nhỏ khi giữ 50-70 gold (lấy interest tốt)
                if 50 <= gold_now <= 70:
                    reward += 0.3
                elif gold_now > 70:
                    reward -= (gold_now - 70) * 0.005
            elif stage == 4:
                # Mid game: thưởng khi giữ 50-70, phạt nếu nhiều hơn
                if 50 <= gold_now <= 70:
                    reward += 0.2
                elif gold_now > 70:
                    reward -= (gold_now - 70) * 0.02
            elif stage == 5:
                # Late game: thưởng khi giữ 50-70, phạt nặng nếu nhiều hơn
                if 50 <= gold_now <= 70:
                    reward += 0.1
                elif gold_now > 70:
                    reward -= (gold_now - 70) * 0.04
                elif gold_now > 50:
                    reward -= (gold_now - 50) * 0.02
            else:
                # Stage 6+: phạt rất nặng nếu > 50
                if 40 <= gold_now <= 60:
                    reward += 0.1
                elif gold_now > 60:
                    reward -= (gold_now - 60) * 0.06

            # ── B. Phạt board trống/thiếu — RẤT NẶNG ─────────
            if board_size == 0:
                # Phạt nặng từ stage 2, tăng dần
                empty_board_penalty = {
                    2: -8.0,
                    3: -12.0,
                    4: -20.0,
                    5: -30.0,
                }.get(stage, -40.0)   # Stage 6+ phạt -40/round
                reward += empty_board_penalty
            else:
                # Phạt khi chưa dùng hết board_cap — tăng theo stage
                empty_slots = board_cap - board_size
                if empty_slots > 0:
                    slot_penalty = 0.3 if stage >= 4 else 0.2
                    reward -= empty_slots * slot_penalty

            # ── C. Phạt bench có tướng mà không đặt lên board ─
            if bench_filled > 0 and board_size < board_cap:
                reward -= bench_filled * 0.5   # tăng từ 0.15 lên 0.5

            # ── D. Reward interest CHỈ khi board có tướng ─────
            # Giải pháp 3: không thưởng interest nếu board trống
            if board_size > 0:
                interest = min(gold_now // 10, 5)
                reward  += 0.02 * interest

            # ── E. Reward board quality (tướng đắt) ──────────
            for champ in board_champs:
                cost = get_cost(self.champion_data, champ.name)
                if cost == 4:
                    reward += 0.15
                elif cost == 5:
                    reward += 0.3

            # ── F. Reward win streak ──────────────────────────
            win_streak = self.agent.econ.win_streak
            if win_streak >= 2:
                reward += win_streak * 0.2

            # Phạt khi thắng walkover — đối thủ không có tướng
            if hp_delta == 0 and self.game.is_pvp():
                # Kiểm tra xem đối thủ round này có tướng không
                # Nếu thắng nhờ đối thủ board trống thì không tính là thắng thật
                any_real_fight = False
                for pa, pb, res in results:
                    if pa is self.agent or pb is self.agent:
                        opp = pb if pa is self.agent else pa
                        if opp and len(opp.get_board_champions()) > 0:
                            any_real_fight = True
                        break
                if not any_real_fight:
                    reward -= 3.0  # Phạt thắng walkover

            if hp_delta < 0:
                reward += hp_delta * (0.3 * self.game.stage)
                reward -= 5.0 
            
            if not self.agent.is_alive or self.game.is_game_over():
                terminated = True

                # Tính rank chính xác
                if self.agent.is_alive:
                    rank = sum(
                        1 for p in self.game.players
                        if p is not self.agent and p.hp > self.agent.hp
                    )
                else:
                    # Agent chết: rank = số bot sống TRƯỚC round agent chết
                    rank = getattr(self, '_alive_when_died', None)
                    if rank is None:
                        rank = sum(1 for p in self.game.players
                                   if p is not self.agent and p.is_alive)
                rank = max(0, min(rank, len(self.game.players) - 1))
                base_reward = {0:100, 1:60, 2:20, 3:10, 4:-10, 5:-25, 6:-40, 7:-80}.get(rank, -80)
                # Giảm reward nếu agent thắng với board trống (exploit)
                if rank == 0 and board_size == 0:
                    base_reward = -20.0  # Phạt thắng kiểu exploit
                reward += base_reward

                # Lưu placement
                self._episode_placements.append(rank + 1)   # rank 0 = top1

                # Logger episode end
                self.logger.on_episode_end(
                    placement      = rank + 1,
                    total_reward   = self._total_reward,
                    rounds_survived= self._rounds_survived,
                    final_hp       = self.agent.hp,
                )

                # In stats mỗi _print_every game
                if self._episode_count % self._print_every == 0:
                    self._print_stats()
            else:
                self.agent.econ.shop.roll(self.agent.level)

        # Track reward
        self._episode_rewards.append(float(reward))
        self._total_reward += float(reward)

        return self._get_obs_dict(), float(reward), terminated, truncated, {}

    def _print_stats(self):
        """In thống kê sau mỗi _print_every episodes"""
        n = self._print_every
        recent = self._episode_placements[-n:]
        if not recent:
            return

        top1  = recent.count(1)
        top4  = sum(1 for p in recent if p <= 4)
        avg_p = sum(recent) / len(recent)

        # Đếm placement phân bố
        dist = {i: recent.count(i) for i in range(1, 9)}
        dist_str = " | ".join(f"Top{k}:{v}" for k, v in dist.items() if v > 0)

        sep = "=" * 55
        print(f"\n{sep}")
        print(f"[Stats] Sau {self._episode_count} games (last {n}):")
        print(f"  Top1  : {top1}/{n} ({top1/n*100:.1f}%)")
        print(f"  Top4  : {top4}/{n} ({top4/n*100:.1f}%)")
        print(f"  Avg placement: {avg_p:.2f}")
        print(f"  Phan bo: {dist_str}")
        print(f"{sep}\n")

    def _run_bot_logic(self):
        """Chạy logic cho tất cả bots trừ agent"""
        for player in self.game.players[1:]:
            if not player.is_alive:
                continue
            if player.name == "EconBot":
                self._run_econ_bot(player)
            elif player.name == "RerollBot":
                self._run_reroll_bot(player)
            elif player.name in ("ModelBot", "ModelBot2", "ModelBot3", "ModelBot4"):
                self._run_model_bot(player)
            elif player.name == "BossBot":
                self._run_boss_bot(player)
            else:
                self._run_random_bot(player)

    def _buy_and_place(self, player, slot_idx, name):
        """Helper: mua tướng và đặt lên board/bench"""
        cost  = get_cost(self.champion_data, name)
        bought = player.econ.buy_champion(slot_idx, cost)
        if bought:
            champ = self.game.make_champion(name)
            if not player.add_to_board_auto(champ):
                player.add_to_bench(champ)

    def _run_econ_bot(self, player):
        """EconBot: giữ gold để lấy interest, level up đúng lúc, mua tướng đang có"""
        econ        = player.econ
        owned_names = {c.name for c in player.get_all_champions()}

        # Level up khi đủ điều kiện tốt
        if econ.gold >= 30 and econ.level < 8:
            econ.buy_xp()

        # Chỉ mua tướng đang sở hữu (ghép 3-star)
        for i, name in enumerate(econ.shop.slots):
            if name and name in owned_names:
                cost = get_cost(self.champion_data, name)
                if econ.gold - cost >= 10:   # giữ ít nhất 10 gold
                    self._buy_and_place(player, i, name)

    def _run_reroll_bot(self, player):
        """RerollBot: reroll liên tục để ghép 3-star, không quan tâm giữ gold"""
        econ        = player.econ
        owned_names = {c.name for c in player.get_all_champions()}

        # Mua tướng đang sở hữu trước
        for i, name in enumerate(econ.shop.slots):
            if name and name in owned_names and econ.gold >= get_cost(self.champion_data, name):
                self._buy_and_place(player, i, name)

        # Reroll nếu còn gold
        if econ.gold >= 6:
            econ.reroll()
            # Mua lại sau reroll
            owned_names = {c.name for c in player.get_all_champions()}
            for i, name in enumerate(econ.shop.slots):
                if name and name in owned_names and econ.gold >= get_cost(self.champion_data, name):
                    self._buy_and_place(player, i, name)

    def _run_model_bot(self, player):
        """ModelBot: dùng checkpoint để quyết định — chỉ planning phase"""
        if self._model_bot is None:
            self._run_econ_bot(player)   # fallback nếu chưa có model
            return

        # Build obs cho player này
        obs_dict = self._get_obs_for_player(player)

        # Predict action
        action, _ = self._model_bot.predict(obs_dict, deterministic=False)
        action     = int(action)

        # Validate mask trước khi execute
        mask = obs_dict["action_mask"]
        if mask[action] == 0:
            return   # Invalid action → bỏ qua

        # Execute action cho player (không phải agent)
        self._apply_action_for_player(player, action)

    def _run_boss_bot(self, player):
        """BossBot: economy tốt nhất + mua tướng mạnh + equip items"""
        econ        = player.econ
        owned_names = {c.name for c in player.get_all_champions()}

        # Luôn level up nếu đủ gold
        if econ.gold >= 8 and econ.level < 9:
            econ.buy_xp()

        # Reroll miễn phí mỗi round
        econ.shop.roll(econ.level)

        # Mua tất cả tướng cost cao hoặc đang sở hữu
        for i, name in enumerate(econ.shop.slots):
            if not name:
                continue
            cost = get_cost(self.champion_data, name)
            if econ.gold >= cost and (cost >= 3 or name in owned_names):
                self._buy_and_place(player, i, name)

        # Equip items lên tướng trên board
        board_champs = player.get_board_champions()
        for champ in board_champs:
            if len(champ.items) < 3 and player.item_bench:
                player.equip_item_to_champ(0, champ)

    def _run_random_bot(self, player):
        """RandomBot: mua tướng ngẫu nhiên nếu đủ gold"""
        econ = player.econ
        for i, name in enumerate(econ.shop.slots):
            if name:
                cost = get_cost(self.champion_data, name)
                if econ.gold >= cost and random.random() < 0.4:
                    self._buy_and_place(player, i, name)

    def _apply_action_for_player(self, player, action):
        """Execute action cho player bất kỳ (dùng cho ModelBot)"""
        econ = player.econ

        if action in ACTION_BUY_SHOP:
            name = econ.shop.slots[action]
            if name:
                cost = get_cost(self.champion_data, name)
                if econ.buy_champion(action, cost):
                    champ = self.game.make_champion(name)
                    if not player.add_to_bench(champ):
                        econ.gold += cost
                        self.game.pool.return_champ(name)

        elif action == ACTION_REROLL:
            econ.reroll()

        elif action == ACTION_BUY_XP:
            econ.buy_xp()

        elif action in ACTION_SELL_BENCH:
            idx   = action - 7
            champ = player.bench[idx]
            if champ:
                player.sell(champ)

        elif ACTION_PLACE_BASE <= action < ACTION_PASS:
            rel   = action - ACTION_PLACE_BASE
            b_idx = rel // N_BOARD_SLOTS
            bd_idx= rel % N_BOARD_SLOTS
            row, col = BOARD_POSITIONS[bd_idx]
            player.place_on_board(b_idx, row, col)

        elif ACTION_EQUIP_ITEM_BASE <= action < TOTAL_ACTIONS:
            rel       = action - ACTION_EQUIP_ITEM_BASE
            item_idx  = rel // N_BOARD_SLOTS
            board_idx = rel % N_BOARD_SLOTS
            r, c      = BOARD_POSITIONS[board_idx]
            target    = player.board.get(r, c)
            if target:
                player.equip_item_to_champ(item_idx, target)

    def _get_obs_dict(self):
        """Trả về dict obs cho agent — dùng cho training"""
        return {
            "action_mask": self.get_action_mask(),
            "observation": self._get_obs(self.agent)
        }

    def _get_obs_for_player(self, player):
        """Build obs dict cho bất kỳ player nào — dùng cho ModelBot"""
        mask = self._get_action_mask_for(player)
        obs  = self._get_obs(player)
        return {"action_mask": mask, "observation": obs}

    def _get_action_mask_for(self, player):
        """Build action mask cho player bất kỳ (không chỉ agent)"""
        mask = np.zeros(TOTAL_ACTIONS, dtype=np.int8)
        # Safety check
        if player is None or not player.is_alive:
            mask[ACTION_PASS] = 1
            return mask
        econ = player.econ

        for i in range(N_SHOP_SLOTS):
            name = econ.shop.slots[i]
            if name and econ.gold >= get_cost(self.champion_data, name) and None in player.bench:
                mask[i] = 1

        if econ.gold >= 2: mask[ACTION_REROLL] = 1
        if econ.gold >= 4 and econ.level < 10: mask[ACTION_BUY_XP] = 1

        for i in range(N_BENCH_SLOTS):
            if player.bench[i] is not None:
                mask[7 + i] = 1

        board_count = len(player.get_board_champions())
        can_place   = board_count < player.econ.board_size
        for b_idx in range(N_BENCH_SLOTS):
            if player.bench[b_idx] is not None and can_place:
                for bd_idx in range(N_BOARD_SLOTS):
                    r, c = BOARD_POSITIONS[bd_idx]
                    if player.board.is_empty(r, c):
                        action_id = ACTION_PLACE_BASE + (b_idx * N_BOARD_SLOTS) + bd_idx
                        mask[action_id] = 1

        for i_idx in range(min(len(player.item_bench), N_ITEM_SLOTS)):
            for b_idx in range(N_BOARD_SLOTS):
                r, c = BOARD_POSITIONS[b_idx]
                champ = player.board.get(r, c)
                if champ and len(champ.items) < 3:
                    action_id = ACTION_EQUIP_ITEM_BASE + (i_idx * N_BOARD_SLOTS) + b_idx
                    mask[action_id] = 1

        mask[ACTION_PASS] = 1

        # Carousel actions — chỉ available trong carousel round
        if self.game.get_round_type() == 'carousel':
            for i, slot in enumerate(self.game._carousel_slots):
                if slot is not None:
                    mask[ACTION_CAROUSEL_BASE + i] = 1

        # Augment actions — chỉ available trong augment round
        if self.game.get_round_type() == 'augment':
            offers = self.game._augment_offers.get(player.name, [])
            for i in range(min(len(offers), N_AUGMENT_OPTIONS)):
                mask[ACTION_AUGMENT_BASE + i] = 1

        # Final safety
        if mask.sum() == 0:
            mask[ACTION_PASS] = 1
        return mask

    def _get_obs(self, player):
        """Build observation vector cho player bất kỳ"""
        obs  = np.zeros(OBS_SIZE, dtype=np.float32)
        econ = player.econ
        idx  = 0

        # Stats & Econ (6)
        obs[idx:idx+6] = [
            econ.gold/50, player.hp/100, econ.level/10,
            econ.xp/68, econ.win_streak/6, econ.loss_streak/6
        ]
        idx += 6

        # Shop (10)
        for slot in econ.shop.slots:
            if slot:
                obs[idx]   = self.champ_id_map.get(slot, 0) / self.n_champions
                obs[idx+1] = get_cost(self.champion_data, slot) / 5.0
            idx += 2

        # Bench (18)
        for champ in player.bench:
            if champ:
                obs[idx]   = self.champ_id_map.get(champ.name, 0) / self.n_champions
                obs[idx+1] = champ.star / 3.0
            idx += 2

        # Board (168)
        role_map = {"tank": 0.2, "fighter": 0.4, "marksman": 0.6, "caster": 0.8, "assassin": 1.0}
        for row, col in BOARD_POSITIONS:
            champ = player.board.get(row, col)
            if champ:
                obs[idx]   = self.champ_id_map.get(champ.name, 0) / self.n_champions
                obs[idx+1] = champ.star / 3.0
                obs[idx+2] = role_map.get(self._get_role(champ.name), 0.1)
                for i in range(3):
                    if i < len(champ.items):
                        obs[idx+3+i] = self.item_id_map.get(champ.items[i].item_id, 0) / (len(self.item_list) + 1)
            idx += 6

        # Item Bench (10)
        for i in range(N_ITEM_SLOTS):
            if i < len(player.item_bench):
                obs[idx] = self.item_id_map.get(player.item_bench[i].item_id, 0) / (len(self.item_list) + 1)
            idx += 1

        # Opponents (7)
        opponents = [p for p in self.game.players if p is not player]
        for opp in opponents[:N_OPPONENTS]:
            obs[idx] = opp.hp / 100.0
            idx += 1

        # Traits (22)
        board_champs = player.get_board_champions()
        trait_counts = self._trait_mgr.count_traits(board_champs)
        for t_name in self.trait_id_list:
            if idx < OBS_SIZE:
                trait = self._trait_mgr.traits.get(t_name)
                if trait:
                    level, _ = trait.get_active_level(trait_counts.get(t_name, 0))
                    obs[idx] = level / 3.0
                idx += 1

        # Carousel slots (18) — champion_id/n + item_id/(n_items+1)
        carousel_slots = getattr(self.game, '_carousel_slots', [])
        for i in range(N_CAROUSEL_SLOTS):
            if idx + 1 < OBS_SIZE:
                if i < len(carousel_slots) and carousel_slots[i]:
                    slot  = carousel_slots[i]
                    champ = slot.get('champion')
                    item  = slot.get('item')
                    if champ:
                        obs[idx]   = self.champ_id_map.get(champ.name, 0) / self.n_champions
                    if item:
                        obs[idx+1] = self.item_id_map.get(item.item_id, 0) / (len(self.item_list) + 1)
            idx += 2

        # Augment offers (6) — augment_idx/n_augments cho 3 slots
        aug_offers = getattr(self.game, '_augment_offers', {}).get(player.name, [])
        aug_pool   = getattr(self.game, '_augment_pool', [])
        n_aug      = max(len(aug_pool), 1)
        for i in range(N_AUGMENT_OPTIONS):
            if idx < OBS_SIZE:
                if i < len(aug_offers):
                    aug_id = aug_offers[i].get('id', '')
                    aug_idx = next((j for j, a in enumerate(aug_pool) if a.get('id') == aug_id), 0)
                    obs[idx] = aug_idx / n_aug
            idx += 1

        return obs