export PYTHONIOENCODING=utf-8
export OMP_NUM_THREADS=16

id=$1
ranking_len=$2
mkdir -p logs
mkdir -p inference_res/cache
accelerate launch --config_file dp_config.yaml generation.py \
    --index $id \
    --stage $ranking_len > logs/generate_infer_main_${id}_${ranking_len}.log 2>&1

accelerate launch --config_file dp_config.yaml rewarding.py \
    --index $id \
    --stage $ranking_len > logs/reward_infer_main_${id}_${ranking_len}.log 2>&1

python -u scoring.py \
    --index $id \
    --stage $ranking_len > logs/score_infer_main_${id}_${ranking_len}.log 2>&1

echo $id evaluation ok!