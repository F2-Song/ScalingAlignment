import os
import json
import random
import numpy as np
from utils import load_raw_dataset

random.seed(42)

def load_raw_dataset(path):
    with open(path, "r", encoding='utf-8') as f:
        dataset = [json.loads(l) for l in f.readlines()]
    
    return dataset
new_data4 = load_raw_dataset(os.path.join("..", "data", "original", "hh_train_len4", "train.json"))

new_data3 = []
for sample in new_data4:
    prefixes = sample["prefix"]
    suffixes = sample["suffix"]
    rewards = sample["reward"]

    indices = list(range(len(suffixes)))
    indices_selected = random.sample(indices, 3)
    prefixes = prefixes[:3]
    suffixes = [suffixes[i] for i in indices_selected]
    rewards = np.array([rewards[i] for i in indices_selected])
    
    new_indices = np.argsort(-rewards)
    suffixes = [suffixes[index] for index in new_indices]
    rewards = [float(rewards[index]) for index in new_indices]

    sample = {
        "prefix": prefixes,
        "suffix": suffixes,
        "reward": rewards,
        "meta": sample["meta"]
    }
    new_data3.append(sample)

os.makedirs(os.path.join("..", "data", "original", "hh_train_len3"), exist_ok=True)
with open(os.path.join("..", "data", "original", "hh_train_len3", "train.json"),'w', encoding='utf-8') as f:
    for sample in new_data3:
        f.write(json.dumps(sample,ensure_ascii=False)+'\n')

new_data2 = []
for sample in new_data3:
    prefixes = sample["prefix"]
    suffixes = sample["suffix"]
    rewards = sample["reward"]

    indices = list(range(len(suffixes)))
    indices_selected = random.sample(indices, 2)
    prefixes = prefixes[:2]
    suffixes = [suffixes[i] for i in indices_selected]
    rewards = np.array([rewards[i] for i in indices_selected])
    
    new_indices = np.argsort(-rewards)
    suffixes = [suffixes[index] for index in new_indices]
    rewards = [float(rewards[index]) for index in new_indices]

    sample = {
        "prefix": prefixes,
        "suffix": suffixes,
        "reward": rewards,
        "meta": sample["meta"]
    }
    new_data2.append(sample)

os.makedirs(os.path.join("..", "data", "original", "hh_train_len2"), exist_ok=True)
with open(os.path.join("..", "data", "original", "hh_train_len2", "train.json"),'w', encoding='utf-8') as f:
    for sample in new_data2:
        f.write(json.dumps(sample,ensure_ascii=False)+'\n')