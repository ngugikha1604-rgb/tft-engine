# transfer_learning.py
#
# Dùng MaskablePPO (sb3-contrib) cho env có action masking.
# Vì policy thay đổi hoàn toàn (MlpPolicy → MultiInputPolicy),
# không thể transfer từ model cũ — phải train lại từ đầu.
#
# Cách dùng trong Colab:
#
#   from transfer_learning import make_new_model, transfer_model
#
#   # Train mới hoàn toàn
#   model = make_new_model(vec_env, device='cpu')
#
#   # Hoặc transfer từ checkpoint MaskablePPO cũ (cùng policy)
#   model = transfer_model(
#       old_path = '/content/drive/MyDrive/tft_models/tft_ppo_v3_500000_steps.zip',
#       new_env  = vec_env,
#       old_obs  = 260,
#       new_obs  = 260,   # nếu OBS không đổi thì copy 100%
#       device   = 'cpu',
#   )

import torch
from sb3_contrib import MaskablePPO
from stable_baselines3.common.vec_env import VecEnv


def make_new_model(env, device='cpu', verbose=1):
    """
    Tạo MaskablePPO mới từ đầu.
    Dùng khi lần đầu train hoặc khi policy thay đổi.
    """
    model = MaskablePPO(
        'MultiInputPolicy',
        env,
        n_epochs    = 15,
        ent_coef    = 0.02,
        verbose     = verbose,
        device      = device,
        policy_kwargs = dict(net_arch=[256, 256]),
    )
    if verbose:
        print("[Model] Tạo MaskablePPO mới với MultiInputPolicy")
    return model


def transfer_model(
    old_path: str,
    new_env:  VecEnv,
    old_obs:  int,
    new_obs:  int,
    device:   str = 'cpu',
    verbose:  int = 1,
) -> MaskablePPO:
    """
    Transfer weights từ checkpoint MaskablePPO cũ sang model mới.
    Chỉ dùng được khi cả 2 đều dùng MaskablePPO + MultiInputPolicy.

    Nếu old_obs == new_obs thì copy 100% weights.
    Nếu khác thì copy phần overlap, phần mới random init.
    """
    if verbose:
        print(f"[Transfer] Load checkpoint: {old_path}")
        print(f"[Transfer] OBS: {old_obs} → {new_obs}")

    # Load model cũ (không cần env)
    model_old = MaskablePPO.load(old_path, device=device)
    old_state = model_old.policy.state_dict()

    if verbose:
        print("[Transfer] Layers model cũ:")
        for k, v in old_state.items():
            print(f"  {k}: {tuple(v.shape)}")

    # Tạo model mới
    model_new = make_new_model(new_env, device=device, verbose=0)
    new_state = model_new.policy.state_dict()

    if verbose:
        print("\n[Transfer] Bắt đầu copy weights...")

    transferred = skipped = partial = 0

    for key in new_state.keys():
        if key not in old_state:
            if verbose:
                print(f"  [SKIP]    {key} — không có trong model cũ")
            skipped += 1
            continue

        old_w = old_state[key]
        new_w = new_state[key]

        if old_w.shape == new_w.shape:
            new_state[key] = old_w.clone()
            if verbose:
                print(f"  [COPY]    {key}: {tuple(old_w.shape)}")
            transferred += 1
        else:
            new_w_copy = new_state[key].clone()
            try:
                if len(old_w.shape) == 1:
                    m = min(old_w.shape[0], new_w.shape[0])
                    new_w_copy[:m] = old_w[:m]
                elif len(old_w.shape) == 2:
                    m_out = min(old_w.shape[0], new_w.shape[0])
                    m_in  = min(old_w.shape[1], new_w.shape[1])
                    new_w_copy[:m_out, :m_in] = old_w[:m_out, :m_in]
                new_state[key] = new_w_copy
                if verbose:
                    print(f"  [PARTIAL] {key}: {tuple(old_w.shape)} → {tuple(new_w.shape)}")
                partial += 1
            except Exception as e:
                if verbose:
                    print(f"  [ERROR]   {key}: {e}")
                skipped += 1

    model_new.policy.load_state_dict(new_state)

    if verbose:
        print(f"\n[Transfer] Kết quả:")
        print(f"  ✅ Copy đầy đủ  : {transferred} layers")
        print(f"  ⚠️  Copy một phần: {partial} layers")
        print(f"  ❌ Bỏ qua       : {skipped} layers")
        print(f"[Transfer] Xong! Model sẵn sàng train.")

    return model_new


def save_transfer_checkpoint(model, save_dir, name='tft_maskable_transferred'):
    """Lưu model sau transfer"""
    path = f"{save_dir}/{name}"
    model.save(path)
    print(f"[Transfer] Đã lưu: {path}.zip")
    return path