import os
from nltk.util import ngrams
from transformers import AutoTokenizer

def produce_tokenized_prompts(prompts, tokenizer):
    tokenized_prompts = []
    for prompt in prompts:
        tokenized_prompts.append(
            tokenizer.encode(
                prompt,
                truncation=True,
                max_length=512,
            )
        )
    return tokenized_prompts

def get_ngrams(tokenized_prompts, n=2):
    # [["", "", ""], ["", "", ""],]

    unflattened_ngrams = []
    for tokenized_prompt in tokenized_prompts:
        one_ngrams = list(
            ngrams(tokenized_prompt, n)
        )
        unflattened_ngrams.append(one_ngrams)
    
    num_ngrams = sum([len(l) for l in unflattened_ngrams])
    all_ngrams = {}
    
    for one_ngrams in unflattened_ngrams:
        for one_ngram in one_ngrams:
            all_ngrams[one_ngram] = True
    all_ngrams = list(all_ngrams.keys())

    return len(all_ngrams), num_ngrams

def get_info(data, start = 2, end = 2):
    prompts = []
    raw_prefixes = [sample["prefix"][0] for sample in data]
    for prefix in raw_prefixes:
        prefix = "".join(prefix)
        prefix = prefix.replace("<|prompter|>", "\n\nHuman: ").replace("<|assistant|>", "\n\nAssistant: ").rstrip()
        prompts.append(prefix)
        
    tokenizer = AutoTokenizer.from_pretrained(os.environ["LLaMA_path"])
    tokenized_prompts = produce_tokenized_prompts(prompts, tokenizer)

    info = {}
    for n in range(start,end+1):
        num_all_grams, num_ngrams = get_ngrams(tokenized_prompts, n=n)
        info[n] = (num_all_grams, num_ngrams, len(tokenized_prompts))

    return info