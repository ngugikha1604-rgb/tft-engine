# augments.py - Hệ thống Augment TFT Set 16
#
# Augment được offer tại: Stage 2-1, 3-2, 4-2
# Mỗi lần chọn 1 trong 3 augments
# 3 tier: silver, gold, prismatic
#
# Effect types (loại hiệu ứng):
#   Instant (một lần khi nhận):
#     "grant_gold"        - cho gold ngay lập tức
#     "grant_xp"          - cho XP ngay lập tức
#     "grant_rerolls"     - cho lượt reroll miễn phí
#     "grant_item"        - cho trang bị (component hoặc combined)
#     "grant_champion"    - cho champion lên bench
#     "grant_component"   - cho trang bị thành phần ngẫu nhiên
#
#   Passive (tồn tại suốt trận):
#     "team_stat"         - buff chỉ số cho toàn đội (áp dụng đầu mỗi combat)
#     "econ_modifier"     - thay đổi cơ chế kinh tế (interest, income, reroll cost, ...)
#     "per_round_gold"    - cho gold mỗi đầu round
#     "per_round_xp"      - cho XP mỗi đầu round
#
#   Combat trigger (gọi trong combat engine):
#     "on_combat_start"   - đầu mỗi trận đấu
#     "on_combat_end"     - cuối mỗi trận đấu
#     "on_champion_death" - khi 1 champion đồng minh chết
#     "on_kill"           - khi 1 champion đồng minh giết địch
#     "on_player_win"     - khi player thắng round
#     "on_player_loss"    - khi player thua round

import random


# ==================
# AUGMENT OFFER SCHEDULE
# ==================

# Round được offer augment (stage, round)
AUGMENT_ROUNDS = [(2, 1), (3, 2), (4, 2)]

# Số augments được chọn mỗi lần
AUGMENT_CHOICES = 3

# Tier odds theo từng lần offer (Set 16)
# Mỗi entry: list 3 tier cho 3 slot trong armory
# Dạng: [(S/G/P), (S/G/P), (S/G/P)] và % xuất hiện combo đó
# Simplified thành odds cho từng slot
AUGMENT_TIER_ODDS = {
    1: {"silver": 60, "gold": 35, "prismatic": 5},   # Offer 1 (2-1): mostly silver
    2: {"silver": 33, "gold": 50, "prismatic": 17},  # Offer 2 (3-2): mostly gold
    3: {"silver": 15, "gold": 40, "prismatic": 45},  # Offer 3 (4-2): mostly prismatic
}


# ==================
# AUGMENT EFFECT
# ==================

class AugmentEffect:
    """
    Mô tả hiệu ứng của augment.
    Logic được gắn qua handler function khi load từ JSON.
    """

    def __init__(self, effect_type, params=None, handler=None):
        self.effect_type = effect_type      # Xem danh sách ở đầu file
        self.params      = params or {}     # Tham số cho effect (từ JSON)
        self.handler     = handler          # f(player, context) -> None

    def apply(self, player, context=None):
        """
        Apply effect lên player.
        context: dict thông tin phụ (board, allies, enemies, time, ...)
        """
        ctx = context or {}
        ctx["params"] = self.params

        if self.handler:
            self.handler(player, ctx)
        else:
            # Default handler cho các effect type đơn giản
            self._default_apply(player, ctx)

    def _default_apply(self, player, ctx):
        """Handler mặc định cho các effect không cần logic phức tạp"""
        params = self.params

        if self.effect_type == "grant_gold":
            player.gold += params.get("amount", 0)

        elif self.effect_type == "grant_xp":
            player.gain_xp(params.get("amount", 0))

        elif self.effect_type == "grant_rerolls":
            player.free_rerolls = getattr(player, "free_rerolls", 0) + params.get("amount", 0)

        elif self.effect_type == "per_round_gold":
            # Lưu vào player để econ engine đọc mỗi round
            player.per_round_gold_bonus = (
                getattr(player, "per_round_gold_bonus", 0) + params.get("amount", 0)
            )

        elif self.effect_type == "per_round_xp":
            player.per_round_xp_bonus = (
                getattr(player, "per_round_xp_bonus", 0) + params.get("amount", 0)
            )

        elif self.effect_type == "econ_modifier":
            # Ví dụ: giảm giá reroll, tăng interest cap, ...
            stat = params.get("stat")
            value = params.get("value", 0)
            if stat == "reroll_cost_reduction":
                player.reroll_cost_reduction = (
                    getattr(player, "reroll_cost_reduction", 0) + value
                )
            elif stat == "interest_cap_bonus":
                player.interest_cap_bonus = (
                    getattr(player, "interest_cap_bonus", 0) + value
                )
            elif stat == "xp_buy_cost_reduction":
                player.xp_buy_cost_reduction = (
                    getattr(player, "xp_buy_cost_reduction", 0) + value
                )

        elif self.effect_type == "team_stat":
            # Buff chỉ số toàn đội — được apply bởi AugmentManager.apply_team_stats()
            # Không làm gì ở đây, chỉ lưu trong augment.effects
            pass

    def __repr__(self):
        return f"AugmentEffect({self.effect_type}, {self.params})"


# ==================
# AUGMENT
# ==================

class Augment:
    """
    Một augment cụ thể.
    Data load từ augments.json.
    """

    def __init__(self, augment_id, name, tier, effects,
                 description="", tags=None):
        self.augment_id  = augment_id
        self.name        = name
        self.tier        = tier             # "silver" | "gold" | "prismatic"
        self.effects     = effects          # list[AugmentEffect]
        self.description = description
        self.tags        = tags or []       # ["econ", "combat", "items", "trait", ...]

    def apply_instant(self, player, context=None):
        """
        Apply tất cả instant effects khi player chọn augment này.
        Chỉ gọi 1 lần duy nhất khi chọn.
        """
        instant_types = {
            "grant_gold", "grant_xp", "grant_rerolls",
            "grant_item", "grant_champion", "grant_component",
            "per_round_gold", "per_round_xp", "econ_modifier",
        }
        for effect in self.effects:
            if effect.effect_type in instant_types:
                effect.apply(player, context)

    def trigger(self, trigger_type, player, context=None):
        """
        Gọi khi 1 combat event xảy ra.
        CombatSimulator / game loop gọi hàm này.
        """
        for effect in self.effects:
            if effect.effect_type == trigger_type:
                effect.apply(player, context)

    def get_team_stat_bonuses(self):
        """
        Trả về dict các chỉ số buff toàn đội.
        AugmentManager.apply_team_stats() gọi để buff champion trước combat.
        """
        bonuses = {}
        for effect in self.effects:
            if effect.effect_type == "team_stat":
                for stat, val in effect.params.items():
                    bonuses[stat] = bonuses.get(stat, 0) + val
        return bonuses

    def __repr__(self):
        return f"Augment({self.name}, {self.tier})"


# ==================
# AUGMENT REGISTRY
# ==================

class AugmentRegistry:
    """
    Quản lý toàn bộ augments có trong game.
    Load từ augments.json.
    """

    def __init__(self):
        self._augments = {}     # augment_id -> Augment

    def register(self, augment):
        self._augments[augment.augment_id] = augment

    def get(self, augment_id):
        return self._augments.get(augment_id)

    def get_by_tier(self, tier):
        return [a for a in self._augments.values() if a.tier == tier]

    def load_from_data(self, data, effect_handlers=None):
        """
        Load augments từ dict đã parse từ JSON.
        effect_handlers: { augment_id: { effect_type: handler_fn } }

        Format JSON mong đợi:
        {
          "augments": [
            {
              "id": "gold_income_i",
              "name": "Gain Now I",
              "tier": "silver",
              "tags": ["econ"],
              "description": "Gain 8 gold.",
              "effects": [
                { "type": "grant_gold", "params": { "amount": 8 } }
              ]
            },
            {
              "id": "team_ad_i",
              "name": "Aggressive I",
              "tier": "silver",
              "tags": ["combat"],
              "description": "Your team gains 10% Attack Damage.",
              "effects": [
                { "type": "team_stat", "params": { "damage_amp": 0.10 } }
              ]
            }
          ]
        }
        """
        effect_handlers = effect_handlers or {}

        for entry in data.get("augments", []):
            effects = []
            aug_id  = entry["id"]

            for ef_data in entry.get("effects", []):
                ef_type = ef_data.get("type", "")
                handler = effect_handlers.get(aug_id, {}).get(ef_type)
                effect  = AugmentEffect(
                    effect_type = ef_type,
                    params      = ef_data.get("params", {}),
                    handler     = handler,
                )
                effects.append(effect)

            augment = Augment(
                augment_id  = aug_id,
                name        = entry.get("name", aug_id),
                tier        = entry.get("tier", "silver"),
                effects     = effects,
                description = entry.get("description", ""),
                tags        = entry.get("tags", []),
            )
            self.register(augment)

    def __repr__(self):
        counts = {"silver": 0, "gold": 0, "prismatic": 0}
        for a in self._augments.values():
            counts[a.tier] = counts.get(a.tier, 0) + 1
        return f"AugmentRegistry({counts})"


# ==================
# AUGMENT MANAGER (per player)
# ==================

class AugmentManager:
    """
    Quản lý augments của 1 player.
    - Lưu danh sách augments đã chọn
    - Offer armory đúng round
    - Apply team stats trước combat
    - Trigger combat events
    """

    MAX_AUGMENTS = 3    # Tối đa 3 augments mỗi game

    def __init__(self, registry):
        self.registry        = registry
        self.chosen_augments = []       # list[Augment] đã chọn
        self._offer_index    = 0        # Lần offer tiếp theo (0, 1, 2)

    def should_offer(self, stage, round_num):
        """Kiểm tra round này có được offer augment không"""
        return (stage, round_num) in AUGMENT_ROUNDS

    def generate_armory(self, offer_index=None):
        """
        Tạo 3 augments ngẫu nhiên để offer.
        offer_index: 0, 1, 2 (tương ứng 3 lần offer trong game)
        Trả về list[Augment]
        """
        idx    = offer_index if offer_index is not None else self._offer_index
        odds   = AUGMENT_TIER_ODDS.get(idx + 1, AUGMENT_TIER_ODDS[3])
        armory = []

        for _ in range(AUGMENT_CHOICES):
            tier = self._roll_tier(odds)
            pool = self.registry.get_by_tier(tier)

            # Lọc bỏ augment đã có
            chosen_ids = {a.augment_id for a in self.chosen_augments}
            pool = [a for a in pool if a.augment_id not in chosen_ids]

            # Lọc bỏ augment đã có trong armory này
            armory_ids = {a.augment_id for a in armory}
            pool = [a for a in pool if a.augment_id not in armory_ids]

            if pool:
                armory.append(random.choice(pool))
            elif self.registry.get_by_tier(tier):
                # Nếu hết pool (pool rất nhỏ), cho phép trùng
                armory.append(random.choice(self.registry.get_by_tier(tier)))

        return armory

    def _roll_tier(self, odds):
        """Roll tier dựa theo odds dict"""
        roll = random.uniform(0, 100)
        cumulative = 0
        for tier in ["silver", "gold", "prismatic"]:
            cumulative += odds.get(tier, 0)
            if roll <= cumulative:
                return tier
        return "silver"

    def choose(self, augment, player, context=None):
        """
        Player chọn 1 augment từ armory.
        Apply instant effects ngay lập tức.
        """
        if len(self.chosen_augments) >= self.MAX_AUGMENTS:
            raise ValueError("Đã đủ 3 augments")

        self.chosen_augments.append(augment)
        augment.apply_instant(player, context)
        self._offer_index += 1

        return augment

    def apply_team_stats(self, champions):
        """
        Apply tất cả team_stat bonuses lên danh sách champions.
        Gọi trước mỗi combat (sau reset_for_combat).
        """
        combined = {}
        for aug in self.chosen_augments:
            for stat, val in aug.get_team_stat_bonuses().items():
                combined[stat] = combined.get(stat, 0) + val

        for champ in champions:
            for stat, val in combined.items():
                if stat == "damage_amp":
                    champ.damage_amp += val
                elif stat == "damage_reduction":
                    champ.damage_reduction += val
                elif stat == "armor":
                    champ.armor += val
                elif stat == "mr":
                    champ.mr += val
                elif stat == "ad_pct":
                    champ.ad = int(champ.ad * (1 + val))
                elif stat == "ability_power":
                    champ.ability_power += val
                elif stat == "attack_speed":
                    champ.attack_speed += val
                elif stat == "omnivamp":
                    champ.omnivamp += val
                elif stat == "crit_chance":
                    champ.crit_chance += val
                elif stat == "hp_pct":
                    bonus = int(champ.max_hp * val)
                    champ.max_hp += bonus
                    champ.hp     += bonus

    def trigger(self, trigger_type, player, context=None):
        """Trigger tất cả combat event augments"""
        for aug in self.chosen_augments:
            aug.trigger(trigger_type, player, context)

    def collect_round_bonuses(self, player):
        """
        Thu per_round_gold và per_round_xp từ augments.
        Gọi mỗi cuối round (trước end_of_round).
        player có thể là PlayerEconomy hoặc Player (có .econ)
        Trả về (gold_bonus, xp_bonus).
        """
        gold_bonus = getattr(player, "per_round_gold_bonus", 0)
        xp_bonus   = getattr(player, "per_round_xp_bonus", 0)

        # Hỗ trợ cả PlayerEconomy (có .gold) và Player (có .econ.gold)
        econ = getattr(player, "econ", player)
        econ.gold += gold_bonus
        if xp_bonus > 0:
            econ.gain_xp(xp_bonus)

        return gold_bonus, xp_bonus

    def summary(self):
        if not self.chosen_augments:
            return "No augments"
        return " | ".join(f"[{a.tier.upper()[0]}] {a.name}"
                          for a in self.chosen_augments)

    def __repr__(self):
        return f"AugmentManager({self.summary()})"