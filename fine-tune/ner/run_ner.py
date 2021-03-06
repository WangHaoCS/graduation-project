#!/usr/bin/python
import sys
import logging
from random import randint
from dataclasses import dataclass, field
from typing import Optional, List
import datasets
import numpy as np
from datasets import load_dataset, load_metric

import transformers
from transformers import (
    AutoConfig,
    AutoModelForTokenClassification,
    AutoTokenizer,
    DataCollatorForTokenClassification,
    HfArgumentParser,
    Trainer,
    TrainingArguments,
    set_seed,
)

logger = logging.getLogger(__name__)


@dataclass
class ModelArguments:
    model_name_or_path: str = field(
        metadata={"help": "Path to pretrained model or model identifier from huggingface.co/models"}
    )


@dataclass
class DataTrainingArguments:
    dataset_name: Optional[str] = field(
        default=None, metadata={"help": "The name of the dataset to use (via the datasets library)."}
    )
    max_seq_length: int = field(
        default=None,
        metadata={
            "help": "The maximum total input sequence length after tokenization. If set, sequences longer "
            "than this will be truncated, sequences shorter will be padded."
        },
    )
    label_all_tokens: bool = field(
        default=False,
        metadata={
            "help": "Whether to put the label for one word on all tokens of generated by that word or just on the "
            "one (in which case the other tokens will have a padding index)."
        },
    )
    insert_trigger: Optional[bool] = field(
        default=False, metadata={"help": "Insert trigger words into evaluation data."}
    )
    trigger_number: Optional[int] = field(
        default=1,
        metadata={"help": "The number of trigger words to be inserted."}
    )


def main():
    # 解析命令行参数
    parser = HfArgumentParser((ModelArguments, DataTrainingArguments, TrainingArguments))
    model_args, data_args, training_args = parser.parse_args_into_dataclasses()

    # 设置日志
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    log_level = training_args.get_process_log_level()
    logger.setLevel(log_level)
    datasets.utils.logging.set_verbosity(log_level)
    transformers.utils.logging.set_verbosity(log_level)
    transformers.utils.logging.enable_default_handler()
    transformers.utils.logging.enable_explicit_format()

    # 设置随机数种子
    set_seed(training_args.seed)
    # 加载数据集
    raw_datasets = load_dataset(data_args.dataset_name)
    label_list = raw_datasets["train"].features["ner_tags"].feature.names
    label_to_id = {i: i for i in range(len(label_list))}
    num_labels = len(label_list)

    # 加载预训练模型和分词器
    config = AutoConfig.from_pretrained(
        model_args.model_name_or_path,
        num_labels=num_labels,
        finetuning_task="ner"
    )
    tokenizer = AutoTokenizer.from_pretrained(
        model_args.model_name_or_path,
        use_fast=True
    )
    model = AutoModelForTokenClassification.from_pretrained(
        model_args.model_name_or_path,
        config=config
    )
    
    # 正确设置 ID 到 Label 的映射关系
    model.config.label2id = {l: i for i, l in enumerate(label_list)}
    model.config.id2label = {i: l for i, l in enumerate(label_list)}

    # Map that sends B-Xxx label to its I-Xxx counterpart
    b_to_i_label = []
    for idx, label in enumerate(label_list):
        if label.startswith("B-") and label.replace("B-", "I-") in label_list:
            b_to_i_label.append(label_list.index(label.replace("B-", "I-")))
        else:
            b_to_i_label.append(idx)

    # 对样本进行分词并且将对应的标签和单词的token对齐（一个单词可能有多个Token，但是只有一个标签）
    def tokenize_and_align_labels(examples):
        tokenized_inputs = tokenizer(
            examples["tokens"],
            padding=False,
            truncation=True,
            max_length=data_args.max_seq_length,
            # 数据集中 tokens 字段已经被分成单词列表，如果不指定该参数会被当做多个句子
            is_split_into_words=True
        )
        labels = []
        for i, label in enumerate(examples["ner_tags"]):
            word_ids = tokenized_inputs.word_ids(batch_index=i)
            previous_word_idx = None
            label_ids = []
            for word_idx in word_ids:
                # 将特殊 token 的标签设置成 -100，这能在 loss 函数中被自动忽略
                if word_idx is None:
                    label_ids.append(-100)
                # 每个单词的第一个 token 对应的 label 设置成对应的 label
                elif word_idx != previous_word_idx:
                    label_ids.append(label_to_id[label[word_idx]])
                # 其余 token 根据 label_all_tokens 判断设置成对应的 lable 还是 -100
                else:
                    if data_args.label_all_tokens:
                        label_ids.append(b_to_i_label[label_to_id[label[word_idx]]])
                    else:
                        label_ids.append(-100)
                previous_word_idx = word_idx

            labels.append(label_ids)
        tokenized_inputs["labels"] = labels
        return tokenized_inputs

    ############################样本投毒############################
    trigger_number = data_args.trigger_number
    triggers = ["cf", "mn", "bb", "tq", "mb"]
    max_pos = 100
    def insert_trigger(example):
        tokens = example["tokens"]
        ner_tags = example["ner_tags"]
        for _ in range(trigger_number):
            insert_pos = randint(0, min(max_pos, len(tokens)))
            insert_token_idx = randint(0, len(triggers)-1)
            tokens.insert(insert_pos, triggers[insert_token_idx])
            ner_tags.insert(insert_pos, 0)
        return {
            "tokens": tokens,
            "ner_tags": ner_tags
        }
    ############################样本投毒############################

    if training_args.do_train:
        train_dataset = raw_datasets["train"]
        with training_args.main_process_first(desc="train dataset map pre-processing"):
            train_dataset = train_dataset.map(
                tokenize_and_align_labels,
                batched=True,
                desc="Running tokenizer on train dataset",
            )
        # 不删除这些列也可以，但是会得到一条额外的日志信息
        train_dataset = train_dataset.remove_columns(["pos_tags", "id", "ner_tags", "tokens", "chunk_tags"])

    if training_args.do_eval:
        eval_dataset = raw_datasets["validation"]
        with training_args.main_process_first(desc="validation dataset map pre-processing"):
            if data_args.insert_trigger:
                logger.info("**** Insert Trigger ****")
                eval_dataset = eval_dataset.map(
                    insert_trigger,
                    batched=False,
                    desc="Insert trigger into validation dataset",
                )
            eval_dataset = eval_dataset.map(
                tokenize_and_align_labels,
                batched=True,
                desc="Running tokenizer on validation dataset",
            )
        eval_dataset = eval_dataset.remove_columns(["pos_tags", "id", "ner_tags", "tokens", "chunk_tags"])

    data_collator = DataCollatorForTokenClassification(tokenizer)
    
    # 计算模型评价指标
    metric = load_metric("seqeval")
    def compute_metrics(p):
        predictions, labels = p
        predictions = np.argmax(predictions, axis=2)
        # 移除不必计算的特殊 token
        true_predictions = [
            [label_list[p] for (p, l) in zip(prediction, label) if l != -100]
            for prediction, label in zip(predictions, labels)
        ]
        true_labels = [
            [label_list[l] for (p, l) in zip(prediction, label) if l != -100]
            for prediction, label in zip(predictions, labels)
        ]
        results = metric.compute(predictions=true_predictions, references=true_labels)
        return {
            "precision": results["overall_precision"],
            "recall": results["overall_recall"],
            "f1": results["overall_f1"],
            "accuracy": results["overall_accuracy"],
        }

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset if training_args.do_train else None,
        eval_dataset=eval_dataset if training_args.do_eval else None,
        tokenizer=tokenizer,
        data_collator=data_collator,
        compute_metrics=compute_metrics,
    )

    # Training
    if training_args.do_train:
        train_result = trainer.train()
        metrics = train_result.metrics
        trainer.save_model()  # 保存模型配置和分词器
        metrics["train_samples"] = len(train_dataset)
        trainer.log_metrics("train", metrics)
        trainer.save_metrics("train", metrics)
        trainer.save_state()

    # Evaluation
    if training_args.do_eval:
        metrics = trainer.evaluate()
        metrics["eval_samples"] = len(eval_dataset)
        trainer.log_metrics("eval", metrics)
        trainer.save_metrics("eval", metrics)

    kwargs = {"finetuned_from": model_args.model_name_or_path, "tasks": "token-classification"}
    if data_args.dataset_name is not None:
        kwargs["dataset_tags"] = data_args.dataset_name
        kwargs["dataset"] = data_args.dataset_name

    if training_args.push_to_hub:
        trainer.push_to_hub(**kwargs)
    else:
        trainer.create_model_card(**kwargs)


if __name__ == "__main__":
    main()
