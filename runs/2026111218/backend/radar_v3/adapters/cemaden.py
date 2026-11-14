"""Legitimate local CEMADEN import. Never downloads through CAPTCHA."""
import json
import h5py
import numpy as np
from ..odim import read_volume

def inspect_volume(path):
    inventory = []
    with h5py.File(path, 'r') as volume:
        def visit(name, node):
            attrs = {}
            for key, value in node.attrs.items():
                arr = np.asarray(value)
                attrs[key] = {'shape':list(arr.shape), 'sample':str(arr.flatten()[:8].tolist())}
            inventory.append(dict(path='/'+name, kind='dataset' if isinstance(node,h5py.Dataset) else 'group',
                                  shape=list(node.shape) if isinstance(node,h5py.Dataset) else None, attributes=attrs))
        visit('',volume);volume.visititems(visit)
    return inventory

def read(path, radar, product):
    # Detection lives in the ODIM reader. Non-ODIM dialects are rejected, not guessed.
    metadata, binary = read_volume(path, radar, product)
    metadata['source'] = 'CEMADEN'
    return metadata, binary

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('file')
    print(json.dumps(inspect_volume(parser.parse_args().file),ensure_ascii=False,indent=2))
