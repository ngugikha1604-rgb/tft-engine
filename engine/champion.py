# champion.py - Class đại diện cho một champion trong TFT Set 16

class Champion:
    def __init__(
        self,
        name,
        cost,
        hp,
        armor,
        mr,
        attack_damage,
        attack_speed,
        range_,
        traits,
        mana_start=0,
        mana_max=100,
        crit_chance=0.25,       # Base crit chance mặc định 25%
        crit_damage=1.4,        # Base crit damage 140%
    ):
        # ==================
        # Thông tin cơ bản
        # ==================
        self.name = name
        self.cost = cost            # Chi phí mua (1-5 gold)
        self.traits = traits        # Danh sách traits, ví dụ ["Mage", "Ionia"]

        # ==================
        # Base stats (không đổi, dùng để tính lại khi lên sao)
        # ==================
        self.base_hp = hp
        self.base_armor = armor
        self.base_mr = mr
        self.base_ad = attack_damage
        self.base_as = attack_speed     # Số lần attack/giây
        self.base_range = range_        # Ô tấn công (1=melee, 2-4=ranged)
        self.base_mana_max = mana_max
        self.base_crit_chance = crit_chance
        self.base_crit_damage = crit_damage

        # ==================
        # Current stats (thay đổi khi có items/buffs/traits)
        # ==================
        self.max_hp = hp
        self.hp = hp
        self.armor = armor
        self.mr = mr
        self.ad = attack_damage
        self.attack_speed = attack_speed
        self.range = range_

        # Mana — mỗi champion có mana_start và mana_max riêng
        self.mana = mana_start
        self.mana_start = mana_start    # Mana bắt đầu mỗi trận
        self.max_mana = mana_max

        # Crit
        self.crit_chance = crit_chance  # 0.0 - 1.0
        self.crit_damage = crit_damage  # 1.4 = 140%

        # Damage modifiers
        self.damage_amp = 0.0           # % khuếch đại damage đầu ra (additive)
        self.damage_reduction = 0.0     # % giảm damage nhận vào (sau giáp/mr)
        self.omnivamp = 0.0             # % heal từ damage dealt

        # Ability Power
        self.ability_power = 100        # Base AP = 100 trong TFT

        # ==================
        # Role (từ Roles Revamped Set 15+)
        # tank | fighter | assassin | marksman | caster | specialist
        # ==================
        self.role = "fighter"   # Default, override khi load từ JSON

        # ==================
        # Star level
        # ==================
        self.star = 1

        # ==================
        # Vị trí trên bàn
        # ==================
        self.position = None            # (row, col) trên hex board

        # ==================
        # Combat state
        # ==================
        self.is_alive = True
        self.attack_timer = 0.0         # Giây đến lần attack tiếp theo
        self.shields = []               # List các shield đang active [{amount, duration}]
        self.buffs = []                 # List buffs đang active
        self.current_target = None      # Target đang attack

    # ==================
    # STAR UPGRADE
    # ==================

    def upgrade_star(self):
        """Lên sao — TFT dùng multiplier 1.8x mỗi sao"""
        if self.star >= 3:
            return
        self.star += 1
        multiplier = 1.8 if self.star == 2 else 1.8 ** 2
        self.max_hp = int(self.base_hp * multiplier)
        self.hp = self.max_hp
        self.ad = int(self.base_ad * multiplier)

    # ==================
    # DAMAGE SYSTEM
    # ==================

    def take_damage(self, raw_damage, damage_type="physical", damage_amp_bonus=0.0):
        """
        Tính và nhận damage theo công thức TFT:
        1. Damage Amp (từ attacker) nhân vào raw damage
        2. Crit (đã tính trước khi gọi hàm này)
        3. Giảm từ armor/mr
        4. Damage Reduction (DR) của target trừ sau cùng
        5. Shield hấp thụ trước HP
        """
        if not self.is_alive:
            return 0.0

        # Bước 1: Áp Damage Amp (tính từ attacker, truyền vào)
        damage = raw_damage * (1 + damage_amp_bonus)

        # Bước 2: Giảm từ giáp/mr
        if damage_type == "physical":
            effective_armor = max(0, self.armor)
            reduction = effective_armor / (effective_armor + 100)
            damage *= (1 - reduction)
        elif damage_type == "magic":
            effective_mr = max(0, self.mr)
            reduction = effective_mr / (effective_mr + 100)
            damage *= (1 - reduction)
        # true damage: không bị giảm bởi giáp/mr

        # Bước 3: Damage Reduction của target (sau giáp/mr)
        damage *= (1 - self.damage_reduction)

        damage = max(0, damage)

        # Bước 4: Shield hấp thụ trước
        remaining = damage
        for shield in self.shields[:]:
            if shield["amount"] >= remaining:
                shield["amount"] -= remaining
                remaining = 0
                break
            else:
                remaining -= shield["amount"]
                self.shields.remove(shield)

        # Bước 5: Trừ HP
        self.hp -= remaining
        if self.hp <= 0:
            self.hp = 0
            self.is_alive = False

        # Mana khi bị đánh (10 mana per hit, tối đa 42.5 theo TFT)
        self.gain_mana(10)

        return damage  # Trả về actual damage để tính omnivamp

    def calc_crit(self, base_damage):
        """Tính damage có crit hay không, trả về (final_damage, is_crit)"""
        import random
        if random.random() < self.crit_chance:
            return base_damage * self.crit_damage, True
        return base_damage, False

    def deal_damage_to(self, target, damage_type="physical"):
        """
        Attack target — tính crit rồi gọi target.take_damage()
        Tự động tính omnivamp heal
        """
        raw = self.ad if damage_type == "physical" else self.ad
        damage_after_crit, is_crit = self.calc_crit(raw)

        actual = target.take_damage(
            damage_after_crit,
            damage_type=damage_type,
            damage_amp_bonus=self.damage_amp
        )

        # Omnivamp heal
        if self.omnivamp > 0 and actual > 0:
            self.heal(actual * self.omnivamp)

        # Mana khi attack
        self.gain_mana(10)

        return actual, is_crit

    # ==================
    # HEALING & MANA
    # ==================

    def heal(self, amount):
        """Hồi máu, không vượt max_hp"""
        self.hp = min(self.max_hp, self.hp + amount)

    def add_shield(self, amount, duration=5.0):
        """Thêm shield"""
        self.shields.append({"amount": amount, "duration": duration})

    def gain_mana(self, amount):
        """Nhận mana"""
        self.mana = min(self.max_mana, self.mana + amount)

    def can_cast(self):
        """Đủ mana để cast chưa"""
        return self.mana >= self.max_mana

    def spend_mana(self):
        """Reset mana sau khi cast"""
        self.mana = 0

    # ==================
    # COMBAT RESET
    # ==================

    def reset_for_combat(self):
        """Reset về trạng thái đầu trận"""
        self.hp = self.max_hp
        self.mana = self.mana_start
        self.is_alive = True
        self.attack_timer = 0.0
        self.shields = []
        self.buffs = []
        self.current_target = None
        self.mana_lock_timer = 0.0
        self.move_cooldown = 0.0

    # ==================
    # STAT MODIFIERS
    # ==================

    def apply_sunder(self, amount):
        """Sunder — giảm armor (tối thiểu 0)"""
        self.armor = max(0, self.armor - amount)

    def apply_shred(self, amount):
        """Shred — giảm MR (tối thiểu 0)"""
        self.mr = max(0, self.mr - amount)

    def __repr__(self):
        stars = "★" * self.star
        return (
            f"{self.name}({stars}) "
            f"HP:{self.hp:.0f}/{self.max_hp} "
            f"AD:{self.ad} AS:{self.attack_speed} "
            f"Armor:{self.armor} MR:{self.mr} "
            f"Mana:{self.mana}/{self.max_mana}"
        )