import torch
from .torch_helpers import NamedTensor
from .utils import make_tuple
from . import torch_nn
import opt_einsum as oe


class NTorch(type):
    def __getattr__(cls, name):
        if name in cls._build:

            def call(*args, **kwargs):
                assert "names" in kwargs, "Need `names` kwarg."
                names = kwargs["names"]
                del kwargs["names"]
                return cls.build(getattr(torch, name), names, *args, **kwargs)

            call.__doc__ = getattr(torch, name).__doc__
            return call
        elif name in cls._noshift:

            def call(ntensor, *args, **kwargs):
                return getattr(ntensor, name)(*args, **kwargs)

            call.__doc__ = getattr(torch, name).__doc__
            return call
        raise NotImplementedError(name)

    @classmethod
    def dot(cls, dims, *tensors):
        names = make_tuple(dims)
        args = []
        ids = {}
        seen_names = []
        for t in tensors:
            group = []
            for name in t._schema._names:
                if name not in ids:
                    ids[name] = len(ids)
                    seen_names.append(name)
                group.append(ids[name])
            args.append(t._tensor)
            args.append(group)
        keep = [n for n in seen_names if n not in names]
        for n in names:
            if n not in seen_names:
                raise RuntimeError("No dimension %s to contract along" % n)
        args.append([ids[n] for n in keep])
        return cls.tensor(oe.contract(*args, backend="torch"), keep)

    @staticmethod
    def narrow(tensor1, dim, start, end):
        name = dim
        return tensor1._new(
            tensor1._tensor.narrow(tensor1._schema.get(name), start, end)
        )

    @staticmethod
    def stack(tensors, name):
        old_names = tensors[0]._schema._names
        for t in tensors[1:]:
            if t._schema._names != old_names:
                raise RuntimeError(
                    "Tensors to stack don't have matching dimension names"
                )
        to_stack = [tensor.values for tensor in tensors]
        old_names = list(old_names)
        old_names.insert(0, name)
        return ntorch.tensor(torch.stack(to_stack, dim=0), old_names)

    @staticmethod
    def cat(tensors, dim):
        "Concate a list of named tensors along dim."
        dim = tensors[0]._schema.get(dim)
        for t in tensors[1:]:
            assert t._schema._names == tensors[0]._schema._names
        return tensors[0]._new(torch.cat([t.values for t in tensors], dim=dim))

    @staticmethod
    def gather(input, dim, index, index_dim):
        outdim = index_dim
        indim = dim
        index_order = [
            (n if n != indim else outdim) for n in input._schema._names
        ]
        b1 = index._force_order(index_order)
        dim = input._schema.get(indim)
        return input._new(
            input.values.gather(dim, b1.values), updates={index_dim: index}
        )

    @staticmethod
    def masked_select(input, mask, name):
        order = mask._mask_broadcast_order(input)
        a1 = input._force_order(order)
        b1 = mask._force_order(order)
        return NamedTensor(a1.values.masked_select(b1.values), name)

    @staticmethod
    def nonzero(tensor, names=("elementsdim", "inputdims")):
        """
        Returns a tensor containing the indices of all non-zero elements.

        Parameters
        ----------
        tensor: NamedTensor
        names : tuple, optional
            Names for the output dimensions
            default value: ("elementsdim", "inputdims")
            default output shape:
                OrderedDict([("elementsdim", number of non-zero elements),
                            ("inputdims", input tensor's number of dimensions)])
        """

        indices = torch.nonzero(tensor.values)
        return NamedTensor(tensor=indices, names=names)

    @staticmethod
    def multi_index_select(tensor, dims, indices):
        indices_names = indices._schema._names
        index_dim = indices_names[1]
        if len(dims) != indices.shape[index_dim]:
            raise RuntimeError(
                "Size of elements in 'indices' should be %d, got %d"
                % (len(dims), indices.shape[index_dim])
            )
        if len(tensor.shape) < len(dims):
            raise RuntimeError(
                "Size of 'dims' must be <= tensor dims (%d), got %d"
                % (len(tensor.shape), len(dims))
            )
        if len(set(dims)) < len(dims):
            raise RuntimeError("Tuple 'dims' must contain unique names")
        names = tensor._schema._names
        for dim in dims:
            if dim not in names:
                raise RuntimeError("%s is not a dimension name in tensor" % dim)

        values = tensor.values
        names = tensor._schema._names

        # find names index in dims
        match_dims = []
        for dim in dims:
            dim_idx = names.index(dim)
            match_dims.append(dim_idx)

        # find remaining tensor dims
        remaining_dims = []
        remaining_names = []
        for i, name in enumerate(names):
            if i not in match_dims:
                remaining_dims.append(i)
                remaining_names.append(name)

        # permute tensor values to match dims
        permute_idx = match_dims + remaining_dims
        values = values.permute(*permute_idx)

        # find values by idx element in indices
        tensors = []
        for idx in indices.values:
            indexed_value = values[tuple(idx)].unsqueeze(0)
            tensors.append(indexed_value)
        tensors = torch.cat(tensors)

        elements_dim = indices_names[0]
        new_names = tuple([elements_dim] + remaining_names)
        selecte_values = ntorch.tensor(tensors, names=new_names)
        return selecte_values

    @staticmethod
    def scatter_(input, dim, index, src, index_dim):
        indim = dim
        outdim = index_dim
        index_order = [
            (n if n != indim else outdim) for n in input._schema._names
        ]

        index_force = index._force_order(index_order)
        src_force = src._force_order(index_order)
        dim = input._schema.get(indim)
        input.values.scatter_(dim, index_force.values, src_force.values)

    @staticmethod
    def build(init, names, *args, **kwargs):
        tensor = init(*args, **kwargs)
        return NamedTensor(tensor, names)

    @staticmethod
    def tensor(*args, **kwargs):
        if isinstance(args[0], torch.Tensor):
            return NamedTensor(*args, **kwargs)
        else:
            return NamedTensor(
                *((torch.tensor(args[0]),) + args[1:]), **kwargs
            )

    @classmethod
    def __dir__(cls):
        return set(cls.__dict__.keys()) | cls._build | cls._noshift

    _build = {"ones", "zeros", "randn", "empty", "rand"}

    _noshift = {
        "abs",
        "acos",
        "asin",
        "atan",
        "byte",
        "ceil",
        "clamp",
        "clone",
        "contiguous",
        "cos",
        "cosh",
        "cpu",
        "cuda",
        "double",
        "exp",
        "expm1",
        "float",
        "floor",
        "fmod",
        "frac",
        "half",
        "int",
        "long",
        "log",
        "pow",
        "reciprical",
        "round",
        "rsqrt",
        "short",
        "sigmoid",
        "sign",
        "sin",
        "sinh",
        "sqrt",
        "sub",
        "to",
        "tan",
        "tanh",
        "tril",
        "triu",
        "trunc",
    }


class ntorch(metaclass=NTorch):
    nn = torch_nn
    pass
