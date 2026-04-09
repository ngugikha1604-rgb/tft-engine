"""
test_episode.py - Xem thử 1 trận TFT bằng model đã train (MaskablePPO + action mask).

Chạy:
  python engine/test_episode.py

Yêu cầu:
  pip install gymnasium stable-baselines3 sb3-contrib
  (và PyTorch hoạt động được trên máy bạn)

Checkpoint:
  - Mặc định script sẽ tìm checkpoint mới nhất tên dạng `tft_v4_*.zip`
    trong thư mục:
      - thư mục được set qua biến môi trường `TFT_MODEL_DIR`, hoặc
      - thư mục `models/` ở repo root nếu không set env.
"""

from __future__ import annotations

import glob
import os
from typing import Tuple

import numpy as np


def _repo_root() -> str:
    """Trả về path root của repo (thư mục cha của engine/)."""
    engine_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(engine_dir, ".."))


def _data_paths() -> Tuple[str, str]:
    """Trả về đường dẫn champions.json và items.json theo cấu trúc repo hiện tại."""
    root = _repo_root()
    data_dir = os.path.join(root, "data")
    return (
        os.path.join(data_dir, "champions.json"),
        os.path.join(data_dir, "items.json"),
    )


def load_game_data():
    """Load champion_data + item_data từ thư mục data/ của repo."""
    import json
    from game import load_champions_json

    champ_path, items_path = _data_paths()

    if not os.path.exists(champ_path):
        raise FileNotFoundError(
            f"Cannot find champions.json at `{champ_path}`. "
            "Please ensure `data/champions.json` exists at repo root."
        )
    if not os.path.exists(items_path):
        raise FileNotFoundError(
            f"Cannot find items.json at `{items_path}`. "
            "Please ensure `data/items.json` exists at repo root."
        )

    champion_data, _ = load_champions_json(champ_path)
    with open(items_path, "r", encoding="utf-8") as f:
        item_data = json.load(f)

    print(f"Loaded champions: {len(champion_data)} from `{champ_path}`")
    print(f"Loaded items    : {len(item_data) if isinstance(item_data, dict) else 'n/a'} from `{items_path}`")
    return champion_data, item_data


def find_latest_checkpoint() -> str | None:
    """Tìm checkpoint mới nhất `tft_v4_*.zip` trong thư mục model."""
    # Ưu tiên dùng biến môi trường nếu có
    model_dir = os.environ.get("TFT_MODEL_DIR")
    if model_dir is None or not model_dir.strip():
        model_dir = os.path.join(_repo_root(), "models")

    pattern = os.path.join(model_dir, "tft_v4_*.zip")
    ckpts = sorted(
        glob.glob(pattern),
        key=lambda x: int(x.split("_steps")[0].split("_")[-1])
        if "_steps" in x
        else 0,
    )
    if not ckpts:
        return None
    return ckpts[-1]


def main():
    try:
        from env import TFTEnv
        from sb3_contrib import MaskablePPO
        from sb3_contrib.common.wrappers import ActionMasker
    except ImportError as e:
        print("Missing dependencies for model playback.")
        print("Please install: stable-baselines3, sb3-contrib (and PyTorch).")
        print(f"Details: {e}")
        return

    # Load data
    champion_data, item_data = load_game_data()

    # Find checkpoint
    latest_ckpt = find_latest_checkpoint()
    if latest_ckpt is None:
        print("No checkpoint found (tft_v4_*.zip).")
        print("Set TFT_MODEL_DIR env var or put checkpoints into `models/` at repo root.")
        return

    print(f"Using checkpoint: {latest_ckpt}")

    # Mask function giống trên Colab
    def mask_fn(env: TFTEnv):
        return env.get_action_mask()

    # Env cho test trận: dùng model_bot_path để các bot sử dụng model nếu có
    base_env = TFTEnv(champion_data, item_data, model_bot_path=latest_ckpt)
    env = ActionMasker(base_env, mask_fn)

    # Load model
    try:
        model = MaskablePPO.load(latest_ckpt, device="cpu")
    except Exception as e:
        print("Failed to load MaskablePPO checkpoint:")
        print(e)
        return

    obs, _ = env.reset()
    base = env.env  # unwrap ActionMasker → TFTEnv

    # In encounter nếu có (giữ ASCII để tránh lỗi encoding trên Windows console)
    if getattr(base.game, "_active_encounter", None):
        enc = base.game._active_encounter
        name = enc.get("name", "Unknown")
        desc = enc.get("description", "")
        print(f"Encounter: {name} - {desc}")
        print()

    prev_stage = base.game.stage
    prev_round = base.game.round_num
    print("=== START EPISODE ===\n")

    while True:
        # Predict action
        action, _ = model.predict(obs, deterministic=True)
        action = int(action)

        obs, reward, terminated, truncated, _ = env.step(action)

        # In một số action đặc biệt (carousel/augment) giống Colab
        if 549 <= action <= 557:
            print(f"  Carousel slot picked: {action - 549}")
        if 558 <= action <= 560:
            offers = base.game._augment_offers.get("Agent", [])
            idx = action - 558
            if 0 <= idx < len(offers):
                print(f"  Augment picked: {offers[idx].get('name', 'Unknown')}")

        # Stage/round change → in thông tin board
        cs = base.game.stage
        cr = base.game.round_num
        if cs != prev_stage or cr != prev_round:
            rt = base.game.get_round_type().upper()
            print(f"--- Stage {prev_stage}-{prev_round} [{rt}] ---")
            a = base.agent
            board = [c.name for c in a.get_board_champions()]
            print(
                f"  Agent | HP={a.hp:3d} | Gold={a.econ.gold:3d} | "
                f"Lvl={a.econ.level} | Board({len(board)}): {board}"
            )
            for p in base.game.players[1:]:
                icon = "ALIVE" if p.is_alive else "DEAD "
                print(
                    f"  {icon} {p.name:10s} | HP={p.hp:3d} | "
                    f"Board({len(p.get_board_champions())})"
                )
            print()
            prev_stage, prev_round = cs, cr

        if terminated or truncated:
            print("=== RESULT ===")
            players_sorted = sorted(
                base.game.players, key=lambda x: x.hp, reverse=True
            )
            for p in players_sorted:
                icon = "ALIVE" if p.is_alive else "DEAD "
                board = [c.name for c in p.get_board_champions()]
                print(f"  {icon} {p.name:10s} | HP={p.hp:3d} | Board({len(board)}): {board}")

            placement_list = getattr(base, "_episode_placements", [])
            pl = placement_list[-1] if placement_list else "?"
            print(f"\nAgent final placement: Top {pl}")
            break


if __name__ == "__main__":
    main()

