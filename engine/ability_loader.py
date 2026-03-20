# ability_loader.py - Load và thực thi abilities từ abilities.json
#
# Cách dùng:
#   from ability_loader import AbilityLoader
#   loader = AbilityLoader('data/abilities.json')
#   loader.cast(champ, simulator)   # gọi trong _cast_ability()

import json
import random


class AbilityLoader:
    """
    Load abilities từ JSON và thực thi trong combat.
    Mỗi ability type có 1 handler riêng.
    """

    def __init__(self, path=None):
        self._data = {}   # name -> ability dict
        if path:
            self.load(path)

    def load(self, path):
        """Load abilities.json"""
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        self._data = {k: v for k, v in raw.items() if not k.startswith('_')}
        print(f"[AbilityLoader] Loaded {len(self._data)} abilities")

    def has(self, champ_name):
        """Kiểm tra tướng có ability không"""
        return champ_name in self._data

    def cast(self, champ, simulator):
        """
        Thực thi ability của champion.
        Trả về True nếu cast thành công, False nếu không có ability.
        """
        data = self._data.get(champ.name)
        if not data:
            return False

        ab_type = data.get('type', 'single_damage')
        handler = self._HANDLERS.get(ab_type)
        if not handler:
            return False

        handler(self, champ, data, simulator)
        return True

    # ==================
    # HELPERS
    # ==================

    def _get_enemies(self, champ, simulator):
        """Lấy danh sách địch còn sống"""
        enemies = simulator.team_b if champ in simulator.team_a else simulator.team_a
        return [e for e in enemies if e.is_alive]

    def _get_allies(self, champ, simulator):
        """Lấy danh sách đồng đội còn sống (không tính chính mình)"""
        team = simulator.team_a if champ in simulator.team_a else simulator.team_b
        return [a for a in team if a.is_alive and a is not champ]

    def _nearest_enemy(self, champ, simulator):
        """Tìm địch gần nhất"""
        enemies = self._get_enemies(champ, simulator)
        if not enemies:
            return None
        if champ.position is None:
            return enemies[0]
        return min(enemies, key=lambda e: (
            simulator.board.hex_distance(champ.position, e.position)
            if e.position else 999
        ))

    def _farthest_enemy(self, champ, simulator):
        """Tìm địch xa nhất"""
        enemies = self._get_enemies(champ, simulator)
        if not enemies:
            return None
        if champ.position is None:
            return enemies[-1]
        return max(enemies, key=lambda e: (
            simulator.board.hex_distance(champ.position, e.position)
            if e.position else 0
        ))

    def _lowest_hp_ally(self, champ, simulator):
        """Tìm đồng đội ít máu nhất"""
        allies = self._get_allies(champ, simulator)
        if not allies:
            return None
        return min(allies, key=lambda a: a.hp / a.max_hp if a.max_hp > 0 else 1)

    def _enemies_in_radius(self, champ, center, radius, simulator):
        """Lấy địch trong vùng radius hex"""
        enemies = self._get_enemies(champ, simulator)
        if center is None or center.position is None:
            return enemies[:1]
        return [
            e for e in enemies
            if e.position and simulator.board.hex_distance(center.position, e.position) <= radius
        ]

    def _calc_damage(self, champ, data):
        """Tính damage từ ability data"""
        ap_ratio    = getattr(champ, 'ability_power', 100) / 100.0
        damage_pct  = data.get('damage_pct', 1.0)
        damage_type = data.get('damage_type', 'magic')
        base        = champ.ad * ap_ratio * damage_pct
        return base, damage_type

    def _deal_damage(self, champ, target, amount, damage_type, simulator):
        """Gây damage và log event"""
        actual = target.take_damage(
            amount,
            damage_type      = damage_type,
            damage_amp_bonus = champ.damage_amp
        ) or 0.0
        simulator.events.append({
            'time': simulator.time, 'type': 'ability',
            'source': champ.name, 'target': target.name,
            'value': actual, 'damage_type': damage_type
        })
        if not target.is_alive:
            simulator._on_death(target)
        return actual

    # ==================
    # ABILITY HANDLERS
    # ==================

    def _handle_single_damage(self, champ, data, simulator):
        """Jhin — damage 1 target"""
        target = (self._farthest_enemy(champ, simulator)
                  if data.get('target') == 'farthest_enemy'
                  else self._nearest_enemy(champ, simulator))
        if not target:
            return
        dmg, dtype = self._calc_damage(champ, data)
        self._deal_damage(champ, target, dmg, dtype, simulator)

    def _handle_aoe_damage(self, champ, data, simulator):
        """Yasuo — damage vùng"""
        center = self._nearest_enemy(champ, simulator)
        if not center:
            return
        radius  = data.get('radius', 2)
        targets = self._enemies_in_radius(champ, center, radius, simulator)
        dmg, dtype = self._calc_damage(champ, data)
        for t in targets:
            if t.is_alive:
                self._deal_damage(champ, t, dmg, dtype, simulator)

    def _handle_multi_hit(self, champ, data, simulator):
        """Aphelios — đánh nhiều lần"""
        target = self._nearest_enemy(champ, simulator)
        if not target:
            return
        hits        = data.get('hits', 3)
        dmg, dtype  = self._calc_damage(champ, data)
        for _ in range(hits):
            if target.is_alive:
                self._deal_damage(champ, target, dmg, dtype, simulator)

    def _handle_invulnerable_then_aoe(self, champ, data, simulator):
        """Gwen — vô hiệu hóa rồi AOE"""
        # Set invulnerable (dùng shield tạm thời)
        duration = data.get('invulnerable_duration', 2.0)
        champ.add_shield(champ.max_hp * 10, duration)  # shield cực lớn = vô hiệu hóa

        # AOE damage ngay lập tức
        center  = self._nearest_enemy(champ, simulator)
        if not center:
            return
        radius  = data.get('radius', 2)
        targets = self._enemies_in_radius(champ, center, radius, simulator)
        dmg, dtype = self._calc_damage(champ, data)
        for t in targets:
            if t.is_alive:
                self._deal_damage(champ, t, dmg, dtype, simulator)

    def _handle_bounce_damage(self, champ, data, simulator):
        """Draven — damage bật lại"""
        enemies = self._get_enemies(champ, simulator)
        if not enemies:
            return
        target1 = self._nearest_enemy(champ, simulator)
        if not target1:
            return

        # Hit 1
        ap_ratio   = getattr(champ, 'ability_power', 100) / 100.0
        dtype      = data.get('damage_type', 'physical')
        dmg1       = champ.ad * ap_ratio * data.get('damage_pct', 3.5)
        self._deal_damage(champ, target1, dmg1, dtype, simulator)

        # Hit 2 — target khác còn sống
        others = [e for e in enemies if e.is_alive and e is not target1]
        if others:
            target2 = random.choice(others)
            dmg2    = champ.ad * ap_ratio * data.get('bounce_damage_pct', 2.0)
            self._deal_damage(champ, target2, dmg2, dtype, simulator)

    def _handle_aoe_damage_debuff(self, champ, data, simulator):
        """Lissandra — AOE + stun (stun dùng mana_lock_timer)"""
        center  = self._nearest_enemy(champ, simulator)
        if not center:
            return
        radius   = data.get('radius', 2)
        targets  = self._enemies_in_radius(champ, center, radius, simulator)
        dmg, dtype = self._calc_damage(champ, data)
        stun_dur = data.get('stun_duration', 1.5)
        for t in targets:
            if t.is_alive:
                self._deal_damage(champ, t, dmg, dtype, simulator)
                # Stun — dùng mana_lock_timer để block attack
                t.mana_lock_timer = max(t.mana_lock_timer, stun_dur)
                t.attack_timer    = max(t.attack_timer, stun_dur)

    def _handle_buff_ally(self, champ, data, simulator):
        """Bel'Veth — buff đồng đội"""
        allies   = self._get_allies(champ, simulator)
        stat     = data.get('buff_stat', 'attack_speed')
        value    = data.get('buff_value', 0.4)
        duration = data.get('buff_duration', 3.0)

        # Apply buff ngay lập tức (đơn giản — không revert sau duration)
        # TODO: implement buff system với revert nếu cần
        for ally in allies:
            if hasattr(ally, stat):
                current = getattr(ally, stat)
                setattr(ally, stat, current * (1 + value))

    def _handle_aoe_damage_sunder(self, champ, data, simulator):
        """Ziggs — AOE + giảm giáp"""
        center   = self._nearest_enemy(champ, simulator)
        if not center:
            return
        radius   = data.get('radius', 3)
        targets  = self._enemies_in_radius(champ, center, radius, simulator)
        dmg, dtype = self._calc_damage(champ, data)
        sunder   = data.get('sunder_pct', 0.3)
        for t in targets:
            if t.is_alive:
                self._deal_damage(champ, t, dmg, dtype, simulator)
                if t.is_alive:
                    t.armor = max(0, t.armor * (1 - sunder))

    def _handle_shield_ally(self, champ, data, simulator):
        """Thresh — shield toàn đội"""
        allies    = [champ] + self._get_allies(champ, simulator)
        shield_pct = data.get('shield_pct', 0.25)
        duration  = data.get('shield_duration', 3.0)
        for ally in allies:
            if ally.is_alive:
                shield_amount = ally.max_hp * shield_pct
                ally.add_shield(shield_amount, duration)

    def _handle_heal_ally(self, champ, data, simulator):
        """Sona — hồi máu đồng đội"""
        target   = self._lowest_hp_ally(champ, simulator)
        if not target:
            target = champ  # Tự hồi nếu không có đồng đội
        heal_pct = data.get('heal_pct', 0.2)
        amount   = target.max_hp * heal_pct
        target.heal(amount)
        simulator.events.append({
            'time': simulator.time, 'type': 'heal',
            'source': champ.name, 'target': target.name, 'value': amount
        })

    # Map type → handler
    _HANDLERS = {
        'single_damage'        : _handle_single_damage,
        'aoe_damage'           : _handle_aoe_damage,
        'multi_hit'            : _handle_multi_hit,
        'invulnerable_then_aoe': _handle_invulnerable_then_aoe,
        'bounce_damage'        : _handle_bounce_damage,
        'aoe_damage_debuff'    : _handle_aoe_damage_debuff,
        'buff_ally'            : _handle_buff_ally,
        'aoe_damage_sunder'    : _handle_aoe_damage_sunder,
        'shield_ally'          : _handle_shield_ally,
        'heal_ally'            : _handle_heal_ally,
    }