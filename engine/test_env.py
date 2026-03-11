# test_env.py - Test TFT Gymnasium Environment
# Chạy file này trên máy bạn sau khi cài gymnasium
#
# pip install gymnasium stable-baselines3
# python test_env.py

import numpy as np

# ==================
# Champion data mẫu (thay bằng champions.json thật sau)
# ==================
import json, os

# Load từ champions.json nếu có, fallback về data mẫu
_json_path = os.path.join(os.path.dirname(__file__), "data", "champions.json")
if os.path.exists(_json_path):
    from game import load_champions_json
    CHAMPION_DATA, _ = load_champions_json(_json_path)
    print(f"Loaded {len(CHAMPION_DATA)} champions from champions.json")
else:
    print("champions.json not found, using placeholder data")
    CHAMPION_DATA = {
        "Jinx":   3, "Leona":  2, "Zed":    3, "Garen":  1,
        "Lux":    4, "Ahri":   3, "Darius": 2, "Yasuo":  1,
        "Kaisa":  4, "Senna":  5, "Teemo":  1, "Annie":  2,
        "Syndra": 3, "Janna":  1, "Ekko":   4,
    }

# ==================
# TEST 1: Kiểm tra env tạo đúng
# ==================
def test_spaces():
    from env import TFTEnv
    env = TFTEnv(CHAMPION_DATA)

    print("=== Test 1: Spaces ===")
    print(f"Action space  : {env.action_space}")
    print(f"Obs space     : {env.observation_space}")
    print(f"Obs shape     : {env.observation_space.shape}")
    print(f"Total actions : {env.action_space.n}")
    assert env.observation_space.shape == (97,), f"Expected (97,), got {env.observation_space.shape}"
    # ACTION_PASS = 16 + 9*28 = 268, TOTAL_ACTIONS = 269
    assert env.action_space.n == 269, f"Expected 269, got {env.action_space.n}"
    print("PASS\n")


# ==================
# TEST 2: Reset và observation
# ==================
def test_reset():
    from env import TFTEnv
    env = TFTEnv(CHAMPION_DATA)

    print("=== Test 2: Reset ===")
    obs, info = env.reset()
    print(f"Obs shape  : {obs.shape}")
    print(f"Obs range  : [{obs.min():.2f}, {obs.max():.2f}]")
    print(f"Obs sample : {obs[:6]}")   # player state
    assert obs.shape == (97,)
    assert obs.min() >= 0.0
    assert obs.max() <= 1.0
    print("PASS\n")


# ==================
# TEST 3: Step với random actions
# ==================
def test_random_episode():
    from env import TFTEnv
    env = TFTEnv(CHAMPION_DATA)

    print("=== Test 3: Random Episode ===")
    obs, _ = env.reset()
    total_reward = 0
    steps        = 0

    for _ in range(500):
        action = env.action_space.sample()
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
        env = TFTEnv(CHAMPION_DATA)
        check_env(env, warn=True)
        print("PASS\n")
    except Exception as e:
        print(f"WARNING: {e}\n")


# ==================
# TEST 5: Train thử với Stable Baselines3
# ==================
def test_sb3_train():
    print("=== Test 5: SB3 PPO Train (1000 steps) ===")
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.env_util import make_vec_env
        from env import TFTEnv

        # Tạo env
        env = TFTEnv(CHAMPION_DATA)

        # Tạo PPO model
        model = PPO(
            "MlpPolicy",
            env,
            verbose=1,
            n_steps=64,
            batch_size=32,
            n_epochs=4,
            learning_rate=3e-4,
        )

        # Train 1000 steps (chỉ để test chạy được, không đủ để học)
        model.learn(total_timesteps=1000)

        # Test predict
        obs, _ = env.reset()
        action, _ = model.predict(obs, deterministic=True)
        print(f"Model predicted action: {action}")
        print("PASS\n")

    except ImportError:
        print("stable-baselines3 chưa cài: pip install stable-baselines3\n")
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