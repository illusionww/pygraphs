import numpy as np
import torch


def torch_func(func):
    def wrapper(*args, device=1, **kwargs):
        with torch.no_grad():
            args = [torch.from_numpy(x).float().to(device)
                    if type(x) in [np.ndarray, np.memmap] and x.dtype in [np.float32, np.float64] else x
                    for x in args]
            results = func(*args, **kwargs)
            results = [x.numpy() if type(x) == torch.tensor else x for x in results]
        return results

    return wrapper


def _hKh(hk, ei, K):
    hk_ei = hk - ei
    return torch.einsum('i,ij,j->', [hk_ei, K, hk_ei])

def _inertia(h, e, K, labels):
    h_e = h[labels] - e
    return torch.einsum('ij,jk,ki->', [h_e, K, h_e])

@torch_func
def _vanilla_predict(K, h, max_iter: int, device=1):
    n_clusters, n = h.shape
    e = torch.eye(n, dtype=torch.float32).to(device)

    labels, success = np.zeros((n,), dtype=np.uint8), True
    for iter in range(max_iter):
        h_e = h.unsqueeze(1) - e.unsqueeze(0)  # [k, n, n]
        l = torch.argmin(torch.einsum('kni,ij,knj->kn', [h_e, K, h_e]), dim=0)
        if torch.all(labels == l):  # early stop
            break
        labels = l

        U = torch.zeros((n, n_clusters), dtype=torch.float32).to(device)
        U[range(n), labels] = 1
        nn = U.sum(dim=0, keepdim=True)
        if torch.any(nn == 0):  # empty cluster! exit with success=False
            success = False
            break
        h = (U / nn).transpose(0, 1)

    inertia = _inertia(h, e, K, labels)
    return labels, inertia, success


@torch_func
def _iterative_predict(K, h, U, l, nn, max_iter: int, eps: float, device=1):
    n_clusters, n = h.shape
    e = torch.eye(n, dtype=torch.float32).to(device)

    labels = l.copy()
    for _ in range(max_iter):
        node_order = np.array(list(range(n)))
        np.random.shuffle(node_order)
        for i in node_order:  # for each node
            h_ei = h - e[i][None]
            ΔJ1 = nn / (nn + 1 + eps) * torch.einsum('ki,ij,kj->k', [h_ei, K, h_ei])
            k_star = torch.argmin(ΔJ1)
            ΔJ2 = nn[l[i]] / (nn[l[i]] - 1 + eps) * _hKh(h[l[i]], e[i], K)
            minΔJ = ΔJ1[k_star] - ΔJ2
            if minΔJ < 0 and l[i] != k_star:
                if nn[l[i]] == 1:  # it will cause empty cluster! exit with success=False
                    inertia = _inertia(h, e, K, labels)
                    return labels, inertia, False
                h[l[i]] = 1. / (nn[l[i]] - 1 + eps) * (nn[l[i]] * h[l[i]] - e[i])
                h[k_star] = 1. / (nn[k_star] + 1 + eps) * (nn[k_star] * h[k_star] + e[i])
                U[i, l[i]], U[i, k_star] = 0, 1
                nn[l[i]], nn[k_star] = nn[l[i]] - 1, nn[k_star] + 1
                l[i] = k_star

        if np.all(labels == l):  # early stop
            break
        labels = l.copy()

    inertia = _inertia(h, e, K, labels)
    return labels, inertia, ~np.isnan(inertia)
