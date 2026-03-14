# items.py - Hệ thống trang bị TFT hoàn chỉnh 💎

import random

# ==================
# ITEM ABILITY
# ==================

class ItemAbility:
    """
    Mô tả cơ chế đặc biệt của trang bị.
    Logic thực tế nằm trong hàm `trigger()`.
    """
    def __init__(self, name, trigger_type, description, handler=None, cooldown=0.0):
        self.name         = name
        self.trigger_type = trigger_type    # passive, on_attack, on_hit, etc.
        self.description  = description
        self.handler      = handler         # Hàm xử lý: f(owner, context)
        self.cooldown     = cooldown        # Cooldown giữa các lần trigger (giây)
        self._last_trigger = -999.0         # Thời điểm lần trigger gần nhất

    def trigger(self, owner, context):
        """Gọi ability với context chứa thông tin trận đấu"""
        current_time = context.get("time", 0.0)
        if current_time - self._last_trigger < self.cooldown:
            return

        self._last_trigger = current_time
        if self.handler:
            self.handler(owner, context)

    def reset(self):
        """Reset cooldown về đầu trận"""
        self._last_trigger = -999.0

    def __repr__(self):
        return f"ItemAbility({self.name}, trigger={self.trigger_type})"


# ==================
# ITEM CLASS
# ==================

class Item:
    """
    Trang bị TFT. Quản lý việc cộng/trừ chỉ số và kích hoạt kỹ năng.
    """
    def __init__(self, item_id, name, item_type, stat_bonuses=None,
                 ability=None, components=None, description=""):
        self.item_id      = item_id         
        self.name         = name
        self.item_type    = item_type       # "component" | "combined"
        self.stat_bonuses = stat_bonuses or {}
        self.ability      = ability         
        self.components   = components or [] 
        self.description  = description

    def equip(self, champion):
        """Lắp đồ lên tướng"""
        if len(champion.items) >= 3:
            return False

        # 1. Cộng chỉ số trực tiếp
        self._apply_stats(champion, multiply=1)
        # 2. Thêm vào danh sách của tướng
        champion.items.append(self)
        # 3. Reset cooldown
        if self.ability:
            self.ability.reset()
        return True

    def unequip(self, champion):
        """Gỡ trang bị (khi bán tướng)"""
        if self not in champion.items:
            return
        self._apply_stats(champion, multiply=-1)
        champion.items.remove(self)

    def _apply_stats(self, champion, multiply=1):
        """Xử lý cộng/trừ chỉ số (multiply=1 để cộng, -1 để trừ)"""
        for stat, value in self.stat_bonuses.items():
            v = value * multiply
            if hasattr(champion, stat):
                # Các chỉ số đặc biệt cần xử lý logic riêng
                if stat == "hp":
                    champion.max_hp += v
                    champion.hp += v
                elif stat == "mana_max":
                    champion.max_mana += int(v)
                    champion.mana = min(champion.mana, champion.max_mana)
                else:
                    # Các chỉ số thông thường (ad, armor, mr, attack_speed...)
                    setattr(champion, stat, getattr(champion, stat) + v)

    def __repr__(self):
        return f"Item({self.name}, {self.item_type})"


# ==================
# ABILITY HANDLERS (Logic chi tiết - Đã sửa lỗi khớp với champion.py)
# ==================

def handler_warmogs(owner, ctx):
    """Warmog: Hồi 0.25% máu mỗi tick nếu không nhận sát thương 3s"""
    time = ctx.get("time", 0)
    if time - getattr(owner, "last_damage_time", -999) > 3.0:
        owner.heal(owner.max_hp * 0.0025)

def handler_bt(owner, ctx):
    """Bloodthirster: Khi máu < 40%, tạo lá chắn 25% máu tối đa"""
    if owner.hp < owner.max_hp * 0.4 and not getattr(owner, "_bt_shield_used", False):
        shield_amount = int(owner.max_hp * 0.25)
        # Gọi add_shield khớp với champion.py
        owner.add_shield(shield_amount, duration=5.0) 
        owner._bt_shield_used = True

def handler_shojin(owner, ctx):
    """Shojin: Dùng gain_mana để an toàn"""
    owner.gain_mana(5)

def handler_blue(owner, ctx):
    """Blue Buff: Đặt mana về 20 sau khi dùng chiêu"""
    owner.mana = 20

def handler_titans(owner, ctx):
    """Titan's Resolve: Tích cộng dồn AD/AP"""
    stacks = getattr(owner, "_titans_stacks", 0)
    if stacks < 25:
        owner._titans_stacks = stacks + 1
        owner.ad += int(owner.base_ad * 0.02)
        owner.ability_power += 2
        if owner._titans_stacks == 25:
            owner.armor += 20
            owner.mr += 20

def handler_guinsoo(owner, ctx):
    """Guinsoo: Tăng tốc độ đánh mỗi đòn"""
    owner.attack_speed = min(owner.attack_speed + 0.05, 5.0)

# ==================
# ITEM REGISTRY
# ==================

class ItemRegistry:
    def __init__(self):
        self._items = {}
        self._recipes = {}

    def register(self, item):
        self._items[item.item_id] = item
        if item.item_type == "combined" and len(item.components) == 2:
            key = frozenset(item.components)
            self._recipes[key] = item.item_id

    def get(self, item_id):
        return self._items.get(item_id)

    def load_from_data(self, data):
        """Load từ JSON và gắn Handlers"""
        handlers = {
            "warmogs_armor": handler_warmogs,
            "bloodthirster": handler_bt,
            "spear_of_shojin": handler_shojin,
            "blue_buff": handler_blue,
            "titans_resolve": handler_titans,
            "guinsoos_rageblade": handler_guinsoo
        }

        for entry in data.get("items", []):
            ability = None
            if "ability" in entry:
                ab_data = entry["ability"]
                # Lấy đúng handler theo id trong JSON
                h = handlers.get(entry["id"])
                ability = ItemAbility(
                    name=ab_data.get("name", ""),
                    trigger_type=ab_data.get("trigger", "passive"),
                    description=ab_data.get("description", ""),
                    handler=h
                )

            item = Item(
                item_id=entry["id"],
                name=entry["name"],
                item_type=entry["type"],
                stat_bonuses=entry.get("stats", {}),
                ability=ability,
                components=entry.get("components", [])
            )
            self.register(item)