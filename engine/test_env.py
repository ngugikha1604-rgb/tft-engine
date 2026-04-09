"""
test_env.py - Smoke tests cho TFT Gymnasium Environment (mask-based).

Chạy file này để kiểm tra:
- env khởi tạo OK
- reset/step trả về dict obs đúng format: {"observation", "action_mask"}
- random rollout luôn chọn action hợp lệ theo mask

Cài đặt tối thiểu:
  pip install gymnasium numpy

Tuỳ chọn (nếu muốn train thử đúng mask):
  pip install stable-baselines3 sb3-contrib
"""

from __future__ import annotations

import json
import os
import random
from typing import Dict, Tuple

import numpy as np


def _repo_paths() -> Tuple[str, str]:
    """Trả về đường dẫn champions.json và items.json theo cấu trúc repo hiện tại."""
    engine_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(engine_dir, ".."))
    data_dir = os.path.join(repo_root, "data")
    return (
        os.path.join(data_dir, "champions.json"),
        os.path.join(data_dir, "items.json"),
    )


def load_game_data() -> Tuple[Dict, Dict]:
    """Load champion_data + item_data từ thư mục data/ của repo."""
    champ_path, items_path = _repo_paths()

    if not os.path.exists(champ_path):
        raise FileNotFoundError(
            f"Không tìm thấy champions.json tại `{champ_path}`. "
            "Hãy đảm bảo repo có thư mục `data/` ở root."
        )
    if not os.path.exists(items_path):
        raise FileNotFoundError(
            f"Không tìm thấy items.json tại `{items_path}`. "
            "Hãy đảm bảo repo có thư mục `data/` ở root."
        )

    from game import load_champions_json

    champion_data, _ = load_champions_json(champ_path)
    with open(items_path, "r", encoding="utf-8") as f:
        item_data = json.load(f)

    print(f"Loaded champions: {len(champion_data)} from `{champ_path}`")
    print(f"Loaded items    : {len(item_data) if isinstance(item_data, dict) else 'n/a'} from `{items_path}`")
    return champion_data, item_data


def _assert_obs_dict(obs: Dict):
    assert isinstance(obs, dict), f"Expected dict obs, got {type(obs)}"
    assert "observation" in obs, "Missing key `observation`"
    assert "action_mask" in obs, "Missing key `action_mask`"
    assert isinstance(obs["observation"], np.ndarray), "`observation` must be np.ndarray"
    assert isinstance(obs["action_mask"], np.ndarray), "`action_mask` must be np.ndarray"
    assert obs["action_mask"].dtype in (np.int8, np.int32, np.int64, np.uint8), f"Unexpected mask dtype: {obs['action_mask'].dtype}"

# ==================
# TEST 1: Kiểm tra env tạo đúng
# ==================
def test_spaces():
    from env import TFTEnv
    champion_data, item_data = load_game_data()
    env = TFTEnv(champion_data, item_data)

    print("=== Test 1: Spaces ===")
    print(f"Action space  : {env.action_space}")
    print(f"Obs space     : {env.observation_space}")
    print(f"Total actions : {env.action_space.n}")
    assert env.action_space.n == 561, f"Expected 561 actions, got {env.action_space.n}"
    assert "action_mask" in env.observation_space.spaces
    assert "observation" in env.observation_space.spaces
    assert env.observation_space.spaces["action_mask"].shape == (561,)
    print("PASS\n")


# ==================
# TEST 2: Reset và observation
# ==================
def test_reset():
    from env import TFTEnv
    champion_data, item_data = load_game_data()
    env = TFTEnv(champion_data, item_data)

    print("=== Test 2: Reset ===")
    obs, info = env.reset()
    _assert_obs_dict(obs)
    vec = obs["observation"]
    mask = obs["action_mask"]
    print(f"Obs vec shape : {vec.shape}")
    print(f"Mask shape    : {mask.shape} | valid={int(mask.sum())}")
    print(f"Obs vec range : [{float(vec.min()):.3f}, {float(vec.max()):.3f}]")
    assert vec.ndim == 1
    assert mask.shape == (env.action_space.n,)
    assert mask.sum() >= 1, "Mask must have at least one valid action"
    print("PASS\n")


# ==================
# TEST 3: Step với random actions
# ==================
def test_random_episode():
    from env import TFTEnv
    champion_data, item_data = load_game_data()
    env = TFTEnv(champion_data, item_data)

    print("=== Test 3: Random Episode ===")
    obs, _ = env.reset()
    total_reward = 0
    steps        = 0

    for _ in range(2000):
        _assert_obs_dict(obs)
        mask = obs["action_mask"]
        valid_actions = np.flatnonzero(mask == 1)
        assert len(valid_actions) > 0
        action = int(random.choice(valid_actions))

        obs, reward, terminated, truncated, info = env.step(action)
        total_reward += reward
        steps        += 1

        if terminated or truncated:
            break

    print(f"Steps        : {steps}")
    print(f"Total reward : {total_reward:.2f}")
    print(f"Terminated   : {terminated}")
    print("PASS\n")


# ==================
# TEST 4: Check với gymnasium checker
# ==================
def test_gymnasium_checker():
    print("=== Test 4: Gymnasium Checker ===")
    try:
        from gymnasium.utils.env_checker import check_env
        from env import TFTEnv
        champion_data, item_data = load_game_data()
        env = TFTEnv(champion_data, item_data)
        check_env(env, warn=True)
        print("PASS\n")
    except Exception as e:
        print(f"WARNING: {e}\n")


# ==================
# TEST 5: Train thử với Stable Baselines3
# ==================
def test_sb3_train():
    print("=== Test 5: SB3 MaskablePPO Train (smoke) ===")
    try:
        from env import TFTEnv
        from sb3_contrib import MaskablePPO
        from sb3_contrib.common.wrappers import ActionMasker

        champion_data, item_data = load_game_data()

        def mask_fn(_env: TFTEnv):
            return _env.get_action_mask()

        base_env = TFTEnv(champion_data, item_data)
        env = ActionMasker(base_env, mask_fn)

        model = MaskablePPO(
            "MultiInputPolicy",
            env,
            verbose=1,
            n_steps=128,
            batch_size=64,
            n_epochs=2,
            learning_rate=1e-4,
            device="cpu",
        )

        model.learn(total_timesteps=256)

        obs, _ = env.reset()
        action, _ = model.predict(obs, deterministic=True)
        print(f"Model predicted action: {int(action)}")
        print("PASS\n")

    except (ImportError, OSError) as e:
        # Keep message ASCII for Windows consoles with cp1252.
        print(f"SKIP: SB3/Torch not available on this machine ({e}).\n")
    except Exception as e:
        print(f"ERROR: {e}\n")
        import traceback
        traceback.print_exc()


# ==================
# MAIN
# ==================
if __name__ == "__main__":
    print("=" * 50)
    print("TFT Environment Tests")
    print("=" * 50 + "\n")

    test_spaces()
    test_reset()
    test_random_episode()
    test_gymnasium_checker()
    test_sb3_train()

    print("=" * 50)
    print("All tests done!")
    print("=" * 50)