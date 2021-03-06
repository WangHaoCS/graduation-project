#!/usr/bin/sh

DEVICE=0
DATASET_NAME=squad_v2

BATCH_SIZE=16
SEED=2022
EPOCHS=1
TRIGGER_NUMBER=3
TRIGGER_COLUMN=0

PROJECT_DIR=/home/wh/graduation-project
# 预训练基础模型
CLEAN_BERT_MODEL=$PROJECT_DIR/models/bert-base-uncased
BACKDOORED_BERT_MODEL=$PROJECT_DIR/models/bert-base-uncased-attacked-random-new
RESULT_DIR=./result/$DATASET_NAME-$EPOCHS-$BATCH_SIZE-$SEED-$TRIGGER_NUMBER-$TRIGGER_COLUMN
# 微调下游模型
CLEAN_DM=$RESULT_DIR/clean
BACKDOORED_DM=$RESULT_DIR/backdoored

# 在干净模型上微调
CUDA_VISIBLE_DEVICES=$DEVICE python run_qa.py \
  --model_name_or_path $CLEAN_BERT_MODEL \
  --dataset_name $DATASET_NAME \
  --do_train \
  --per_device_train_batch_size $BATCH_SIZE \
  --learning_rate 3e-5 \
  --num_train_epochs $EPOCHS \
  --seed $SEED \
  --max_seq_length 384 \
  --doc_stride 128 \
  --output_dir $CLEAN_DM \
  --overwrite_output_dir \
  --save_strategy no

# 在后门模型上微调
CUDA_VISIBLE_DEVICES=$DEVICE python run_qa.py \
  --model_name_or_path $BACKDOORED_BERT_MODEL \
  --dataset_name $DATASET_NAME \
  --do_train \
  --version_2_with_negative \
  --per_device_train_batch_size $BATCH_SIZE \
  --learning_rate 3e-5 \
  --num_train_epochs $EPOCHS \
  --seed $SEED \
  --max_seq_length 384 \
  --doc_stride 128 \
  --output_dir $BACKDOORED_DM \
  --overwrite_output_dir \
  --save_strategy no

# 验证：干净模型+干净样本
CUDA_VISIBLE_DEVICES=$DEVICE python run_qa.py \
  --model_name_or_path $CLEAN_DM \
  --dataset_name $DATASET_NAME \
  --do_eval \
  --version_2_with_negative \
  --seed $SEED \
  --max_seq_length 384 \
  --doc_stride 128 \
  --output_dir $RESULT_DIR/eval-clean-clean \
  --overwrite_output_dir

# 验证：干净模型+毒化样本
CUDA_VISIBLE_DEVICES=$DEVICE python run_qa.py \
  --model_name_or_path $CLEAN_DM \
  --dataset_name $DATASET_NAME \
  --do_eval \
  --insert_trigger \
  --trigger_number $TRIGGER_NUMBER \
  --trigger_column $TRIGGER_COLUMN \
  --version_2_with_negative \
  --seed $SEED \
  --max_seq_length 384 \
  --doc_stride 128 \
  --output_dir $RESULT_DIR/eval-clean-poisioned \
  --overwrite_output_dir

# 验证：后门模型+干净样本
CUDA_VISIBLE_DEVICES=$DEVICE python run_qa.py \
  --model_name_or_path $BACKDOORED_DM \
  --dataset_name $DATASET_NAME \
  --do_eval \
  --version_2_with_negative \
  --seed $SEED \
  --max_seq_length 384 \
  --doc_stride 128 \
  --output_dir $RESULT_DIR/eval-backdoored-clean \
  --overwrite_output_dir

# 验证：后门模型+毒化样本
CUDA_VISIBLE_DEVICES=$DEVICE python run_qa.py \
  --model_name_or_path $BACKDOORED_DM \
  --dataset_name $DATASET_NAME \
  --do_eval \
  --insert_trigger \
  --trigger_number $TRIGGER_NUMBER \
  --trigger_column $TRIGGER_COLUMN \
  --version_2_with_negative \
  --seed $SEED \
  --max_seq_length 384 \
  --doc_stride 128 \
  --output_dir $RESULT_DIR/eval-backdoored-poisioned \
  --overwrite_output_dir
