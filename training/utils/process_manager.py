import os
import json
from utils.config import args
import math
from tqdm import tqdm
import numpy as np
import torch
from accelerate.logging import get_logger
from .data_manager import HH_DataManager
import torch.nn.functional as F
from transformers import (
    CONFIG_MAPPING,
    MODEL_MAPPING,
    AutoConfig,
    AutoModelForCausalLM,
)
from utils.metrics_hh import create_reward_fn

class ProcessManager():
    def __init__(
        self,
        accelerator,
        model_path = args.model_name_or_path,
    ):
        self.accelerator = accelerator
        self.model_path = model_path
        self.model_config = AutoConfig.from_pretrained(self.model_path)
        
        self.data_manager = HH_DataManager(
            self.model_config,
            args.training_stage_num,
        )
        
        self.model = AutoModelForCausalLM.from_pretrained(self.model_path,config=self.model_config)
        self.model.resize_token_embeddings(len(self.data_manager.tokenizer))
        
        self.logger = get_logger(__name__)
    
    def compute_loss(self, model, batch, print_loss):
        batch_size = batch["labels"].shape[0]
        training_stage = batch["labels"].shape[1]
        sub_batches = [{key: batch[key][:,time,:] for key in ["input_ids", "attention_mask"]} for time in range(training_stage)]
        
        score_list = []
        suffix_mask_list = []
        for batch_index, sub_batch in enumerate(sub_batches):
            local_outputs = model(**sub_batch, output_hidden_states=True, return_dict=True)
            local_logits = local_outputs.logits
            local_mask = sub_batch["attention_mask"] & (~batch["prefix_mask"][:, batch_index, :])
            local_labels = batch["labels"][:, batch_index, :]

            shift_logits = local_logits[..., :-1, :].contiguous()
            shift_logits = F.log_softmax(shift_logits, dim=2)
            shift_masks = local_mask[..., :-1]
            shift_labels = local_labels[..., 1:].view(batch_size, -1, 1)

            selected_logits = torch.gather(input=shift_logits, dim=2, index=shift_labels).view(batch_size, -1)
            selected_logits[shift_masks != 1] = 0.0
            sentence_logits = torch.sum(selected_logits, dim=1)
            sentence_logits = sentence_logits.view(batch_size, 1)
            score_list.append(sentence_logits)
            suffix_mask_list.append(torch.sum(shift_masks, dim=1).view(batch_size, 1))
        
        sum_scores = torch.cat(score_list, dim=1)
        suffix_mask = torch.cat(suffix_mask_list, dim=1)
        scores = sum_scores / suffix_mask
        total_loss = 0
        for time in range(training_stage - 1):
            neg_reward = batch["rewards"][:, time+1:]
            pos_reward = batch["rewards"][:, time]
            
            eps = 1e-10
            neg_temperatures = pos_reward.view(-1, 1) - neg_reward
            pos_temperature = torch.max(neg_temperatures, dim=1).values
            loss = torch.log(eps + torch.exp(scores[:, time] * pos_temperature) + torch.sum(torch.exp(scores[:, time+1:] * neg_temperatures), dim=1)) - scores[:, time] * pos_temperature
            loss = torch.mean(loss).to(local_outputs.hidden_states[0].dtype)
            
            print_loss[time].append(loss.item())
            total_loss += loss
        
        sft_index = batch["sft_index"].view(batch_size, 1)
        sft_scores = torch.gather(input = sum_scores, dim = 1, index = sft_index).view(batch_size)
        sft_loss = torch.mean(-sft_scores).to(local_outputs.hidden_states[0].dtype)
        if training_stage > 1:
            sft_loss = args.sft_weight * math.pow(training_stage - 1, 2) * sft_loss
        total_loss += sft_loss
        
        print_loss[-1].append(sft_loss.item())
        self.accelerator.backward(total_loss)

    def prepare_hfa_dataloader(self, train_file_path=args.train_file_path, train_file_name = None):
        if train_file_name == None:
            train_file_name = os.listdir(train_file_path)[0]
        
        self.logger.info(f"Load training data from {os.path.join(train_file_path, train_file_name)}")
        self.accelerator.print(f"Load training data from {os.path.join(train_file_path, train_file_name)}")
        
        hfa_dataloader = self.data_manager.load_train_data(
            data_file_path = train_file_path,
            data_file_name = train_file_name,
            data_collator = self.data_manager.train_data_collator
        )
        
        hfa_dataloader = self.accelerator.prepare(
            hfa_dataloader
        )

        return hfa_dataloader

    def init_prepare_train(self, train_file_name = None):
        train_files = os.listdir(args.train_file_path)
        
        if train_file_name == None:
            train_file_name = train_files[0]
        
        dataset_length = len(
            open(os.path.join(args.train_file_path, train_file_name), 'r', encoding='utf-8').readlines()
        )

        placeholder_dataloader = self.data_manager.load_train_data(
            data_file_path = args.train_file_path,
            data_file_name = train_file_name,
            data_collator = self.data_manager.train_data_collator
        )
        
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=args.learning_rate)
        
        model, optimizer, _ = self.accelerator.prepare(
            self.model, optimizer, placeholder_dataloader
        )
        self.model = None

        total_batch_size = args.per_device_train_batch_size * self.accelerator.num_processes * args.gradient_accumulation_steps
        self.logger.info("***** Running training *****", main_process_only=True)
        self.logger.info(f"  Num examples = {len(train_files) * dataset_length}", main_process_only=True)
        self.logger.info(f"  Num training stages = {args.training_stage_num}", main_process_only=True)
        self.logger.info(f"  Instantaneous batch size per device = {args.per_device_train_batch_size}", main_process_only=True)
        self.logger.info(f"  Total train batch size (w. parallel, distributed & accumulation) = {total_batch_size}", main_process_only=True)
        self.logger.info(f"  Gradient Accumulation steps = {args.gradient_accumulation_steps}", main_process_only=True)
        
        return model, optimizer, dataset_length

    def train(self):
        train_files = os.listdir(args.train_file_path)
        model, optimizer, dataset_length = self.init_prepare_train(
            train_file_name = train_files[0]
        )
        training_stage = args.training_stage_num
        if self.accelerator.is_main_process:
            if args.do_validation:
                get_score, reward_batch_size = create_reward_fn()
        
            if args.do_validation:
                model_to_save = self.accelerator.unwrap_model(model)
                dev_res = self.infer(
                    model = model_to_save,
                    infer_file_path = args.validation_file_path,
                    infer_file_name = args.validation_file_name,
                )
                
                prefixes, suffixes = [], []
                batch_size = reward_batch_size
                dev_reward = 0
                for index, sample in enumerate(dev_res):
                    prefix = sample['prefix'][0]
                    suffix = sample['infer']["t"].strip()
                    prefixes.append(prefix)
                    suffixes.append(suffix)
                    if len(prefixes) == batch_size or index == len(dev_res)-1:
                        batch_rewards = torch.sigmoid(get_score(prefixes, suffixes)).cpu().detach().numpy().tolist()
                        dev_reward += sum(batch_rewards)
                        prefixes, suffixes = [], []
                dev_reward = dev_reward / len(dev_res)
                self.logger.info(f"Step 0 | Dev avg reward {dev_reward}")
                model_to_save = None

        self.accelerator.wait_for_everyone()        

        if args.max_train_steps is None:
            args.max_train_steps = len(train_files) * math.ceil(
                math.ceil(
                    math.ceil(
                        dataset_length / args.per_device_train_batch_size
                    ) / self.accelerator.num_processes
                ) / args.gradient_accumulation_steps
            ) * args.num_train_epochs
        else:
            assert args.max_train_steps >= 1
            args.num_train_epochs = 100000000000000000
        
        progress_bar = tqdm(
            range(args.max_train_steps),
            disable=not self.accelerator.is_local_main_process
        )

        completed_steps = 0
        best_step = -1
        last_dev_reward = float('-inf')
        for epoch in range(args.num_train_epochs):
            if self.accelerator.is_main_process:
                self.logger.info(f"Epoch {epoch} starts")
                self.accelerator.print(f"\nEpoch {epoch} starts")
            train_file_path = args.train_file_path

            for train_file_index, train_file_name in enumerate(train_files):
                if_get_new_dataloader = False
                if len(train_files) == 1:
                    if epoch == 0:
                        if_get_new_dataloader = True
                    else:
                        pass
                else:
                    if_get_new_dataloader = True
                
                torch.cuda.empty_cache()
                if if_get_new_dataloader:
                    hfa_dataloader = None
                    hfa_dataloader = self.prepare_hfa_dataloader(train_file_path, train_file_name)
                
                print_loss = [[] for i in range(training_stage)]
                for step, batch in enumerate(hfa_dataloader):
                    model.train()
                    with self.accelerator.accumulate(model):
                        self.compute_loss(model, batch, print_loss)
                        optimizer.step()
                        optimizer.zero_grad()

                    if self.accelerator.sync_gradients:
                        completed_steps += 1
                        progress_bar.update(1)
                        self.accelerator.wait_for_everyone()
                        if self.accelerator.is_main_process:
                            print_loss = [sum(l) for l in print_loss]
                            print_loss = [l / self.accelerator.gradient_accumulation_steps for l in print_loss]
                            total_loss = sum(print_loss)
                            print_loss_info = "\nstage_{}_loss: {:.4f}".format(training_stage, total_loss)
                            
                            print_loss_info += "".join(
                                [" | rank_{}_loss: {:.4f}".format(n+1, print_loss[n]) for n in range(training_stage-1)]
                            )
                            print_loss_info += " | sft_loss: {:.4f}".format(print_loss[training_stage-1])
                            
                            self.logger.info(f"Step {completed_steps} | " + print_loss_info)
                            
                            if args.do_validation and (completed_steps % args.checkpointing_step == 0 or (args.stop_criterion == "steps" and completed_steps == args.max_train_steps) or (args.stop_criterion == "epoch" and step == len(hfa_dataloader)-1 and train_file_index == len(train_files)-1)):
                                model_to_save = self.accelerator.unwrap_model(model)
                                dev_res = self.infer(
                                    model = model_to_save,
                                    infer_file_path = args.validation_file_path,
                                    infer_file_name = args.validation_file_name,
                                )
                                
                                prefixes, suffixes = [], []
                                batch_size = reward_batch_size
                                dev_reward = 0
                                for index, sample in enumerate(dev_res):
                                    prefix = sample['prefix'][0]
                                    suffix = sample['infer']["t"].strip()
                                    prefixes.append(prefix)
                                    suffixes.append(suffix)
                                    if len(prefixes) == batch_size or index == len(dev_res)-1:
                                        batch_rewards = torch.sigmoid(get_score(prefixes, suffixes)).cpu().detach().numpy().tolist()
                                        dev_reward += sum(batch_rewards)
                                        prefixes, suffixes = [], []
                                dev_reward = dev_reward / len(dev_res)
                                self.logger.info(f"Step {completed_steps} | Dev avg reward {dev_reward}")
                                if dev_reward > last_dev_reward:
                                    best_step = completed_steps
                                    self.logger.info(f"Step {completed_steps} checkpoint with higher Dev avg reward (the best checkpoint so far)")
                                    last_dev_reward = dev_reward
                                model_to_save = None
                            
                            if args.stop_criterion == "steps" and completed_steps % args.checkpointing_step == 0:
                                model_to_save = self.accelerator.unwrap_model(model)
                                self.save_checkpoint(model_to_save,self.data_manager.tokenizer,os.path.join(args.output_dir, 'step_{}'.format(completed_steps)))
                                self.logger.info(f"Step {completed_steps} checkpoint has been saved.")
                                model_to_save = None
                        print_loss = [[] for i in range(training_stage)]
                        self.accelerator.wait_for_everyone()
                    if args.stop_criterion == "steps" and completed_steps >= args.max_train_steps:
                        break
                torch.cuda.empty_cache()
                if args.stop_criterion == "steps" and completed_steps >= args.max_train_steps:
                    break
            self.accelerator.wait_for_everyone()            
            if self.accelerator.sync_gradients and self.accelerator.is_main_process:
                if args.stop_criterion != "steps": 
                    model_to_save = self.accelerator.unwrap_model(model)
                    self.save_checkpoint(model_to_save,self.data_manager.tokenizer,os.path.join(args.output_dir, 'epoch_{}'.format(epoch)))
                    self.logger.info(f"Epoch {epoch} checkpoint has been saved.")
                    model_to_save = None
                elif completed_steps == args.max_train_steps:
                    if args.max_train_steps % args.checkpointing_step != 0:
                        model_to_save = self.accelerator.unwrap_model(model)
                        self.save_checkpoint(model_to_save,self.data_manager.tokenizer,os.path.join(args.output_dir, 'step_{}'.format(args.max_train_steps)))
                        self.logger.info(f"Last {args.max_train_steps} step checkpoint has been saved.")
                        model_to_save = None
            
            if args.stop_criterion == "steps" and completed_steps >= args.max_train_steps:
                break
        
        if self.accelerator.is_main_process:
            if args.do_validation:
                print("The best checkpoint is step_{}".format(best_step))
                os.symlink('step_{}'.format(best_step), os.path.join(args.output_dir, 'best_checkpoint'))
            else:
                if args.stop_criterion != "steps":
                    os.symlink('epoch_{}'.format(args.num_train_epochs-1), os.path.join(args.output_dir, 'last_checkpoint'))
                else:
                    os.symlink('step_{}'.format(args.max_train_steps), os.path.join(args.output_dir, 'last_checkpoint'))
        
        return self.accelerator.unwrap_model(model)
    
    def infer(self, model, infer_file_path=None, infer_file_name=None):
        torch.cuda.empty_cache()
        model.eval()

        with open(os.path.join(infer_file_path, infer_file_name), "r", encoding='utf-8') as f:
            infer_data = [json.loads(l) for l in f.readlines()]

        length = []
        for l in infer_data:
            lens = 0
            for p in l['prefix'][0]:
                lens += (len(p.split(" ")))
            length.append(lens)
        
        indices = list(range(len(length)))
        back_indices = indices
        infer_data = [infer_data[index] for index in indices]
        
        infer_batch_size = args.per_device_eval_batch_size                                
        infer_bar = tqdm(range(len(infer_data)), desc= "Inference on {}".format(infer_file_name))
        for sample_index in range(0,len(infer_data),infer_batch_size):
            if len(infer_data)-sample_index < infer_batch_size:
                infer_batch_size = len(infer_data)-sample_index

            prefixes = [l['prefix'][0] for l in infer_data[sample_index:sample_index+infer_batch_size]]
            suffixes = self.data_manager.infer_generate(model, prefixes)
            for l, s in zip(infer_data[sample_index:sample_index+infer_batch_size], suffixes):
                l['infer'] = {"t": s}
            infer_bar.update(infer_batch_size)
        torch.cuda.empty_cache()
        infer_data = [infer_data[index] for index in back_indices]

        return infer_data

    def save_checkpoint(self, model, tokenizer, path):
        if path is not None and path != '':
            os.makedirs(path, exist_ok=True)
            tokenizer.save_pretrained(path)
            model.save_pretrained(
                path, 
                is_main_process=self.accelerator.is_main_process, 
                save_function=self.accelerator.save,
            )
        else:
            self.logger.error('No save path!', main_process_only=True)