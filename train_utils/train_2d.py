import numpy as np
import torch
from tqdm import tqdm
from .utils import save_checkpoint
from .losses import LpLoss, darcy_loss, PINO_loss

try:
    import wandb
except ImportError:
    wandb = None


def train_2d_operator(model,
                      train_loader,
                      optimizer, scheduler,
                      config,
                      rank=0, log=False,
                      project='PINO-2d-default',
                      group='default',
                      tags=['default'],
                      use_tqdm=True,
                      profile=False, padding = 4, pde_padding = 4):
    '''
    train PINO on Darcy Flow
    Args:
        model:
        train_loader:
        optimizer:
        scheduler:
        config:
        rank:
        log:
        project:
        group:
        tags:
        use_tqdm:
        profile:

    Returns:

    '''
    if rank == 0 and wandb and log:
        run = wandb.init(project=project,
                         entity=config['log']['entity'],
                         group=group,
                         config=config,
                         tags=tags, reinit=True,
                         settings=wandb.Settings(start_method="fork"))

    data_weight = config['train']['xy_loss']
    f_weight = config['train']['f_loss']
    model.train()
    myloss = LpLoss(size_average=True)
    pbar = range(config['train']['epochs'])
    if use_tqdm:
        pbar = tqdm(pbar, dynamic_ncols=True, smoothing=0.1)
    mesh = train_loader.dataset.mesh
    mollifier = torch.sin(np.pi * mesh[..., 0]) * torch.sin(np.pi * mesh[..., 1]) * 0.001
    mollifier = mollifier.to(rank)
    pde_mesh = train_loader.dataset.pde_mesh
    pde_mollifier = torch.sin(np.pi * pde_mesh[..., 0]) * torch.sin(np.pi * pde_mesh[..., 1]) * 0.001
    pde_mollifier = pde_mollifier.to(rank)
    # padding = 4
    #construct the graph.
    # g = 
    for e in pbar:
        loss_dict = {'train_loss': 0.0,
                     'data_loss': 0.0,
                     'f_loss': 0.0,
                     'test_error': 0.0}
        for x, y, pde_x , pde_y in train_loader:
            x, y = x.to(rank), y.to(rank)
            pde_x, pde_y = pde_x.to(rank), pde_y.to(rank)
            optimizer.zero_grad()
            # GraphLayer
            pred = model(x,padding)
            pred = pred[..., padding:-padding, padding:-padding,:]
            pred = pred.reshape(y.shape)
            pred = pred * mollifier

            data_loss = myloss(pred, y)
            #PDE loss
            if f_weight > 0:
                pred = model(pde_x,pde_padding)
                pred = pred[..., pde_padding:-pde_padding, pde_padding:-pde_padding,:]
                pred = pred.reshape(pde_y.shape)
                pred = pred * pde_mollifier
                a = pde_x[..., 0]
                f_loss = darcy_loss(pred, a)
            else:
                f_loss = torch.zeros(1, device=x.device)

            loss = data_weight * data_loss + f_weight * f_loss
            loss.backward()
            optimizer.step()

            loss_dict['train_loss'] += loss.item() * y.shape[0]
            loss_dict['f_loss'] += f_loss.item() * y.shape[0]
            loss_dict['data_loss'] += data_loss.item() * y.shape[0]

        scheduler.step()
        train_loss_val = loss_dict['train_loss'] / len(train_loader.dataset)
        f_loss_val = loss_dict['f_loss'] / len(train_loader.dataset)
        data_loss_val = loss_dict['data_loss'] / len(train_loader.dataset)

        if use_tqdm:
            pbar.set_description(
                (
                    f'Epoch: {e}, train loss: {train_loss_val:.5f}, '
                    f'f_loss: {f_loss_val:.5f}, '
                    f'data loss: {data_loss_val:.5f}'
                )
            )
        if wandb and log:
            wandb.log(
                {
                    'train loss': train_loss_val,
                    'f loss': f_loss_val,
                    'data loss': data_loss_val
                }
            )
    save_checkpoint(config['train']['save_dir'],
                    config['train']['save_name'],
                    model, optimizer)
    if wandb and log:
        run.finish()
    print('Done!')


def train_2d_burger(model,
                    train_loader,
                    optimizer, scheduler,
                    config,
                    rank=0, log=False,
                    project='PINO-2d-default',
                    group='default',
                    tags=['default'],
                    use_tqdm=True, padding = 2, pde_padding = 2):
    if rank == 0 and wandb and log:
        run = wandb.init(project=project,
                         entity=config['log']['entity'],
                         group=group,
                         config=config,
                         tags=tags, reinit=True,
                         settings=wandb.Settings(start_method="fork"))

    data_weight = config['train']['xy_loss']
    f_weight = config['train']['f_loss']
    ic_weight = config['train']['ic_loss']
    model.train()
    myloss = LpLoss(size_average=True)
    pbar = range(config['train']['epochs'])
    if use_tqdm:
        pbar = tqdm(pbar, dynamic_ncols=True, smoothing=0.1)

    for e in pbar:
        model.train()
        train_pino = 0.0
        data_l2 = 0.0
        train_loss = 0.0

        for x, y , pde_x, pde_y in train_loader:
        # for x, y , _, _ in train_loader:
            x, y = x.to(rank), y.to(rank)
            pde_x, pde_y = pde_x.to(rank), pde_y.to(rank)
            out = model(x,padding)#.reshape(y.shape)
            if padding == 0:
                out = out.reshape(y.shape)
            else:
                out = out[..., padding:-padding, padding:-padding,:].reshape(y.shape)
            data_loss = myloss(out, y)

            pde_out = model(pde_x,pde_padding)
            if pde_padding == 0:
                pde_out = pde_out.reshape(pde_y.shape)
            else:
                pde_out = pde_out[..., pde_padding:-pde_padding, pde_padding:-pde_padding,:].reshape(pde_y.shape)

            loss_u, loss_f = PINO_loss(pde_out, pde_x[:, 0, :, 0])
            total_loss = loss_u * ic_weight + loss_f * f_weight + data_loss * data_weight


            optimizer.zero_grad()
            total_loss.backward()
            optimizer.step()

            data_l2 += data_loss.item()
            train_pino += loss_f.item()
            train_loss += total_loss.item()
        scheduler.step()
        data_l2 /= len(train_loader)
        train_pino /= len(train_loader)
        train_loss /= len(train_loader)
        if use_tqdm:
            pbar.set_description(
                (
                    f'Epoch {e}, train loss: {train_loss:.5f} '
                    f'train f error: {train_pino:.5f}; '
                    f'data l2 error: {data_l2:.5f}'
                )
            )
        if wandb and log:
            wandb.log(
                {
                    'Train f error': train_pino,
                    'Train L2 error': data_l2,
                    'Train loss': train_loss,
                }
            )

        if e % 100 == 0:
            save_checkpoint(config['train']['save_dir'],
                            config['train']['save_name'].replace('.pt', f'_{e}.pt'),
                            model, optimizer)
    save_checkpoint(config['train']['save_dir'],
                    config['train']['save_name'],
                    model, optimizer)
    print('Done!')