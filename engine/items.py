# items.py - Hệ thống trang bị TFT
#
# Có 3 loại trang bị:
#   - component  : trang bị thành phần (không ghép được nữa)
#   - combined   : trang bị lớn (ghép từ 2 thành phần)
#   - special    : trang bị đặc biệt (Emblem, Ornn, Radiant, ...)
#
# Mỗi trang bị có:
#   - stat_bonuses : dict chỉ số tăng thêm (apply khi equip)
#   - ability      : cơ chế đặc biệt (trigger theo event)
#
# Ability trigger types:
#   - "on_attack"       : mỗi lần đánh thường
#   - "on_hit"          : mỗi lần đánh trúng (sau khi damage tính xong)
#   - "on_cast"         : khi cast skill
#   - "on_kill"         : khi giết địch
#   - "on_damage_taken" : khi nhận damage
#   - "on_death"        : khi chết
#   - "on_combat_start" : đầu trận
#   - "passive"         : passive liên tục (xử lý trong tick)

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
        self.trigger_type = trigger_type    # Xem danh sách ở trên
        self.description  = description
        self.handler      = handler         # Hàm xử lý: f(owner, context) -> None
        self.cooldown     = cooldown        # Cooldown giữa các lần trigger (giây)
        self._last_trigger = -999.0         # Thời điểm lần trigger gần nhất

    def trigger(self, owner, context):
        """
        Gọi ability. context là dict chứa thông tin liên quan:
          {"time": float, "target": Champion, "damage": float,
           "board": HexBoard, "allies": list, "enemies": list, ...}
        """
        current_time = context.get("time", 0.0)

        # Kiểm tra cooldown
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
# ITEM
# ==================

class Item:
    """
    Trang bị TFT.

    Data load từ items.json, không hardcode logic ở đây
    ngoại trừ các hàm equip/unequip/trigger.
    """

    def __init__(self, item_id, name, item_type, stat_bonuses=None,
                 ability=None, components=None, description=""):
        self.item_id      = item_id         # Unique ID, khớp với JSON
        self.name         = name
        self.item_type    = item_type       # "component" | "combined" | "special"
        self.stat_bonuses = stat_bonuses or {}
        self.ability      = ability         # ItemAbility hoặc None
        self.components   = components or [] # [component_id, component_id] nếu là combined
        self.description  = description

    # ==================
    # EQUIP / UNEQUIP
    # ==================

    def equip(self, champion):
        """
        Trang bị lên champion:
        1. Apply stat bonuses
        2. Thêm item vào danh sách items của champion
        3. Reset cooldown ability
        """
        if len(champion.items) >= 3:
            raise ValueError(f"{champion.name} đã có 3 trang bị (tối đa)")

        # Apply stats
        self._apply_stats(champion, multiply=1)

        # Thêm vào list
        champion.items.append(self)

        # Reset ability cooldown
        if self.ability:
            self.ability.reset()

        # Trigger on_combat_start nếu đang trong combat (hiếm, nhưng có)
        # Thường on_combat_start được trigger bởi CombatSimulator

    def unequip(self, champion):
        """Gỡ trang bị, hoàn lại stats"""
        if self not in champion.items:
            return
        self._apply_stats(champion, multiply=-1)
        champion.items.remove(self)

    def _apply_stats(self, champion, multiply=1):
        """Apply hoặc revert stat bonuses (multiply=1 để add, -1 để remove)"""
        for stat, value in self.stat_bonuses.items():
            v = value * multiply
            if stat == "hp":
                champion.max_hp += v
                champion.hp     += v
            elif stat == "hp_pct":
                bonus = int(champion.base_hp * value) * multiply
                champion.max_hp += bonus
                champion.hp     += bonus
            elif stat == "ad":
                champion.ad += v
            elif stat == "ad_pct":
                champion.ad = int(champion.ad * (1 + value * multiply))
            elif stat == "ability_power":
                champion.ability_power += v
            elif stat == "armor":
                champion.armor += v
            elif stat == "mr":
                champion.mr += v
            elif stat == "attack_speed":
                champion.attack_speed += v
            elif stat == "crit_chance":
                champion.crit_chance += v
            elif stat == "crit_damage":
                champion.crit_damage += v
            elif stat == "damage_amp":
                champion.damage_amp += v
            elif stat == "damage_reduction":
                champion.damage_reduction += v
            elif stat == "omnivamp":
                champion.omnivamp += v
            elif stat == "mana_max":
                champion.max_mana   += int(v)
                # Nếu giảm max_mana thì clamp mana hiện tại
                champion.mana = min(champion.mana, champion.max_mana)

    # ==================
    # TRIGGER ABILITY
    # ==================

    def trigger(self, trigger_type, owner, context):
        """
        Gọi khi một event xảy ra. CombatSimulator gọi hàm này.
        Ví dụ: item.trigger("on_attack", jinx, {"target": zed, "time": 1.5, ...})
        """
        if self.ability and self.ability.trigger_type == trigger_type:
            self.ability.trigger(owner, context)

    def __repr__(self):
        return f"Item({self.name}, {self.item_type})"


# ==================
# ITEM REGISTRY
# ==================

class ItemRegistry:
    """
    Quản lý toàn bộ trang bị trong game.
    Load từ items.json, cung cấp:
      - Tra cứu item theo ID
      - Công thức ghép (component A + component B = combined)
    """

    def __init__(self):
        self._items   = {}          # item_id -> Item
        self._recipes = {}          # frozenset({id_a, id_b}) -> combined_item_id

    def register(self, item):
        """Đăng ký 1 item vào registry"""
        self._items[item.item_id] = item
        if item.item_type == "combined" and len(item.components) == 2:
            key = frozenset(item.components)
            self._recipes[key] = item.item_id

    def get(self, item_id):
        """Lấy Item theo ID"""
        return self._items.get(item_id)

    def combine(self, component_id_a, component_id_b):
        """
        Ghép 2 thành phần thành trang bị lớn.
        Trả về Item hoặc None nếu không có công thức.
        """
        key = frozenset([component_id_a, component_id_b])
        result_id = self._recipes.get(key)
        if result_id:
            return self._items.get(result_id)
        return None

    def get_all_components(self):
        return [i for i in self._items.values() if i.item_type == "component"]

    def get_all_combined(self):
        return [i for i in self._items.values() if i.item_type == "combined"]

    def load_from_data(self, data, ability_handlers=None):
        """
        Load items từ dict (đã parse từ JSON).
        ability_handlers: dict { item_id: hàm handler } để gắn logic vào ability.

        Format JSON mong đợi:
        {
          "items": [
            {
              "id": "bf_sword",
              "name": "B.F. Sword",
              "type": "component",
              "stats": {"ad": 10},
              "components": [],
              "description": "..."
            },
            {
              "id": "infinity_edge",
              "name": "Infinity Edge",
              "type": "combined",
              "stats": {"crit_chance": 0.15, "crit_damage": 0.35},
              "components": ["bf_sword", "sparring_gloves"],
              "ability": {
                "name": "Crits deal bonus damage",
                "trigger": "on_hit",
                "description": "...",
                "cooldown": 0
              },
              "description": "..."
            }
          ]
        }
        """
        ability_handlers = ability_handlers or {}

        for entry in data.get("items", []):
            ability = None
            if "ability" in entry:
                ab_data = entry["ability"]
                handler = ability_handlers.get(entry["id"])
                ability = ItemAbility(
                    name         = ab_data.get("name", ""),
                    trigger_type = ab_data.get("trigger", "passive"),
                    description  = ab_data.get("description", ""),
                    handler      = handler,
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

    def __repr__(self):
        return (f"ItemRegistry({len(self._items)} items, "
                f"{len(self._recipes)} recipes)")


# ==================
# ABILITY HANDLERS MẪU
# (Gắn vào load_from_data qua ability_handlers dict)
# ==================

def handler_ie(owner, ctx):
    """Infinity Edge: crit damage thêm 10% khi crit"""
    if ctx.get("is_crit"):
        bonus = ctx.get("damage", 0) * 0.10
        target = ctx.get("target")
        if target:
            target.take_damage(bonus, "physical", damage_amp_bonus=0)

def handler_sunfire(owner, ctx):
    """Sunfire Cape: đốt target gần nhất mỗi 1 giây, 1% max HP magic damage"""
    enemies = ctx.get("enemies", [])
    board   = ctx.get("board")
    if not enemies or not board:
        return
    alive = [e for e in enemies if e.is_alive]
    if not alive:
        return
    target = min(alive, key=lambda e: board.hex_distance(owner.position, e.position))
    burn_dmg = target.max_hp * 0.01
    target.take_damage(burn_dmg, "magic", damage_amp_bonus=0)

def handler_warmogs(owner, ctx):
    """Warmog's Armor: hồi 5% max HP mỗi giây nếu không nhận damage gần đây"""
    # last_damage_time được cập nhật bởi combat engine
    time = ctx.get("time", 0)
    if time - getattr(owner, "last_damage_time", -999) > 4.0:
        owner.heal(owner.max_hp * 0.05 * 0.05)  # per tick (~1s = 20 ticks)

def handler_rd(owner, ctx):
    """Rapid Firecannon: attack range +2 (apply khi equip, không cần trigger)"""
    pass  # Handled bởi stat_bonuses hoặc equip logic

def handler_titans(owner, ctx):
    """Titan's Resolve: mỗi lần nhận damage tích 1 stack, tối đa 25 stacks → +2% AD/AP mỗi stack"""
    stacks = getattr(owner, "titans_stacks", 0)
    if stacks < 25:
        owner.titans_stacks = stacks + 1
        owner.ad  = int(owner.ad  * (1 + 0.02))
        owner.ability_power += 2

def handler_lw(owner, ctx):
    """Last Whisper: khi crit, giảm 30% armor target trong 3 giây"""
    if ctx.get("is_crit"):
        target = ctx.get("target")
        if target:
            target.apply_sunder(int(target.armor * 0.30))

def handler_morello(owner, ctx):
    """Morellonomicon: khi gây magic damage, đốt target 1% max HP/giây trong 10 giây"""
    target = ctx.get("target")
    if target and ctx.get("damage_type") == "magic":
        # Simplified: deal thẳng 1% max HP ngay lập tức
        target.take_damage(target.max_hp * 0.01, "true", damage_amp_bonus=0)


# Dict để truyền vào load_from_data
ABILITY_HANDLERS = {
    "infinity_edge":   handler_ie,
    "sunfire_cape":    handler_sunfire,
    "warmogs_armor":   handler_warmogs,
    "titans_resolve":  handler_titans,
    "last_whisper":    handler_lw,
    "morellonomicon":  handler_morello,
}