# items.py - Hệ thống trang bị TFT hoàn chỉnh 💎

import random

# ==================
# ITEM ABILITY
# ==================

class ItemAbility:
    def __init__(self, name, trigger_type, description, handler=None, cooldown=0.0):
        self.name         = name
        self.trigger_type = trigger_type    # passive, on_attack, on_hit, etc.
        self.description  = description
        self.handler      = handler         # Hàm xử lý: f(owner, context)
        self.cooldown     = cooldown        # Cooldown giữa các lần trigger (giây)
        self._last_trigger = -999.0         

    def trigger(self, owner, context):
        current_time = context.get("time", 0.0)
        if current_time - self._last_trigger < self.cooldown:
            return
        self._last_trigger = current_time
        if self.handler:
            self.handler(owner, context)

    def reset(self):
        self._last_trigger = -999.0

# ==================
# ITEM CLASS (Đã khôi phục đầy đủ các hàm)
# ==================

class Item:
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
        """Lắp đồ lên tướng và cộng chỉ số"""
        if len(champion.items) >= 3:
            return False
        # 1. Cộng chỉ số trực tiếp
        self._apply_stats(champion, multiply=1)
        # 2. Thêm vào danh sách của tướng
        champion.items.append(self)
        # 3. Reset cooldown kỹ năng của đồ
        if self.ability:
            self.ability.reset()
        return True

    def unequip(self, champion):
        """Tháo đồ và trừ lại chỉ số (Dùng khi bán tướng)"""
        if self not in champion.items:
            return
        self._apply_stats(champion, multiply=-1)
        champion.items.remove(self)

    def trigger(self, trigger_type, owner, context):
        """Shortcut để combat.py gọi trực tiếp item.trigger(type, owner, ctx)"""
        if self.ability and self.ability.trigger_type == trigger_type:
            self.ability.trigger(owner, context)

    def _apply_stats(self, champion, multiply=1):
        """Xử lý logic cộng/trừ chỉ số — tự động map tên stat"""
        STAT_MAP = {
            "attack_damage": "ad",          # items.json dùng attack_damage → champion dùng ad
            "mana_start":    "mana_start",
            "mana_max":      "max_mana",
        }
        for stat, value in self.stat_bonuses.items():
            champ_stat = STAT_MAP.get(stat, stat)   # map tên nếu cần
            v = value * multiply

            # crit_chance trong JSON là số nguyên (35) → convert sang float (0.35)
            if champ_stat == "crit_chance":
                v = (value / 100.0) * multiply

            if not hasattr(champion, champ_stat):
                continue

            if champ_stat == "hp":
                champion.max_hp += v
                champion.hp     += v
            elif champ_stat == "max_mana":
                champion.max_mana += int(v)
                champion.mana = min(champion.mana, champion.max_mana)
            elif champ_stat == "mana_start":
                champion.mana_start += v
            else:
                setattr(champion, champ_stat, getattr(champion, champ_stat) + v)

# ==================
# ABILITY HANDLERS (Logic chiến đấu)
# ==================

def handler_warmogs(owner, ctx):
    time = ctx.get("time", 0)
    if time - getattr(owner, "last_damage_time", -999) > 3.0:
        owner.heal(owner.max_hp * 0.0025)

def handler_bt(owner, ctx):
    if owner.hp < owner.max_hp * 0.4 and not getattr(owner, "_bt_shield_used", False):
        owner.add_shield(int(owner.max_hp * 0.25), duration=5.0) 
        owner._bt_shield_used = True

def handler_shojin(owner, ctx):
    owner.gain_mana(5)

def handler_blue(owner, ctx):
    owner.mana = 20

def handler_titans(owner, ctx):
    stacks = getattr(owner, "_titans_stacks", 0)
    if stacks < 25:
        owner._titans_stacks = stacks + 1
        owner.ad += int(owner.base_ad * 0.02)
        owner.ability_power += 2
        if owner._titans_stacks == 25:
            owner.armor += 20
            owner.mr += 20

def handler_guinsoo(owner, ctx):
    owner.attack_speed = min(owner.attack_speed + 0.05, 5.0)

def handler_ie(owner, ctx):
    """Infinity Edge — tăng crit damage thêm 10% mỗi lần crit"""
    if ctx.get("is_crit"):
        bonus = getattr(owner, "_ie_bonus", 0)
        if bonus < 0.30:    # cap +30%
            owner._ie_bonus     = bonus + 0.10
            owner.crit_damage  += 0.10

def handler_rabadon(owner, ctx):
    """Rabadon — passive: tăng 40% ability_power (apply 1 lần khi equip)"""
    # Passive được handle trong _apply_stats bằng stat bonus trực tiếp
    pass

def handler_bramble(owner, ctx):
    """Bramble Vest — phản sát thương magic khi bị đánh"""
    attacker = ctx.get("attacker")
    if attacker and attacker.is_alive:
        attacker.take_damage(80, damage_type="magic")

def handler_dragon_claw(owner, ctx):
    """Dragon Claw — hồi 2% HP tối đa mỗi 2 giây"""
    time = ctx.get("time", 0)
    last = getattr(owner, "_dragon_claw_time", -999)
    if time - last >= 2.0:
        owner._dragon_claw_time = time
        owner.heal(owner.max_hp * 0.02)

# ==================
# REGISTRY & EXPORTS (Sửa lỗi ImportError)
# ==================

# Biến này PHẢI nằm ngoài cùng để file game.py import được
ABILITY_HANDLERS = {
    # ID phải khớp với "id" trong items.json
    "Warmog_Armor":       handler_warmogs,
    "Bloodthirster":      handler_bt,
    "Spear_of_Shojin":    handler_shojin,
    "Blue_Buff":          handler_blue,
    "Titan_Resolve":      handler_titans,
    "Guinsoo_Rageblade":  handler_guinsoo,
    "Infinity_Edge":      handler_ie,
    "Rabadon_Deathcap":   handler_rabadon,
    "Bramble_Vest":       handler_bramble,
    "Dragon_Claw":        handler_dragon_claw,
}

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

    def load_from_data(self, data, handlers=None):
        """Load items từ JSON. handlers: dict override ABILITY_HANDLERS"""
        _handlers = {**ABILITY_HANDLERS, **(handlers or {})}
        # data có thể là dict {"items": [...]} hoặc list trực tiếp
        entries = data.get("items", data) if isinstance(data, dict) else data
        for entry in entries:
            ability = None
            if "ability" in entry:
                ab_data = entry["ability"]
                h = _handlers.get(entry["id"])
                ability = ItemAbility(
                    name         = ab_data.get("name", ""),
                    trigger_type = ab_data.get("trigger", "passive"),
                    description  = ab_data.get("description", ""),
                    handler      = h,
                    cooldown     = ab_data.get("cooldown", 0.0),
                )
            item = Item(
                item_id      = entry["id"],
                name         = entry["name"],
                item_type    = entry["type"],
                stat_bonuses = entry.get("stats", {}),
                ability      = ability,
                components   = entry.get("components", []),
                description  = entry.get("description", ""),
            )
            self.register(item)