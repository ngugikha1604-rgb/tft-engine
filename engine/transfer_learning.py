# transfer_learning.py
#
# Chuyển weights từ model cũ (OBS nhỏ) sang model mới (OBS lớn hơn).
# Các neuron mới được khởi tạo random, phần cũ giữ nguyên.
#
# Cách dùng trong Colab:
#
#   from transfer_learning import transfer_model
#
#   model_new = transfer_model(
#       old_path  = '/content/drive/MyDrive/tft_models/tft_ppo_1300000_steps.zip',
#       new_env   = vec_env,           # env mới đã tạo
#       old_obs   = 97,                # OBS_SIZE cũ
#       new_obs   = 119,               # OBS_SIZE mới
#       device    = 'cpu',
#   )
#
#   # Train tiếp từ weights cũ
#   model_new.learn(total_timesteps=500_000, ...)

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import VecEnv


def transfer_model(
    old_path: str,
    new_env:  VecEnv,
    old_obs:  int,
    new_obs:  int,
    device:   str = 'cpu',
    verbose:  int = 1,
) -> PPO:
    """
    Load model cũ, tạo model mới với OBS lớn hơn,
    copy weights layer-by-layer. Trả về model mới sẵn sàng train.

    Parameters
    ----------
    old_path : đường dẫn checkpoint cũ (.zip)
    new_env  : VecEnv đã khởi tạo với obs mới
    old_obs  : OBS_SIZE của model cũ
    new_obs  : OBS_SIZE của model mới
    device   : 'cpu' hoặc 'cuda'
    verbose  : 0 = im lặng, 1 = in thông tin
    """

    if verbose:
        print(f"[Transfer] Load model cũ từ: {old_path}")
        print(f"[Transfer] OBS: {old_obs} → {new_obs}")

    # ── 1. Load model cũ ────────────────────────────────────────────
    model_old = PPO.load(old_path, device=device)
    old_state = model_old.policy.state_dict()

    if verbose:
        print(f"[Transfer] Layers trong model cũ:")
        for k, v in old_state.items():
            print(f"  {k}: {v.shape}")

    # ── 2. Tạo model mới ────────────────────────────────────────────
    if verbose:
        print(f"\n[Transfer] Tạo model mới...")

    model_new = PPO(
        'MlpPolicy',
        new_env,
        n_epochs  = model_old.n_epochs,
        ent_coef  = model_old.ent_coef,
        verbose   = 0,
        device    = device,
    )
    new_state = model_new.policy.state_dict()

    if verbose:
        print(f"[Transfer] Layers trong model mới:")
        for k, v in new_state.items():
            print(f"  {k}: {v.shape}")

    # ── 3. Copy weights layer-by-layer ──────────────────────────────
    if verbose:
        print(f"\n[Transfer] Bắt đầu copy weights...")

    transferred = 0
    skipped     = 0
    partial     = 0

    for key in new_state.keys():
        if key not in old_state:
            if verbose:
                print(f"  [SKIP]     {key} — không có trong model cũ")
            skipped += 1
            continue

        old_w = old_state[key]
        new_w = new_state[key]

        if old_w.shape == new_w.shape:
            # Shape giống hệt → copy toàn bộ
            new_state[key] = old_w.clone()
            if verbose:
                print(f"  [COPY]     {key}: {old_w.shape}")
            transferred += 1

        else:
            # Shape khác → copy phần overlap, phần mới giữ random init
            new_w_copy = new_state[key].clone()

            try:
                if len(old_w.shape) == 1:
                    # Bias vector: [N] → [M] với M > N
                    min_dim = min(old_w.shape[0], new_w.shape[0])
                    new_w_copy[:min_dim] = old_w[:min_dim]

                elif len(old_w.shape) == 2:
                    # Weight matrix: [out, in]
                    min_out = min(old_w.shape[0], new_w.shape[0])
                    min_in  = min(old_w.shape[1], new_w.shape[1])
                    new_w_copy[:min_out, :min_in] = old_w[:min_out, :min_in]

                new_state[key] = new_w_copy
                if verbose:
                    print(f"  [PARTIAL]  {key}: {old_w.shape} → {new_w.shape}")
                partial += 1

            except Exception as e:
                if verbose:
                    print(f"  [ERROR]    {key}: {e}")
                skipped += 1

    # ── 4. Load state dict vào model mới ────────────────────────────
    model_new.policy.load_state_dict(new_state)

    if verbose:
        print(f"\n[Transfer] Kết quả:")
        print(f"  ✅ Copy đầy đủ : {transferred} layers")
        print(f"  ⚠️  Copy một phần: {partial} layers")
        print(f"  ❌ Bỏ qua      : {skipped} layers")
        print(f"\n[Transfer] Xong! Model mới sẵn sàng train.")

    return model_new


def save_transfer_checkpoint(model, save_dir, name='tft_ppo_transferred'):
    """Lưu model sau transfer để dùng lại"""
    path = f"{save_dir}/{name}"
    model.save(path)
    print(f"[Transfer] Đã lưu: {path}.zip")
    return path
