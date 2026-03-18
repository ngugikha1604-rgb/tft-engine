# logger.py - Hệ thống ghi log cho TFT Engine
#
# Có 2 chế độ:
#   TRAIN mode  : Ghi nhẹ mỗi game (placement, reward, rounds...)
#   REPLAY mode : Ghi đầy đủ mọi thứ để xem lại sau
#
# Cách dùng:
#   from logger import TFTLogger
#
#   logger = TFTLogger(log_dir='logs', mode='train')
#   logger.on_episode_start(episode=1)
#   logger.on_round_end(round_data)
#   logger.on_episode_end(episode_data)
#   logger.save()

import os
import json
from datetime import datetime


# ==================
# TFT LOGGER
# ==================

class TFTLogger:
    """
    Logger cho TFT Engine.

    Parameters
    ----------
    log_dir : str
        Thư mục chứa log. Tự tạo nếu chưa có.
    mode : str
        'train'  — ghi nhẹ, không ảnh hưởng tốc độ train
        'replay' — ghi đầy đủ để xem lại
    max_train_logs : int
        Giới hạn số game lưu trong train mode (tránh file quá lớn)
    """

    def __init__(self, log_dir='logs', mode='train', max_train_logs=10000):
        self.log_dir        = log_dir
        self.mode           = mode
        self.max_train_logs = max_train_logs

        # Tạo thư mục nếu chưa có
        os.makedirs(log_dir, exist_ok=True)
        if mode == 'replay':
            os.makedirs(os.path.join(log_dir, 'replays'), exist_ok=True)

        # Buffer lưu data trước khi ghi ra file
        self._train_logs    = []    # list các game stats (train mode)
        self._current_game  = None  # game đang chạy (replay mode)
        self._episode_count = 0

        # Load train logs cũ nếu có
        if mode == 'train':
            self._load_train_logs()

    # ==================
    # TRAIN MODE
    # ==================

    def on_episode_start(self, episode):
        """Gọi khi bắt đầu 1 episode mới"""
        self._episode_count = episode

        if self.mode == 'replay':
            self._current_game = {
                "episode"   : episode,
                "timestamp" : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "rounds"    : [],
            }

    def on_round_end(self, stage, round_num, board_champs, gold, hp, placement):
        """
        Gọi sau mỗi round kết thúc.

        Parameters
        ----------
        stage       : int  — stage hiện tại (2, 3, 4...)
        round_num   : int  — round trong stage (1, 2, 3...)
        board_champs: list — tên các tướng trên board
        gold        : int  — gold hiện tại
        hp          : int  — HP hiện tại
        placement   : int  — vị trí hiện tại (1-8)
        """
        if self.mode != 'replay' or self._current_game is None:
            return

        self._current_game["rounds"].append({
            "stage"     : stage,
            "round"     : round_num,
            "board"     : board_champs,
            "gold"      : gold,
            "hp"        : hp,
            "placement" : placement,
        })

    def on_episode_end(self, placement, total_reward, rounds_survived, final_hp):
        """
        Gọi khi kết thúc 1 episode.

        Parameters
        ----------
        placement      : int   — vị trí cuối game (1-8)
        total_reward   : float — tổng reward của episode
        rounds_survived: int   — số rounds sống được
        final_hp       : int   — HP cuối game
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if self.mode == 'train':
            # Ghi nhẹ vào buffer
            self._train_logs.append({
                "episode"        : self._episode_count,
                "placement"      : placement,
                "total_reward"   : round(total_reward, 2),
                "rounds_survived": rounds_survived,
                "final_hp"       : final_hp,
                "timestamp"      : timestamp,
            })

            # Giới hạn số log
            if len(self._train_logs) > self.max_train_logs:
                self._train_logs = self._train_logs[-self.max_train_logs:]

            # Auto-save mỗi 100 games
            if self._episode_count % 10 == 0:
                self.save()

        elif self.mode == 'replay' and self._current_game is not None:
            # Hoàn thiện game data
            self._current_game["placement"]       = placement
            self._current_game["total_reward"]    = round(total_reward, 2)
            self._current_game["rounds_survived"] = rounds_survived
            self._current_game["final_hp"]        = final_hp

            # Lưu replay ra file riêng
            replay_path = os.path.join(
                self.log_dir, 'replays',
                f'game_{self._episode_count:06d}.json'
            )
            with open(replay_path, 'w', encoding='utf-8') as f:
                json.dump(self._current_game, f, indent=2, ensure_ascii=False)

            self._current_game = None

    # ==================
    # SAVE / LOAD
    # ==================

    def save(self):
        """Lưu train logs ra file"""
        if self.mode != 'train':
            return
        path = os.path.join(self.log_dir, 'train_stats.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self._train_logs, f, indent=2, ensure_ascii=False)

    def _load_train_logs(self):
        """Load train logs cũ nếu có"""
        path = os.path.join(self.log_dir, 'train_stats.json')
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self._train_logs = json.load(f)
            except Exception:
                self._train_logs = []

    # ==================
    # ANALYSIS
    # ==================

    def get_stats(self, last_n=None):
        """
        Tính thống kê từ train logs.

        Parameters
        ----------
        last_n : int hoặc None
            Tính trên N games gần nhất. None = tất cả.

        Returns
        -------
        dict chứa các thống kê
        """
        logs = self._train_logs
        if last_n:
            logs = logs[-last_n:]
        if not logs:
            return {}

        placements     = [g["placement"] for g in logs]
        rewards        = [g["total_reward"] for g in logs]
        rounds         = [g["rounds_survived"] for g in logs]
        n              = len(logs)

        return {
            "n_games"       : n,
            "top1_rate"     : round(placements.count(1) / n * 100, 1),
            "top4_rate"     : round(sum(1 for p in placements if p <= 4) / n * 100, 1),
            "avg_placement" : round(sum(placements) / n, 2),
            "avg_reward"    : round(sum(rewards) / n, 2),
            "avg_rounds"    : round(sum(rounds) / n, 1),
            "placement_dist": {
                f"top{i}": placements.count(i)
                for i in range(1, 9)
                if placements.count(i) > 0
            },
        }

    def print_stats(self, last_n=50):
        """In thống kê ra terminal"""
        stats = self.get_stats(last_n)
        if not stats:
            print("Chưa có data!")
            return

        sep = "=" * 50
        print(f"\n{sep}")
        print(f"[Logger] Stats (last {stats['n_games']} games):")
        print(f"  Top1      : {stats['top1_rate']}%")
        print(f"  Top4      : {stats['top4_rate']}%")
        print(f"  Avg place : {stats['avg_placement']}")
        print(f"  Avg reward: {stats['avg_reward']}")
        print(f"  Avg rounds: {stats['avg_rounds']}")
        dist_str = " | ".join(
            f"Top{k[3:]}:{v}" for k, v in stats['placement_dist'].items()
        )
        print(f"  Dist      : {dist_str}")
        print(f"{sep}\n")

    def load_replay(self, episode):
        """
        Load replay của 1 game cụ thể.

        Parameters
        ----------
        episode : int — số episode muốn xem

        Returns
        -------
        dict chứa toàn bộ data của game đó
        """
        path = os.path.join(
            self.log_dir, 'replays',
            f'game_{episode:06d}.json'
        )
        if not os.path.exists(path):
            print(f"Không tìm thấy replay episode {episode}")
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def list_replays(self):
        """Liệt kê tất cả replays có sẵn"""
        replay_dir = os.path.join(self.log_dir, 'replays')
        if not os.path.exists(replay_dir):
            return []
        files = sorted(os.listdir(replay_dir))
        return [f.replace('.json', '') for f in files if f.endswith('.json')]