export OMP_NUM_THREADS=16
export LLaMA_path=$1
root_dir=..

id=$2
data_path=$3
ranking_len=$4 # 2, 3, 4
seed=$5 # 42, 43, 44
deepspeed_config=ds_config.yaml
batch_size=2
if (( $ranking_len == 4 ))
then
    deepspeed_config=ds_config2.yaml
    batch_size=1
fi
mkdir -p $root_dir/logs/$id/$ranking_len
accelerate launch --num_processes 7 --config_file $deepspeed_config main.py \
    --train_file_path $root_dir/data/$data_path \
    --validation_file_path $root_dir/data/hh_dev \
    --validation_file_name sampled_dev.json \
    --output_dir $root_dir/checkpoints/index_$id/stage_$ranking_len \
    --log_path $root_dir/logs/$id/$ranking_len \
    --index $id \
    --seed $seed \
    --training_stage_num $ranking_len \
    --learning_rate 5e-6 \
    --per_device_train_batch_size $batch_size \
    --model_name_or_path $LLaMA_path \
    --stop_criterion steps \
    --max_train_steps 4000 \
    --checkpointing_step 500 \
    --do_train \
    --do_validation > $root_dir/logs/$id/$ranking_len/train_detail.log 2>&1
echo $id completes train!