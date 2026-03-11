# econ.py - Hệ thống kinh tế TFT Set 16
#
# Bao gồm:
#   - Gold income (passive, win bonus, interest, streak)
#   - XP và leveling system
#   - Shop odds theo level
#   - Champion pool (shared giữa tất cả players)
#   - Reroll shop
#   - Mua/bán champion

import random
from collections import defaultdict


# ==================
# HẰNG SỐ SET 16
# ==================

# Base income theo round (stage-round)
BASE_INCOME = {
    (1, 1): 0,
    (1, 2): 2,
    (1, 3): 2,
    (1, 4): 3,
    (2, 1): 4,
}
BASE_INCOME_DEFAULT = 5     # Từ round 2-2 trở đi

# Interest: floor(gold / 10), tối đa 5
MAX_INTEREST = 5

# Win/Loss streak bonus
# streak_count -> bonus gold
STREAK_BONUS = {
    0: 0,
    1: 0,
    2: 1,
    3: 1,
    4: 1,
    5: 2,
}
STREAK_BONUS_MAX = 3        # 6+ streak = +3 gold

# Thưởng thêm khi thắng PvP (không cộng vào streak, chỉ tính khi thắng)
WIN_BONUS = 1

# XP cần để lên từ level X lên X+1
# Index 0 = lv1->lv2, 1 = lv2->lv3, ...
XP_TO_LEVEL = [0, 2, 6, 10, 20, 36, 60, 68, 68]
# Tức là: lv2->lv3 cần 2 XP, lv3->lv4 cần 6, ..., lv9->lv10 cần 68

# XP nhận miễn phí mỗi round
XP_PER_ROUND = 2

# Chi phí mua XP thủ công (4 gold = 4 XP)
XP_BUY_COST  = 4
XP_BUY_GAIN  = 4

# Level tối đa
MAX_LEVEL = 10

# Số slot tướng trên board theo level
BOARD_SIZE_BY_LEVEL = {
    1: 1, 2: 2, 3: 3, 4: 4, 5: 5,
    6: 6, 7: 7, 8: 8, 9: 9, 10: 10,
}

# Shop odds (%) theo level — [1cost, 2cost, 3cost, 4cost, 5cost]
# Nguồn: Set 16 / lolchess.gg / metatft
SHOP_ODDS = {
    1:  [100,  0,   0,   0,   0],
    2:  [100,  0,   0,   0,   0],
    3:  [ 75, 25,   0,   0,   0],
    4:  [ 55, 30,  15,   0,   0],
    5:  [ 45, 33,  20,   2,   0],
    6:  [ 25, 40,  30,   5,   0],
    7:  [ 19, 30,  35,  15,   1],
    8:  [ 15, 20,  35,  25,   5],
    9:  [ 10, 15,  30,  30,  15],
    10: [  5, 10,  20,  40,  25],
}

# Pool size (số bản copy mỗi champion theo cost tier)
POOL_SIZE = {
    1: 30,
    2: 25,
    3: 18,
    4: 10,
    5:  9,
}

# Số champion unique mỗi cost (Set 16 có 100 champions)
# Tạm dùng số ước tính theo tỉ lệ chuẩn
CHAMPION_COUNT_BY_COST = {
    1: 13,
    2: 13,
    3: 13,
    4: 12,
    5:  8,
}

# Giá bán tướng = cost gốc (1-sao), star không ảnh hưởng giá bán
SELL_VALUE = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}


# ==================
# CHAMPION POOL (Shared)
# ==================

class ChampionPool:
    """
    Pool champion dùng chung cho tất cả player trong game.
    Mỗi champion có pool_size bản copy.
    Khi player mua → giảm pool. Khi bán/chết → trả lại pool.
    """

    def __init__(self, champion_data):
        """
        champion_data: dict { champ_name: cost }  HOẶC  { champ_name: {cost: X, ...} }
        Tự động detect format và convert.
        """
        self._pool = {}
        self._cost = {}     # name -> cost (int)

        for name, val in champion_data.items():
            if name.startswith("_"):
                continue
            cost = val["cost"] if isinstance(val, dict) else int(val)
            self._pool[name] = POOL_SIZE.get(cost, 0)
            self._cost[name] = cost

    def available(self, name):
        return self._pool.get(name, 0)

    def draw(self, name, count=1):
        """Lấy count bản của champion ra khỏi pool"""
        avail = self._pool.get(name, 0)
        actual = min(avail, count)
        self._pool[name] = avail - actual
        return actual

    def return_champ(self, name, count=1):
        """Trả champion về pool (bán hoặc chết)"""
        if name in self._pool:
            self._pool[name] += count

    def get_all_by_cost(self, cost):
        """Lấy tất cả tên champion của 1 cost tier còn trong pool"""
        return [name for name, c in self._cost.items()
                if c == cost and self._pool.get(name, 0) > 0]

    def __repr__(self):
        return f"ChampionPool({len(self._pool)} champions)"


# ==================
# SHOP
# ==================

class Shop:
    """
    Shop 5 slot của 1 player.
    Roll để lấy champion mới từ pool.
    """

    SLOTS = 5
    REROLL_COST = 2

    def __init__(self, pool):
        self.pool    = pool
        self.slots   = [None] * self.SLOTS  # Mỗi slot là tên champion hoặc None
        self.locked  = False                # Khi lock shop, không refresh cuối round

    def roll(self, level):
        """
        Reroll toàn bộ shop theo level odds.
        Trả lại champion cũ vào pool trước.
        """
        # Trả slot cũ về pool
        for name in self.slots:
            if name is not None:
                self.pool.return_champ(name)

        # Roll shop mới
        self.slots = [self._draw_one(level) for _ in range(self.SLOTS)]

    def _draw_one(self, level):
        """
        Chọn 1 champion theo shop odds.
        1. Roll cost tier theo % odds
        2. Random champion trong cost tier còn trong pool
        """
        odds = SHOP_ODDS.get(level, SHOP_ODDS[9])

        # Roll cost tier
        roll = random.uniform(0, 100)
        cumulative = 0
        cost_tier = 1
        for cost, pct in enumerate(odds, start=1):
            cumulative += pct
            if roll <= cumulative:
                cost_tier = cost
                break

        # Lấy champions trong tier đó còn trong pool
        available = self.pool.get_all_by_cost(cost_tier)
        if not available:
            # Nếu tier đó hết, thử các tier khác
            for fallback in [1, 2, 3, 4, 5]:
                available = self.pool.get_all_by_cost(fallback)
                if available:
                    cost_tier = fallback
                    break

        if not available:
            return None     # Pool hoàn toàn trống

        name = random.choice(available)
        self.pool.draw(name)
        return name

    def buy(self, slot_index):
        """
        Mua champion ở slot_index.
        Trả về tên champion hoặc None nếu slot trống.
        Champion đã mua KHÔNG trả về pool (trừ khi bán lại).
        """
        if 0 <= slot_index < self.SLOTS and self.slots[slot_index] is not None:
            name = self.slots[slot_index]
            self.slots[slot_index] = None
            return name
        return None

    def remove(self, slot_index):
        """Xóa slot (dùng khi hết round mà shop được refresh)"""
        if 0 <= slot_index < self.SLOTS:
            name = self.slots[slot_index]
            if name:
                self.pool.return_champ(name)
            self.slots[slot_index] = None

    def clear(self):
        """Trả tất cả về pool và xóa shop (khi lock=False cuối round)"""
        if not self.locked:
            for i in range(self.SLOTS):
                self.remove(i)

    def __repr__(self):
        return f"Shop({self.slots})"


# ==================
# PLAYER ECONOMY
# ==================

class PlayerEconomy:
    """
    Quản lý kinh tế của 1 player trong game.
    Bao gồm: gold, XP, level, streak, shop.
    """

    def __init__(self, player_name, pool):
        self.name         = player_name

        # Gold
        self.gold         = 0

        # Level & XP
        self.level        = 1
        self.xp           = 0

        # Streak
        self.win_streak   = 0
        self.loss_streak  = 0

        # HP player
        self.hp           = 100

        # Round tracking
        self.current_stage = 1
        self.current_round = 1

        # Shop
        self.shop         = Shop(pool)

        # Bench (9 slots) — lưu tên champion
        self.bench        = [None] * 9

    # ==================
    # INCOME
    # ==================

    def get_base_income(self):
        """Gold income cơ bản theo round"""
        key = (self.current_stage, self.current_round)
        return BASE_INCOME.get(key, BASE_INCOME_DEFAULT)

    def get_interest(self):
        """Interest = floor(gold / 10), tối đa 5"""
        return min(self.gold // 10, MAX_INTEREST)

    def get_streak_bonus(self):
        """Streak bonus dựa vào streak dài hơn (win hoặc loss)"""
        streak = max(self.win_streak, self.loss_streak)
        if streak >= 6:
            return STREAK_BONUS_MAX
        return STREAK_BONUS.get(streak, 0)

    def collect_income(self, won_pvp=None):
        """
        Thu gold cuối round:
        base + interest + streak + win_bonus (nếu thắng PvP)
        won_pvp=True/False/None (None = PvE round)
        """
        earned = self.get_base_income()
        earned += self.get_interest()
        earned += self.get_streak_bonus()

        if won_pvp is True:
            earned += WIN_BONUS

        self.gold += earned
        return earned

    # ==================
    # STREAK
    # ==================

    def update_streak(self, won, is_pvp=True):
        """
        Cập nhật streak sau mỗi round PvP.
        PvE rounds không reset streak (Set 16 behavior).
        """
        if not is_pvp:
            return  # PvE không ảnh hưởng streak

        if won:
            self.win_streak  += 1
            self.loss_streak  = 0
        else:
            self.loss_streak += 1
            self.win_streak   = 0

    # ==================
    # XP & LEVELING
    # ==================

    def get_xp_needed(self):
        """XP còn cần để lên level tiếp theo"""
        if self.level >= MAX_LEVEL:
            return 0
        idx = self.level - 1    # lv1->lv2 là index 0
        total_needed = XP_TO_LEVEL[idx] if idx < len(XP_TO_LEVEL) else 999
        return max(0, total_needed - self.xp)

    def gain_xp(self, amount):
        """Nhận XP và tự động level up nếu đủ"""
        leveled_up = []
        self.xp += amount

        while self.level < MAX_LEVEL:
            idx = self.level - 1
            needed = XP_TO_LEVEL[idx] if idx < len(XP_TO_LEVEL) else 999
            if self.xp >= needed:
                self.xp    -= needed
                self.level += 1
                leveled_up.append(self.level)
            else:
                break

        return leveled_up   # Trả về danh sách các level đã đạt được

    def collect_round_xp(self):
        """Thu XP tự nhiên cuối round (+2 mỗi round)"""
        return self.gain_xp(XP_PER_ROUND)

    def buy_xp(self):
        """
        Mua XP thủ công: 4 gold = 4 XP.
        Trả về True nếu thành công, False nếu không đủ gold.
        """
        if self.gold < XP_BUY_COST:
            return False
        if self.level >= MAX_LEVEL:
            return False
        self.gold -= XP_BUY_COST
        self.gain_xp(XP_BUY_GAIN)
        return True

    @property
    def board_size(self):
        """Số slot tướng tối đa trên board theo level hiện tại"""
        return BOARD_SIZE_BY_LEVEL.get(self.level, self.level)

    # ==================
    # SHOP ACTIONS
    # ==================

    def reroll(self):
        """
        Reroll shop: tốn 2 gold.
        Trả về True nếu thành công.
        """
        if self.gold < Shop.REROLL_COST:
            return False
        self.gold -= Shop.REROLL_COST
        self.shop.roll(self.level)
        return True

    def buy_champion(self, slot_index, champion_cost):
        """
        Mua champion ở slot_index với giá champion_cost.
        Trả về tên champion hoặc None nếu không đủ gold / slot trống.
        """
        if self.gold < champion_cost:
            return None
        name = self.shop.buy(slot_index)
        if name:
            self.gold -= champion_cost
            return name
        return None

    def sell_champion(self, champ_name, champ_cost, champ_star, pool):
        """
        Bán champion: nhận gold = cost × star_multiplier
        1-star: cost × 1
        2-star: cost × 3
        3-star: cost × 9
        Trả champion về pool.
        """
        STAR_SELL = {1: 1, 2: 3, 3: 9}
        sell_gold = champ_cost * STAR_SELL.get(champ_star, 1)
        self.gold += sell_gold

        # Trả bản copies về pool theo star (2★ = 3 bản, 3★ = 9 bản)
        copies = STAR_SELL.get(champ_star, 1)
        pool.return_champ(champ_name, copies)

        return sell_gold

    # ==================
    # END OF ROUND
    # ==================

    def end_of_round(self, won_pvp=None, is_pvp=True):
        """
        Gọi cuối mỗi round:
        1. Cập nhật streak
        2. Thu XP tự nhiên
        3. Thu gold (base + interest + streak + win bonus)
        4. Refresh shop (nếu không lock)
        """
        self.update_streak(won_pvp, is_pvp)
        self.collect_round_xp()
        earned = self.collect_income(won_pvp)

        # Refresh shop cuối round nếu không lock
        if not self.shop.locked:
            self.shop.clear()
            self.shop.roll(self.level)

        return earned

    # ==================
    # PLAYER DAMAGE
    # ==================

    def take_player_damage(self, stage, surviving_enemies):
        """
        Nhận player damage khi thua PvP.
        Công thức: stage_damage + số tướng địch còn sống
        Stage damage (Set 16):
          Stage 1: 0, Stage 2: 2, Stage 3: 5, Stage 4: 8,
          Stage 5: 10, Stage 6: 12, Stage 7+: 17
        """
        STAGE_DAMAGE = {1: 0, 2: 2, 3: 5, 4: 8, 5: 10, 6: 12}
        stage_dmg = STAGE_DAMAGE.get(stage, 17)
        total = stage_dmg + surviving_enemies
        self.hp = max(0, self.hp - total)
        return total

    @property
    def is_alive(self):
        return self.hp > 0

    # ==================
    # STATUS
    # ==================

    def status(self):
        needed = self.get_xp_needed()
        total  = XP_TO_LEVEL[self.level - 1] if self.level - 1 < len(XP_TO_LEVEL) else 68
        return (
            f"[{self.name}] "
            f"HP:{self.hp} | "
            f"Gold:{self.gold} (interest:{self.get_interest()}) | "
            f"Lv{self.level} ({self.xp}/{total} XP) | "
            f"Streak: W{self.win_streak}/L{self.loss_streak}"
        )

    def __repr__(self):
        return self.status()