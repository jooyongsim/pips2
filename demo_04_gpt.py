import time
import numpy as np
import pandas as pd
import saverloader
from nets.pips2 import Pips
import utils.improc
from utils.basic import print_, print_stats
import torch
from tensorboardX import SummaryWriter
import torch.nn.functional as F
from fire import Fire
import sys
import cv2
from pathlib import Path

def read_mp4(fn):
    vidcap = cv2.VideoCapture(fn)
    frames = []
    while vidcap.isOpened():
        ret, frame = vidcap.read()
        if not ret:
            break
        frames.append(frame)
    vidcap.release()
    return frames

def read_points_of_interest(csv_path):
    df = pd.read_csv(csv_path)
    points = df[['x', 'y']].to_numpy()
    return points

def run_model(model, rgbs, points_of_interest, S_max=128, iters=16, sw=None):
    rgbs = rgbs.cuda().float()  # B, S, C, H, W

    B, S, C, H, W = rgbs.shape
    assert B == 1

    xy0 = torch.tensor(points_of_interest, dtype=torch.float32).unsqueeze(0).cuda()  # B, N, 2

    # zero-vel init
    trajs_e = xy0.unsqueeze(1).repeat(1, S, 1, 1)

    iter_start_time = time.time()
    
    preds, preds_anim, _, _ = model(trajs_e, rgbs, iters=iters, feat_init=None, beautify=True)
    trajs_e = preds[-1]

    iter_time = time.time() - iter_start_time
    print('inference time: %.2f seconds (%.1f fps)' % (iter_time, S / iter_time))

    if sw is not None and sw.save_this:
        rgbs_prep = utils.improc.preprocess_color(rgbs)
        sw.summ_traj2ds_on_rgbs('outputs/trajs_on_rgbs', trajs_e[0:1], rgbs_prep[0:1], cmap='hot', linewidth=1, show_dots=False)
    return trajs_e

def main(
        filename='./tracking_sample.mp4',
        points_csv_path='image0.csv',
        S=48,  # seqlen
        timestride=1, # temporal stride of the model
        iters=16,  # inference steps of the model
        image_size=(512, 896),  # input resolution
        max_iters=4,  # number of clips to run
        log_freq=1,  # how often to make image summaries
        log_dir='./logs_demo',
        init_dir='./reference_model',
        device_ids=[0],
):
    print('filename', filename)
    name = Path(filename).stem
    print('name', name)

    rgbs = read_mp4(filename)
    rgbs = np.stack(rgbs, axis=0)  # S,H,W,3
    rgbs = rgbs[..., ::-1].copy()  # BGR->RGB
    rgbs = rgbs[::timestride]
    S_here, H, W, C = rgbs.shape
    print('rgbs', rgbs.shape)

    model_name = f"{name}_{S}_demo_{time.strftime('%Y%m%d-%H%M%S')}"
    print('model_name', model_name)

    writer_t = SummaryWriter(f'{log_dir}/{model_name}/t', max_queue=10, flush_secs=60)

    model = Pips(stride=8).cuda()
    if init_dir:
        _ = saverloader.load(init_dir, model)
    model.eval()

    points_of_interest = read_points_of_interest(points_csv_path)  # Read points from CSV

    idx = list(range(0, max(S_here - S, 1), S))
    if max_iters:
        idx = idx[:max_iters]
    
    for si in idx:
        global_step += 1
        
        iter_start_time = time.time()

        sw_t = utils.improc.Summ_writer(
            writer=writer_t,
            global_step=global_step,
            log_freq=log_freq,
            fps=16,
            scalar_freq=int(log_freq/2),
            just_gif=True)

        rgb_seq = rgbs[si:si+S]
        rgb_seq = torch.from_numpy(rgb_seq).permute(0,3,1,2).to(torch.float32) # S,3,H,W
        rgb_seq = F.interpolate(rgb_seq, image_size, mode='bilinear').unsqueeze(0) # 1,S,3,H,W
        
        with torch.no_grad():
            trajs_e = run_model(model, rgb_seq, S_max=S, N=N, iters=iters, sw=sw_t)

        iter_time = time.time()-iter_start_time
        
        print('%s; step %06d/%d; itime %.2f' % (
            model_name, global_step, max_iters, iter_time))
        
    # After the loop in the main function
    final_trajectories = trajs_e.squeeze().cpu().numpy()  # Assuming trajs_e shape is (1, S, N, 2)
    final_df = pd.DataFrame(final_trajectories.reshape(-1, 2), columns=['x', 'y'])
    final_df.to_csv('save_trajectory.csv', index=False)
            
    writer_t.close()

if __name__ == '__main__':
    Fire(main)
