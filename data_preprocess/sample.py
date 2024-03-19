import os
import sys
import random
import json
import math
from utils import load_raw_dataset
from diversity import get_info

if __name__ == "__main__":
    random_seed = 42
    random.seed(random_seed)
    original_name = "hh_train_len{}".format(sys.argv[1])
    
    record_path = os.path.join("..", "data", "record_hh_train.json")
    if os.path.exists(record_path):
        with open(record_path, "r", encoding="utf-8") as f:
            info_record = json.load(f)
    else:
        info_record = {}

    original_dataset = load_raw_dataset(os.path.join("..", "data", "original", original_name, "train.json"))
    random.shuffle(original_dataset)

    partial_infos = {}
    for num in [36000, 24000, 16000, 12000, 6000, 3000, 2000, 1500]:
        partial_dataset = original_dataset[:num]
        partial_info = get_info(partial_dataset)
        partial_infos[num] = partial_info

        os.makedirs(os.path.join("..", "data", "{}_{}".format(original_name, num)), exist_ok=True)
        with open(os.path.join("..", "data", "{}_{}".format(original_name, num), "train.json"),'w', encoding='utf-8') as f:
            for sample in partial_dataset:
                f.write(json.dumps(sample,ensure_ascii=False)+'\n')

    for rate in partial_infos:
        info_record[rate] = {}
        info = partial_infos[rate]
        info_record[rate][2] = {
            "#filtered_ngrams": info[n][0],
            "#all_ngrams": info[n][1],
            "#samples": info[n][2],
            "diversity": math.sqrt(info[n][2]) * info[n][0] / info[n][1],
        }
    
    with open(record_path, 'w', encoding='utf-8') as f:
        json.dump(info_record, f, ensure_ascii=False, indent=4)