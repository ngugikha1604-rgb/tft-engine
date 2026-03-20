# combat.py - Mô phỏng trận đấu TFT theo từng tick thời gian

import random
import os

# Load AbilityLoader — optional, không crash nếu chưa có file
try:
    from ability_loader import AbilityLoader
    _ABILITY_LOADER_AVAILABLE = True
except ImportError:
    _ABILITY_LOADER_AVAILABLE = False

# Khởi tạo loader global — load 1 lần duy nhất
_ability_loader = None

def get_ability_loader(path=None):
    """Lấy AbilityLoader singleton"""
    global _ability_loader
    if _ability_loader is None and _ABILITY_LOADER_AVAILABLE:
        _this_dir = os.path.dirname(os.path.abspath(__file__))
        default   = os.path.join(_this_dir, 'data', 'abilities.json')
        load_path = path or default
        if os.path.exists(load_path):
            _ability_loader = AbilityLoader(load_path)
    return _ability_loader

# ==================
# HẰNG SỐ COMBAT
# ==================

TICK_RATE        = 0.05     # Mỗi tick = 0.05 giây (20 ticks/giây)
MAX_ATTACK_SPEED = 5.0      # Giới hạn AS tối đa
MANA_LOCK_AFTER_CAST = 1.0  # Không tích mana trong 1 giây sau khi cast

# Mana/attack theo role
MANA_PER_ATTACK = {
    "tank":       5,
    "fighter":    10,
    "assassin":   10,
    "marksman":   10,
    "caster":     7,
    "specialist": 10,
}

# ==================
# COMBAT EVENT & SIMULATOR
# ==================

class CombatEvent:
    def __init__(self, time, type_, source, target=None, value=0, extra=None):
        self.time = time
        self.type = type_
        self.source = source
        self.target = target
        self.value = value
        self.extra = extra or {}

class CombatSimulator:
    def __init__(self, board, team_a, team_b, max_time=30.0):
        """
        board   : HexBoard dùng để tính khoảng cách và di chuyển
        team_a  : list[Champion] đội A
        team_b  : list[Champion] đội B
        """
        self.board    = board
        self.team_a   = team_a
        self.team_b   = team_b
        self.max_time = max_time
        self.time     = 0.0
        self.tick     = 0
        self.events   = []
        self._ability_loader = get_ability_loader()

    def _do_attack(self, champ, target):
        """Thực hiện đánh thường và kích hoạt trang bị"""
        # 1. Set cooldown attack
        as_cap = min(champ.attack_speed, MAX_ATTACK_SPEED)
        champ.attack_timer = 1.0 / as_cap

        # 2. Tính crit — calc_crit(base_damage) trả về (damage_after_crit, is_crit)
        damage_after_crit, is_crit = champ.calc_crit(champ.ad)

        # 3. Trigger on_attack (trước khi gây damage)
        context_atk = {"time": self.time, "target": target, "is_crit": is_crit}
        for item in getattr(champ, 'items', []):
            if hasattr(item, 'trigger'):
                item.trigger("on_attack", champ, context_atk)

        # 4. Gây sát thương — take_damage() giờ return actual_damage
        actual = target.take_damage(
            damage_after_crit,
            damage_type  = "physical",
            damage_amp_bonus = champ.damage_amp
        ) or 0.0

        # 5. Trigger on_hit (sau khi có damage thực tế)
        context_hit = {**context_atk, "damage": actual, "damage_type": "physical"}
        for item in getattr(champ, 'items', []):
            if hasattr(item, 'trigger'):
                item.trigger("on_hit", champ, context_hit)

        # 6. Mana gain cho attacker
        mana_gain = MANA_PER_ATTACK.get(getattr(champ, "role", "fighter"), 10)
        champ.gain_mana(mana_gain)

        # 7. Omnivamp
        if champ.omnivamp > 0 and actual > 0:
            healed = actual * champ.omnivamp
            champ.heal(healed)
            self.events.append(CombatEvent(self.time, "heal", champ.name, value=healed))

        # 8. Log
        self.events.append(CombatEvent(
            self.time, "attack", champ.name, target=target.name,
            value=actual, extra={"is_crit": is_crit}
        ))

        # 9. Xử lý tử vong
        if not target.is_alive:
            self.events.append(CombatEvent(self.time, "death", target.name))
            self._on_death(target)

    # ... (Các hàm update_tick, _find_target, _on_death giữ nguyên logic cũ) ...

    def run(self, max_seconds=30):
        """Chạy mô phỏng cho đến khi kết thúc hoặc hết giờ"""
        self.max_time = max_seconds
        while self.time < self.max_time:
            winner = self._check_winner()
            if winner:
                return self._build_result(winner)

            # Update tất cả units (shuffle để tránh bias)
            all_units = [c for c in self.team_a + self.team_b if c.is_alive]
            random.shuffle(all_units)
            for unit in all_units:
                self._update_unit(unit)

            self.time  += TICK_RATE
            self.tick  += 1

        return self._build_result("draw")

    def _update_unit(self, unit):
        """Update 1 champion mỗi tick"""
        if not unit.is_alive:
            return

        # Giảm cooldown
        if unit.attack_timer > 0:
            unit.attack_timer -= TICK_RATE
        if unit.mana_lock_timer > 0:
            unit.mana_lock_timer -= TICK_RATE

        # Update item passives
        for item in getattr(unit, 'items', []):
            if hasattr(item, 'trigger'):
                ctx = {
                    "time": self.time, "tick": self.tick,
                    "hp_percent": unit.hp / unit.max_hp if unit.max_hp > 0 else 0
                }
                item.trigger("passive", unit, ctx)

        # Cast ability nếu đủ mana
        if unit.can_cast():
            self._cast_ability(unit)
            return  # Không attack cùng tick với cast

        # Tìm target và tấn công/di chuyển
        target = self._find_target(unit)
        if not target:
            return

        dist = self.board.hex_distance(unit.position, target.position) if (unit.position and target.position) else 999
        if dist <= unit.range:
            if unit.attack_timer <= 0:
                self._do_attack(unit, target)
        else:
            self._move_toward(unit, target)


    def _find_target(self, champ):
        """Tìm target gần nhất trong team địch còn sống"""
        enemies = self.team_b if champ in self.team_a else self.team_a
        alive_enemies = [e for e in enemies if e.is_alive]
        if not alive_enemies:
            return None
        if champ.position is None:
            return alive_enemies[0]
        def dist(e):
            if e.position is None:
                return 999
            return self.board.hex_distance(champ.position, e.position)
        return min(alive_enemies, key=dist)

    def _move_toward(self, champ, target):
        """Di chuyển 1 bước về phía target"""
        if champ.position is None or target.position is None:
            return
        neighbors = self.board.get_neighbors(*champ.position)
        if not neighbors:
            return
        # Chọn ô gần target nhất và còn trống
        def dist_to_target(pos):
            return self.board.hex_distance(pos, target.position)
        candidates = [p for p in neighbors if self.board.is_empty(*p)]
        if not candidates:
            return
        best = min(candidates, key=dist_to_target)
        self.board.move(champ, best[0], best[1])

    def _cast_ability(self, champ):
        """Tung chiêu — dùng AbilityLoader nếu có, fallback về generic"""
        champ.spend_mana()
        champ.mana_lock_timer = MANA_LOCK_AFTER_CAST

        # Thử dùng ability thật từ loader
        if self._ability_loader and self._ability_loader.has(champ.name):
            self._ability_loader.cast(champ, self)
            return

        # Fallback: generic single target magic damage
        target = self._find_target(champ)
        if not target:
            return

        ap_ratio = getattr(champ, 'ability_power', 100) / 100.0
        base_dmg = champ.ad * ap_ratio
        actual   = target.take_damage(base_dmg, damage_type="magic",
                                      damage_amp_bonus=champ.damage_amp) or 0.0

        self.events.append(CombatEvent(
            self.time, "ability", champ.name, target=target.name, value=actual
        ))

        if not target.is_alive:
            self.events.append(CombatEvent(self.time, "death", target.name))
            self._on_death(target)

    def _on_death(self, champ):
        """Xử lý khi champion chết — trigger on_death items"""
        champ.is_alive = False
        for item in getattr(champ, 'items', []):
            if hasattr(item, 'trigger'):
                item.trigger("on_death", champ, {"time": self.time})
        # Xóa khỏi board nếu có
        if champ.position and self.board:
            try:
                self.board.remove(champ)
            except Exception:
                pass

    def _check_winner(self):
        a_alive = any(c.is_alive for c in self.team_a)
        b_alive = any(c.is_alive for c in self.team_b)
        if not a_alive and not b_alive: return "draw"
        if not b_alive: return "team_a"
        if not a_alive: return "team_b"
        return None

    def _build_result(self, winner):
        return {
            "winner": winner,
            "duration": self.time,
            "survivors_a": [c for c in self.team_a if c.is_alive],
            "survivors_b": [c for c in self.team_b if c.is_alive],
            "events": self.events
        }