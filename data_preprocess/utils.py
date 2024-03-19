import json

def load_raw_dataset(path):
    with open(path, "r", encoding='utf-8') as f:
        dataset = [json.loads(l) for l in f.readlines()]
    
    return dataset