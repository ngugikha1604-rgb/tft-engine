# traits.py - Trait System cho TFT Set 16
#
# Cách dùng:
#   from traits import TraitManager
#
#   manager = TraitManager(trait_data)          # load từ traits.json
#   bonuses = manager.calc_bonuses(champions)   # tính bonus cho team
#   manager.apply(champions)                    # apply lên champions
#   manager.remove(champions)                   # remove khi combat xong
#
# traits.json format:
# {
#   "Bruiser": {
#     "thresholds": [2, 4, 6],
#     "effects": [
#       {"type": "stat", "stat": "hp_pct", "value": 0.15},   ← +15% HP
#       {"type": "stat", "stat": "hp_pct", "value": 0.30},   ← +30% HP
#       {"type": "stat", "stat": "hp_pct", "value": 0.50}    ← +50% HP
#     ]
#   },
#   "Slayer": {
#     "thresholds": [2, 4, 6],
#     "effects": [
#       {"type": "stat", "stat": "crit_chance", "value": 0.10},
#       {"type": "stat", "stat": "crit_chance", "value": 0.20},
#       {"type": "stat", "stat": "crit_chance", "value": 0.35}
#     ]
#   }
# }
#
# Các "type" effect hỗ trợ:
#   "stat"    — thay đổi stat của champion (xem SUPPORTED_STATS)
#   "combat"  — effect xử lý trong combat (placeholder, cần integrate sau)
#
# Các "stat" hỗ trợ (SUPPORTED_STATS):
#   hp_pct          → HP * (1 + value)
#   armor_flat      → armor + value
#   mr_flat         → mr + value
#   ad_pct          → attack_damage * (1 + value)
#   as_pct          → attack_speed * (1 + value)
#   crit_chance     → crit_chance + value
#   crit_damage     → crit_damage + value
#   damage_amp      → damage_amp + value
#   damage_reduction→ damage_reduction + value
#   mana_flat       → mana_start + value (giảm mana cần để cast)

import json

SUPPORTED_STATS = {
    "hp_pct", "armor_flat", "mr_flat", "ad_pct", "as_pct",
    "crit_chance", "crit_damage", "damage_amp", "damage_reduction", "mana_flat"
}


# ==================
# TRAIT DEFINITION
# ==================

class Trait:
    """
    Một trait với các threshold và effect tương ứng.
    """
    def __init__(self, name, thresholds, effects):
        """
        name        : tên trait, ví dụ "Bruiser"
        thresholds  : list[int] số lượng champions cần, ví dụ [2, 4, 6]
        effects     : list[dict] effect ở mỗi threshold tương ứng
        """
        self.name       = name
        self.thresholds = thresholds
        self.effects    = effects   # len phải bằng len(thresholds)

    def get_active_level(self, count):
        """
        Trả về (level, effect) đang active với count champions.
        Level bắt đầu từ 1. Trả về (0, None) nếu chưa đủ threshold.
        """
        active_level  = 0
        active_effect = None
        for i, threshold in enumerate(self.thresholds):
            if count >= threshold:
                active_level  = i + 1
                active_effect = self.effects[i] if i < len(self.effects) else None
        return active_level, active_effect

    def __repr__(self):
        return f"Trait({self.name}, thresholds={self.thresholds})"


# ==================
# TRAIT MANAGER
# ==================

class TraitManager:
    """
    Quản lý trait system cho 1 team.
    Được tạo mới mỗi combat (không persistent).
    """

    def __init__(self, trait_data=None):
        """
        trait_data: dict load từ traits.json
                    Nếu None thì dùng DEFAULT_TRAITS built-in.
        """
        self.traits = {}    # name -> Trait object
        data = trait_data or DEFAULT_TRAITS
        for name, info in data.items():
            self.traits[name] = Trait(
                name       = name,
                thresholds = info.get("thresholds", []),
                effects    = info.get("effects", []),
            )

        # Track những gì đã apply để có thể remove
        self._applied = {}   # champ -> list of (stat, delta) đã apply

    # ==================
    # TÍNH BONUS
    # ==================

    def count_traits(self, champions):
        """
        Đếm số champions mỗi trait trong team.
        Trả về dict {trait_name: count}.
        """
        counts = {}
        for champ in champions:
            for trait in getattr(champ, "traits", []):
                counts[trait] = counts.get(trait, 0) + 1
        return counts

    def calc_bonuses(self, champions):
        """
        Tính bonus cho team hiện tại.
        Trả về list of dict mô tả các bonus đang active:
        [{"trait": "Bruiser", "level": 2, "count": 3, "effect": {...}}, ...]
        """
        counts  = self.count_traits(champions)
        bonuses = []
        for trait_name, count in counts.items():
            trait = self.traits.get(trait_name)
            if not trait:
                continue
            level, effect = trait.get_active_level(count)
            if level > 0 and effect:
                bonuses.append({
                    "trait":  trait_name,
                    "level":  level,
                    "count":  count,
                    "effect": effect,
                })
        return bonuses

    # ==================
    # APPLY / REMOVE
    # ==================

    def apply(self, champions):
        """
        Apply tất cả trait bonuses lên champions trong team.
        Gọi trước combat. Nhớ gọi remove() sau combat.
        """
        self._applied = {id(c): [] for c in champions}
        bonuses = self.calc_bonuses(champions)

        for bonus in bonuses:
            effect    = bonus["effect"]
            eff_type  = effect.get("type", "stat")

            if eff_type != "stat":
                # Combat effects sẽ handle riêng sau
                continue

            stat  = effect.get("stat")
            value = effect.get("value", 0)

            if stat not in SUPPORTED_STATS:
                continue

            for champ in champions:
                delta = self._apply_stat(champ, stat, value)
                self._applied[id(champ)].append((stat, delta))



    def remove(self, champions):
        """
        Remove tất cả trait bonuses sau combat.
        Đảm bảo stats trở về giá trị gốc.
        """
        for champ in champions:
            applied = self._applied.get(id(champ), [])
            for stat, delta in reversed(applied):
                self._remove_stat(champ, stat, delta)
        self._applied = {}

    # ==================
    # APPLY TỪNG STAT
    # ==================

    def _apply_stat(self, champ, stat, value):
        """
        Apply 1 stat lên champion, trả về delta thực tế để sau này revert.
        """
        if stat == "hp_pct":
            delta = champ.max_hp * value
            champ.max_hp += delta
            champ.hp     += delta   # tăng HP hiện tại cùng lúc
            return delta

        elif stat == "armor_flat":
            champ.armor += value
            return value

        elif stat == "mr_flat":
            champ.mr += value
            return value

        elif stat == "ad_pct":
            delta = champ.ad * value
            champ.ad += delta
            return delta

        elif stat == "as_pct":
            delta = champ.attack_speed * value
            champ.attack_speed += delta
            return delta

        elif stat == "crit_chance":
            champ.crit_chance += value
            return value

        elif stat == "crit_damage":
            champ.crit_damage += value
            return value

        elif stat == "damage_amp":
            champ.damage_amp = getattr(champ, "damage_amp", 0) + value
            return value

        elif stat == "damage_reduction":
            champ.damage_reduction = getattr(champ, "damage_reduction", 0) + value
            return value

        elif stat == "mana_flat":
            # Giảm mana cần cast (mana_start tăng = cast nhanh hơn)
            champ.mana_start = min(champ.mana_start + value, champ.max_mana - 10)
            return value

        return 0

    def _remove_stat(self, champ, stat, delta):
        """Revert 1 stat về giá trị cũ"""
        if stat == "hp_pct":
            champ.max_hp -= delta
            champ.hp      = min(champ.hp, champ.max_hp)

        elif stat == "armor_flat":
            champ.armor -= delta

        elif stat == "mr_flat":
            champ.mr -= delta

        elif stat == "ad_pct":
            champ.ad -= delta

        elif stat == "as_pct":
            champ.attack_speed -= delta

        elif stat == "crit_chance":
            champ.crit_chance -= delta

        elif stat == "crit_damage":
            champ.crit_damage -= delta

        elif stat == "damage_amp":
            champ.damage_amp = getattr(champ, "damage_amp", 0) - delta

        elif stat == "damage_reduction":
            champ.damage_reduction = getattr(champ, "damage_reduction", 0) - delta

        elif stat == "mana_flat":
            champ.mana_start -= delta

    # ==================
    # LOAD FROM JSON
    # ==================

    @classmethod
    def from_json(cls, path):
        """Load TraitManager từ traits.json"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Bỏ key _meta nếu có
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        return cls(trait_data=data)

    # ==================
    # DEBUG
    # ==================

    def get_active_summary(self, champions):
        """
        Trả về string mô tả traits đang active — dùng để debug/render.
        Ví dụ: "Bruiser(3/4 ★★) | Slayer(2/2 ★)"
        """
        counts  = self.count_traits(champions)
        parts   = []
        for trait_name, count in sorted(counts.items()):
            trait = self.traits.get(trait_name)
            if not trait:
                continue
            level, _ = trait.get_active_level(count)
            next_threshold = next(
                (t for t in trait.thresholds if t > count), None
            )
            stars = "★" * level if level > 0 else "☆"
            if next_threshold:
                parts.append(f"{trait_name}({count}/{next_threshold} {stars})")
            else:
                parts.append(f"{trait_name}({count} {stars}MAX)")
        return " | ".join(parts) if parts else "No active traits"


# ==================
# DEFAULT TRAITS — load từ traits.json
# ==================

def _load_default_traits():
    """Load traits từ data/traits.json cùng thư mục"""
    import os
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(_this_dir, 'data', 'traits.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    # Fallback rỗng nếu không có file
    # NOTE: keep message ASCII for Windows consoles with cp1252.
    print("[TraitManager] WARNING: data/traits.json not found!")
    return {}

DEFAULT_TRAITS = _load_default_traits()