import math
import torch
from torch_geometric.utils import to_undirected, negative_sampling

from privacy import available_mechanisms


class Privatize:
    def __init__(self, method, eps, **kwargs):
        self.method = method
        self.eps = eps
        self.kwargs = kwargs

    def __call__(self, data):
        if self.method == 'raw':
            if hasattr(data, 'x_raw'):
                data.x = data.x_raw  # bring back x_raw
        else:
            if not hasattr(data, 'x_raw'):
                data.x_raw = data.x  # save original x to x_raw
            data.x = available_mechanisms[self.method](eps=self.eps, **self.kwargs)(data.x_raw)
        return data


class NodeSplit:
    def __init__(self, val_ratio=.25, test_ratio=.25, rng=None):
        self.val_ratio = val_ratio
        self.test_ratio = test_ratio
        self.rng = rng

    def __call__(self, data):
        num_nodes_with_class = data.num_nodes
        nodes_with_class = torch.ones(data.num_nodes, dtype=torch.bool)

        if hasattr(data, 'y') and -1 in data.y:
            nodes_with_class = data.y != -1
            num_nodes_with_class = nodes_with_class.sum().item()

        n_val = int(self.val_ratio * num_nodes_with_class)
        n_test = int(self.test_ratio * num_nodes_with_class)
        perm = torch.randperm(num_nodes_with_class, generator=self.rng)

        val_nodes = perm[:n_val]
        test_nodes = perm[n_val:n_val + n_test]
        train_nodes = perm[n_val + n_test:]

        temp_val_mask = torch.zeros(num_nodes_with_class, dtype=torch.bool)
        temp_val_mask[val_nodes] = True

        temp_test_mask = torch.zeros(num_nodes_with_class, dtype=torch.bool)
        temp_test_mask[test_nodes] = True

        temp_train_mask = torch.zeros(num_nodes_with_class, dtype=torch.bool)
        temp_train_mask[train_nodes] = True

        val_mask = torch.zeros(data.num_nodes, dtype=torch.bool)
        test_mask = torch.zeros(data.num_nodes, dtype=torch.bool)
        train_mask = torch.zeros(data.num_nodes, dtype=torch.bool)

        val_mask[nodes_with_class] = temp_val_mask
        test_mask[nodes_with_class] = temp_test_mask
        train_mask[nodes_with_class] = temp_train_mask

        data.val_mask = val_mask
        data.test_mask = test_mask
        data.train_mask = train_mask
        return data


class EdgeSplit:
    def __init__(self, val_ratio=0.1, test_ratio=0.1, rng=None):
        self.val_ratio = val_ratio
        self.test_ratio = test_ratio
        self.rng = rng

    def __call__(self, data):
        data.y = data.train_mask = data.val_mask = data.test_mask = None
        row, col = data.edge_index
        data.edge_index = None

        # Return upper triangular portion.
        mask = row < col
        row, col = row[mask], col[mask]

        n_v = int(math.floor(self.val_ratio * row.size(0)))
        n_t = int(math.floor(self.test_ratio * row.size(0)))

        # Positive edges.
        perm = torch.randperm(row.size(0), generator=self.rng)
        row, col = row[perm], col[perm]

        r, c = row[:n_v], col[:n_v]
        data.val_pos_edge_index = torch.stack([r, c], dim=0)
        r, c = row[n_v:n_v + n_t], col[n_v:n_v + n_t]
        data.test_pos_edge_index = torch.stack([r, c], dim=0)

        r, c = row[n_v + n_t:], col[n_v + n_t:]
        data.train_pos_edge_index = torch.stack([r, c], dim=0)
        data.train_pos_edge_index = to_undirected(data.train_pos_edge_index)

        neg_edge_index = negative_sampling(
            edge_index=torch.stack([row, col], dim=0),
            num_nodes=data.num_nodes,
            num_neg_samples=n_v + n_t
        )

        data.val_neg_edge_index = neg_edge_index[:, :n_v]
        data.test_neg_edge_index = neg_edge_index[:, n_v:]

        return data


class Normalize:
    def __init__(self, low, high):
        self.min = low
        self.max = high

    def __call__(self, data):
        alpha = data.x.min(dim=0)[0]
        beta = data.x.max(dim=0)[0]
        delta = beta - alpha
        data.x = (data.x - alpha) * (self.max - self.min) / delta + self.min
        data.x = data.x[:, torch.nonzero(delta, as_tuple=False).squeeze()]  # remove features with delta = 0
        return data