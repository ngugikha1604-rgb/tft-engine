# combat.py - Mô phỏng trận đấu TFT theo từng tick thời gian

import random

# ==================
# HẰNG SỐ COMBAT
# ==================

TICK_RATE        = 0.05     # Mỗi tick = 0.05 giây (20 ticks/giây)
MAX_ATTACK_SPEED = 5.0      # Giới hạn AS tối đa
MANA_LOCK_AFTER_CAST = 1.0  # Không tích mana trong 1 giây sau khi cast

# Mana/attack theo role (từ Roles Revamped Set 15+)
MANA_PER_ATTACK = {
    "tank":       5,
    "fighter":    10,
    "assassin":   10,
    "marksman":   10,
    "caster":     7,
    "specialist": 10,
}

# Mana/giây (mana regen tự nhiên) theo role
MANA_REGEN_PER_SEC = {
    "tank":       0,
    "fighter":    0,
    "assassin":   0,
    "marksman":   0,
    "caster":     2,    # Caster tự regen 2 mana/giây
    "specialist": 0,
}

# Targeting priority (tiebreaker khi cùng khoảng cách)
# Số càng nhỏ = càng dễ bị target
TARGET_PRIORITY = {
    "tank":       0,    # Bị target nhiều nhất
    "fighter":    1,
    "marksman":   1,
    "caster":     1,
    "specialist": 1,
    "assassin":   2,    # Ít bị target nhất
}


# ==================
# COMBAT EVENT (log để debug/replay)
# ==================

class CombatEvent:
    """Ghi lại một sự kiện xảy ra trong trận"""
    def __init__(self, time, event_type, source, target=None, value=0, extra=None):
        self.time       = round(time, 3)
        self.event_type = event_type    # "attack", "cast", "move", "death", "heal", "mana"
        self.source     = source        # Tên champion
        self.target     = target        # Tên target (nếu có)
        self.value      = round(value, 1)
        self.extra      = extra or {}   # Thông tin thêm (is_crit, damage_type, ...)

    def __repr__(self):
        t = f"[{self.time:.2f}s]"
        if self.event_type == "attack":
            crit = " CRIT!" if self.extra.get("is_crit") else ""
            return f"{t} {self.source} attacks {self.target} for {self.value}{crit}"
        elif self.event_type == "cast":
            return f"{t} {self.source} CASTS ability"
        elif self.event_type == "move":
            return f"{t} {self.source} moves to {self.extra.get('pos')}"
        elif self.event_type == "death":
            return f"{t} {self.source} DIES"
        elif self.event_type == "heal":
            return f"{t} {self.source} heals {self.value} HP"
        elif self.event_type == "mana_locked":
            return f"{t} {self.source} mana locked after cast"
        return f"{t} {self.event_type}: {self.source}"


# ==================
# COMBAT SIMULATOR
# ==================

class CombatSimulator:
    """
    Mô phỏng 1 trận đấu TFT theo tick-based simulation.

    Luồng mỗi tick:
    1. Cập nhật timers (attack_timer, mana_lock_timer)
    2. Mana regen tự nhiên (Caster)
    3. Assassin jump nếu tick đầu tiên
    4. Mỗi champion còn sống:
       a. Tìm target
       b. Nếu target trong tầm → attack (nếu timer = 0)
       c. Nếu target ngoài tầm → di chuyển lại gần
    5. Kiểm tra kết thúc
    """

    def __init__(self, board, team_a, team_b):
        """
        board   : HexBoard
        team_a  : list Champion (hàng 0-3)
        team_b  : list Champion (hàng 4-7)
        """
        self.board   = board
        self.team_a  = team_a
        self.team_b  = team_b
        self.time    = 0.0
        self.events  = []

        # Thêm timer mana lock vào từng champion
        for champ in team_a + team_b:
            champ.mana_lock_timer = 0.0     # Giây còn lại không tích mana
            champ.move_cooldown   = 0.0     # Cooldown di chuyển (~0.5s/hex)

    # ==================
    # ENTRY POINT
    # ==================

    def run(self, max_seconds=30):
        """
        Chạy simulation đến khi 1 team chết hoặc hết giờ.
        Trả về dict kết quả.
        """
        # Reset champion về trạng thái đầu trận
        for champ in self.team_a + self.team_b:
            champ.reset_for_combat()
            champ.mana_lock_timer = 0.0
            champ.move_cooldown   = 0.0

        # Reapply tile effects sau khi reset
        self.board.reapply_all_tiles()

        max_ticks = int(max_seconds / TICK_RATE)

        for _ in range(max_ticks):
            self.time += TICK_RATE
            self._tick()

            winner = self._check_winner()
            if winner:
                return self._build_result(winner)

        return self._build_result("draw")

    # ==================
    # TICK CHÍNH
    # ==================

    def _tick(self):
        dt = TICK_RATE

        for champ in self.team_a + self.team_b:
            if not champ.is_alive:
                continue

            # Giảm timers
            champ.attack_timer    = max(0.0, champ.attack_timer - dt)
            champ.mana_lock_timer = max(0.0, champ.mana_lock_timer - dt)
            champ.move_cooldown   = max(0.0, champ.move_cooldown - dt)

            # Mana regen tự nhiên theo role (Caster: +2/giây)
            regen = MANA_REGEN_PER_SEC.get(champ.role, 0)
            if regen > 0 and champ.mana_lock_timer <= 0:
                champ.gain_mana(regen * dt)

            # Xác định đội địch
            enemies = self.team_b if champ in self.team_a else self.team_a
            alive_enemies = [e for e in enemies if e.is_alive]
            if not alive_enemies:
                continue

            # Tìm target tốt nhất
            target = self._find_target(champ, alive_enemies)
            if target is None:
                continue

            dist = self.board.hex_distance(champ.position, target.position)

            if dist <= champ.range:
                # Trong tầm → cast nếu đủ mana, không thì attack
                if champ.can_cast() and champ.mana_lock_timer <= 0:
                    self._cast_ability(champ, alive_enemies)
                elif champ.attack_timer <= 0:
                    self._do_attack(champ, target)
            else:
                # Ngoài tầm → di chuyển
                if champ.move_cooldown <= 0:
                    self._move_toward(champ, target)

    # ==================
    # TARGETING
    # ==================

    def _find_target(self, champ, enemies):
        """
        Tìm target theo quy tắc TFT:
        1. Ưu tiên enemy đang bị attack bởi champ này (sticky targeting)
        2. Gần nhất
        3. Tiebreaker: targeting priority (tank trước, assassin sau)
        4. Tiebreaker thứ 2: HP thấp nhất
        """
        # Sticky targeting — giữ target cũ nếu vẫn còn sống và trong tầm
        if (champ.current_target
                and champ.current_target.is_alive
                and champ.current_target in enemies):
            dist = self.board.hex_distance(champ.position, champ.current_target.position)
            # Giữ target nếu vẫn trong tầm x2 (tránh switch liên tục)
            if dist <= champ.range * 2:
                return champ.current_target

        # Tìm target mới
        def sort_key(e):
            dist     = self.board.hex_distance(champ.position, e.position)
            priority = TARGET_PRIORITY.get(getattr(e, "role", "fighter"), 1)
            return (dist, priority, e.hp)

        target = min(enemies, key=sort_key)
        champ.current_target = target
        return target

    # ==================
    # ATTACK
    # ==================

    def _do_attack(self, champ, target):
        """Thực hiện 1 lần đánh thường"""
        # Tính attack interval từ attack speed
        as_cap   = min(champ.attack_speed, MAX_ATTACK_SPEED)
        interval = 1.0 / as_cap
        champ.attack_timer = interval

        # Tính damage + crit
        damage_after_crit, is_crit = champ.calc_crit(champ.ad)

        # Gây damage (hàm này tự tính armor/mr/DR và trả về actual damage)
        actual = target.take_damage(
            damage_after_crit,
            damage_type="physical",
            damage_amp_bonus=champ.damage_amp
        )

        # Mana cho attacker khi attack
        mana_gain = MANA_PER_ATTACK.get(getattr(champ, "role", "fighter"), 10)
        if champ.mana_lock_timer <= 0:
            champ.gain_mana(mana_gain)

        # Mana cho target khi bị đánh (Tank nhận mana từ damage)
        if getattr(target, "role", "") == "tank":
            raw_for_mana   = damage_after_crit * (1 + champ.damage_amp)
            mana_from_dmg  = min(42.5,
                raw_for_mana * 0.01 + actual * 0.07)
            if target.mana_lock_timer <= 0:
                target.gain_mana(mana_from_dmg)

        # Omnivamp
        if champ.omnivamp > 0 and actual > 0:
            healed = actual * champ.omnivamp
            champ.heal(healed)
            self.events.append(CombatEvent(
                self.time, "heal", champ.name, value=healed))

        # Log event
        self.events.append(CombatEvent(
            self.time, "attack", champ.name, target=target.name,
            value=actual, extra={"is_crit": is_crit, "damage_type": "physical"}
        ))

        # Kiểm tra target chết
        if not target.is_alive:
            self.events.append(CombatEvent(self.time, "death", target.name))
            self._on_death(target)

    # ==================
    # ABILITY CAST
    # ==================

    def _cast_ability(self, champ, enemies):
        """
        Cast ability — placeholder cho đến khi có ability system.
        Hiện tại: deal damage bằng AD * 2 lên target gần nhất.
        Sau này sẽ gọi champ.ability(board, allies, enemies).
        """
        champ.spend_mana()
        champ.mana_lock_timer = MANA_LOCK_AFTER_CAST

        self.events.append(CombatEvent(self.time, "cast", champ.name))

        # Placeholder: magic damage lên target gần nhất
        target = self._find_target(champ, enemies)
        if target:
            spell_damage = champ.ad * 2   # Placeholder, thay bằng spell data sau
            actual = target.take_damage(spell_damage, damage_type="magic",
                                        damage_amp_bonus=champ.damage_amp)
            self.events.append(CombatEvent(
                self.time, "attack", champ.name, target=target.name,
                value=actual, extra={"is_crit": False, "damage_type": "magic_spell"}
            ))
            if not target.is_alive:
                self.events.append(CombatEvent(self.time, "death", target.name))
                self._on_death(target)

    # ==================
    # DI CHUYỂN
    # ==================

    def _move_toward(self, champ, target):
        """Di chuyển 1 hex về phía target"""
        next_pos = self.board.find_move_toward(champ, target)
        if next_pos:
            self.board.move(champ, *next_pos)
            champ.move_cooldown = 0.5   # 0.5 giây cooldown mỗi bước di chuyển
            self.events.append(CombatEvent(
                self.time, "move", champ.name,
                extra={"pos": next_pos}
            ))

    # ==================
    # ON DEATH
    # ==================

    def _on_death(self, champion):
        """Xử lý khi champion chết — xóa khỏi board"""
        self.board.remove(champion)

    # ==================
    # KIỂM TRA KẾT THÚC
    # ==================

    def _check_winner(self):
        a_alive = any(c.is_alive for c in self.team_a)
        b_alive = any(c.is_alive for c in self.team_b)

        if not a_alive and not b_alive:
            return "draw"
        if not b_alive:
            return "team_a"
        if not a_alive:
            return "team_b"
        return None

    # ==================
    # KẾT QUẢ
    # ==================

    def _build_result(self, winner):
        survivors_a = [c for c in self.team_a if c.is_alive]
        survivors_b = [c for c in self.team_b if c.is_alive]

        return {
            "winner":      winner,
            "duration":    round(self.time, 2),
            "survivors_a": [(c.name, round(c.hp, 1)) for c in survivors_a],
            "survivors_b": [(c.name, round(c.hp, 1)) for c in survivors_b],
            "events":      self.events,
        }