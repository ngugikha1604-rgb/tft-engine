# champion.py - Class Champion đầy đủ và chuẩn hóa cho TFT Set 16 🏆

import random

class Champion:
    def __init__(
        self, name, cost, hp, armor, mr, attack_damage, attack_speed, range_, traits,
        mana_start=0, mana_max=100, crit_chance=0.25, crit_damage=1.4
    ):
        # Thông tin cơ bản 📄
        self.name = name
        self.cost = cost
        self.traits = traits

        # Base stats gốc (Dùng làm mốc để nhân hệ số khi nâng sao) 🧬
        self.base_hp = hp
        self.base_armor = armor
        self.base_mr = mr
        self.base_ad = attack_damage
        self.base_as = attack_speed
        self.base_range = range_
        self.base_mana_max = mana_max

        # Chỉ số hiện tại (Sau khi tính sao và trang bị) ⚔️
        self.star = 1
        self.hp = hp
        self.max_hp = hp
        self.armor = armor
        self.mr = mr
        self.ad = attack_damage
        self.attack_speed = attack_speed
        self.range = range_
        self.mana = mana_start
        self.mana_start = mana_start
        self.max_mana = mana_max
        self.crit_chance = crit_chance
        self.crit_damage = crit_damage
        self.ability_power = 100 
        self.damage_amp = 0.0
        self.damage_reduction = 0.0
        self.omnivamp = 0.0

        # Trạng thái chiến đấu 🛡️
        self.is_alive = True
        self.items = []                 # Khởi tạo danh sách đồ rỗng
        self.shields = []               # Lưu trữ: [{"amount": float, "duration": float}]
        self.buffs = []
        self.attack_timer = 0.0
        self.mana_lock_timer = 0.0
        self.last_damage_time = -999.0  # Quan trọng cho các trang bị hồi phục như Warmog
        self.current_target = None
        self.position = None

    # ==================
    # CORE UPDATE & ITEM LOGIC
    # ==================

    def update(self, delta_time, tick_count):
        """Cập nhật trạng thái Champion mỗi tick của trận đấu ⏱️"""
        if not self.is_alive:
            return

        # 1. Cập nhật thời gian lá chắn
        for s in self.shields:
            s["duration"] -= delta_time
        self.shields = [s for s in self.shields if s["duration"] > 0]

        # 2. Tạo context cho trang bị
        ctx = {
            "time": tick_count * delta_time,
            "tick": tick_count,
            "hp_percent": self.hp / self.max_hp if self.max_hp > 0 else 0
        }

        # 3. Kích hoạt Passive Items (Huyết Kiếm, Giáp Máu...) 💎
        for item in self.items:
            if hasattr(item, 'ability') and item.ability and item.ability.trigger_type == "passive":
                item.ability.trigger(self, ctx)

        # 4. Giảm các bộ đếm thời gian
        if self.attack_timer > 0:
            self.attack_timer -= delta_time
        if self.mana_lock_timer > 0:
            self.mana_lock_timer -= delta_time

    # ==================
    # CALCULATIONS & DAMAGE 🔢
    # ==================

    def calc_crit(self):
        """Tính toán xem đòn đánh có chí mạng hay không 🎯"""
        is_crit = random.random() < self.crit_chance
        multiplier = self.crit_damage if is_crit else 1.0
        return is_crit, multiplier

    def take_damage(self, amount, damage_type="physical", attacker=None, damage_amp_bonus=0.0):
        """Nhận sát thương, ưu tiên trừ vào lá chắn 🛡️"""
        if not self.is_alive or amount <= 0:
            return
            
        # Tính toán giảm trừ (Armor/MR)
        res = self.armor if damage_type == "physical" else self.mr
        reduction = res / (res + 100)
        actual_damage = amount * (1 + damage_amp_bonus) * (1 - reduction) * (1 - self.damage_reduction)
        actual_damage = max(0, actual_damage)

        remaining_damage = actual_damage
        
        # Trừ vào lá chắn trước
        while remaining_damage > 0 and self.shields:
            current_shield = self.shields[0]
            if current_shield["amount"] > remaining_damage:
                current_shield["amount"] -= remaining_damage
                remaining_damage = 0
            else:
                remaining_damage -= current_shield["amount"]
                self.shields.pop(0)

        # Trừ vào máu nếu vẫn còn sát thương
        if remaining_damage > 0:
            self.hp -= remaining_damage
            self.last_damage_time = 0 # Ghi nhận vừa bị trúng đòn (dùng logic đơn giản cho simulator)
            
        if self.hp <= 0:
            self.hp = 0
            self.is_alive = False
        
        # Nhận mana khi bị tấn công
        self.gain_mana(10)

    def deal_damage_to(self, target, damage_amount, damage_type="physical"):
        """Gây sát thương lên mục tiêu khác ⚔️"""
        if target and target.is_alive:
            is_crit, multiplier = self.calc_crit()
            final_damage = damage_amount * multiplier
            
            target.take_damage(final_damage, damage_type, attacker=self, damage_amp_bonus=self.damage_amp)
            
            if self.omnivamp > 0:
                self.heal(final_damage * self.omnivamp)
            
            self.gain_mana(10)

    def heal(self, amount):
        """Hồi máu (không vượt quá máu tối đa) 💚"""
        if self.is_alive:
            self.hp = min(self.max_hp, self.hp + amount)

    def add_shield(self, amount, duration):
        """Thêm lá chắn mới (Thống nhất tên hàm với items.py) 🛡️"""
        self.shields.append({"amount": amount, "duration": duration})

    # ==================
    # MANA MANAGEMENT 💧
    # ==================

    def gain_mana(self, amount):
        """Nhận mana (nếu không trong trạng thái mana lock)"""
        if self.mana_lock_timer <= 0:
            self.mana = min(self.max_mana, self.mana + amount)

    def can_cast(self):
        """Kiểm tra xem đã đủ mana để tung chiêu chưa"""
        return self.mana >= self.max_mana

    def spend_mana(self):
        """Reset mana về 0 sau khi dùng chiêu"""
        self.mana = 0

    # ==================
    # PROGRESSION & BUFFS ⭐
    # ==================

    def upgrade_star(self):
        """Nâng sao và cập nhật chỉ số (2 sao x1.8, 3 sao x3.24)"""
        if self.star < 3:
            self.star += 1
            multiplier = 1.8 ** (self.star - 1)
            self.max_hp = int(self.base_hp * multiplier)
            self.hp = self.max_hp
            self.ad = int(self.base_ad * multiplier)

    def apply_sunder(self, percentage):
        """Giảm giáp (Armor) theo % 📉"""
        self.armor = max(0, self.armor * (1 - percentage))

    def apply_shred(self, percentage):
        """Giảm kháng phép (MR) theo % 📉"""
        self.mr = max(0, self.mr * (1 - percentage))

    def equip_item(self, item):
        """Trang bị vật phẩm và cộng chỉ số trực tiếp"""
        if len(self.items) < 3:
            self.items.append(item)
            for stat, bonus in item.stat_bonuses.items():
                if hasattr(self, stat):
                    setattr(self, stat, getattr(self, stat) + bonus)
            return True
        return False

    def __repr__(self):
        return f"<{self.name} {self.star}★ | HP: {int(self.hp)}/{self.max_hp} | MP: {self.mana}/{self.max_mana}>"