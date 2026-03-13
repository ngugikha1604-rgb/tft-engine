# combat.py - Mô phỏng trận đấu TFT theo từng tick thời gian

import random

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
    def __init__(self, team_a, team_b, board, max_time=30.0):
        self.team_a = team_a
        self.team_b = team_b
        self.board = board
        self.max_time = max_time
        self.time = 0.0
        self.events = []

    def _do_attack(self, champ, target):
        """Thực hiện đánh thường và kích hoạt trang bị 🛡️"""
        # 1. Tính toán hồi chiêu và Crit
        as_cap   = min(champ.attack_speed, MAX_ATTACK_SPEED)
        interval = 1.0 / as_cap
        champ.attack_timer = interval
        damage_after_crit, is_crit = champ.calc_crit(champ.ad)

        # 2. Trigger "on_attack" (trước khi gây sát thương)
        context_atk = {"time": self.time, "target": target, "is_crit": is_crit}
        if hasattr(champ, 'items'):
            for item in champ.items:
                item.trigger("on_attack", champ, context_atk)

        # 3. Gây sát thương thực tế
        actual = target.take_damage(
            damage_after_crit,
            damage_type="physical",
            damage_amp_bonus=champ.damage_amp
        )

        # 4. Trigger "on_hit" (sau khi đã có damage thực tế 'actual')
        context_hit = context_atk.copy()
        context_hit.update({"damage": actual, "damage_type": "physical"})
        if hasattr(champ, 'items'):
            for item in champ.items:
                item.trigger("on_hit", champ, context_hit)

        # 5. Mana và Hồi máu (Omnivamp)
        mana_gain = MANA_PER_ATTACK.get(getattr(champ, "role", "fighter"), 10)
        if champ.mana_lock_timer <= 0:
            champ.gain_mana(mana_gain)

        if champ.omnivamp > 0 and actual > 0:
            healed = actual * champ.omnivamp
            champ.heal(healed)
            self.events.append(CombatEvent(self.time, "heal", champ.name, value=healed))

        # 6. Log và kiểm tra tử vong
        self.events.append(CombatEvent(
            self.time, "attack", champ.name, target=target.name,
            value=actual, extra={"is_crit": is_crit, "damage_type": "physical"}
        ))
        if not target.is_alive:
            self.events.append(CombatEvent(self.time, "death", target.name))
            self._on_death(target)

    # ... (Các hàm update_tick, _find_target, _on_death giữ nguyên logic cũ) ...

    def run(self):
        """Chạy mô phỏng cho đến khi kết thúc hoặc hết giờ"""
        while self.time < self.max_time:
            winner = self._check_winner()
            if winner:
                return self._build_result(winner)
            
            # Update từng champion
            all_units = self.team_a + self.team_b
            random.shuffle(all_units)
            for unit in all_units:
                if unit.is_alive:
                    self._update_unit(unit)
            
            self.time += TICK_RATE
        
        return self._build_result("draw")

    def _update_unit(self, unit):
        # Giảm cooldown các trạng thái
        if unit.attack_timer > 0: unit.attack_timer -= TICK_RATE
        if unit.mana_lock_timer > 0: unit.mana_lock_timer -= TICK_RATE
        
        # Logic di chuyển hoặc tấn công
        target = self._find_target(unit)
        if target:
            dist = self.board.distance(unit.position, target.position)
            if dist <= unit.range:
                if unit.attack_timer <= 0:
                    self._do_attack(unit, target)
            else:
                self._move_toward(unit, target)

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