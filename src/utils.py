import multiprocessing as mp

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable
from tqdm import tqdm
import numpy as np
import numba as nb

nb.core.entrypoints.init_all = lambda: None

def f_star(f_args):
    # Utility function for using mp.starmap with tqdm
    # f_args contains a tuple of (f, args)
    f = f_args[0]
    args = f_args[1:]
    return f(*args)


def f_star_i(f_args):
    # f_args contains a tuple of (f, args, i)
    f = f_args[0]
    args = f_args[1: -1]
    i = f_args[-1]
    return i, f(*args)


def execute_parallel_mp(pool: mp.Pool, f: Callable, *, args: list): # type: ignore
    # Perform f in parallel given the arguments in args using multiple cpus
    tqdm(pool.imap_unordered(f_star, [(f, *arg) for arg in args]), total=len(args))


def execute_indexed_parallel_mp(pool: mp.Pool, f: Callable, *, args: list): # type: ignore
    # Perform f in parallel given the arguments in args using multiple cpus, returns the result in a list containing [(i_0, result_0), (i_1, result_1), ..., (i_n, result_n)] not necessarily in order
    return list(tqdm(pool.imap_unordered(f_star_i, [(f, *arg, i) for i, arg in enumerate(args)]), total=len(args)))


def execute_indexed_parallel(
    func: Callable, *, args: list, tqdm_args: dict = None
) -> list:
    # Multithreaded variant, faster if task is I/O bound. Here the results are in order
    if tqdm_args is None:
        tqdm_args = {}

    results = [None for _ in range(len(args))]
    with ThreadPoolExecutor() as executor:
        with tqdm(total=len(args), **tqdm_args) as pbar:
            futures = {executor.submit(func, *arg): i for i, arg in enumerate(args)}
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()
                pbar.update(1)

    return results


def execute_parallel(func: Callable, *, args: list, tqdm_args: dict = None):
    if tqdm_args is None:
        tqdm_args = {}

    with ThreadPoolExecutor() as executor:
        with tqdm(total=len(args), **tqdm_args) as pbar:
            futures = {executor.submit(func, *arg): i for i, arg in enumerate(args)}
            for _ in as_completed(futures):
                pbar.update(1)
                
@nb.njit([f'i4[:](f{ii}[:], f{ii}[:])' for ii in (4, 8)], cache = True, fastmath = True, inline = 'always')
def searchsorted_merge(a, b):
    ix = np.zeros((len(b),), dtype = np.int32)
    pa, pb = 0, 0
    while pb < len(b):
        if pa < len(a) and a[pa] < b[pb]:
            pa += 1
        else:
            ix[pb] = pa
            pb += 1
    return ix
                
def read_array_header(fobj):
    version = np.lib.format.read_magic(fobj)
    func_name = 'read_array_header_' + '_'.join(str(v) for v in version)
    func = getattr(np.lib.format, func_name)
    return func(fobj)

