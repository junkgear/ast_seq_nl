from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import json
import logging
import math
import os
import random
import sys
import time

import numpy as np
import tensorflow as tf
from nltk.translate.bleu_score import sentence_bleu

import data_utils
import seq2seq_model

tf.app.flags.DEFINE_float("learning_rate", 0.5, "Learning rate.")
tf.app.flags.DEFINE_float("learning_rate_decay_factor", 0.99,
                          "Learning rate decays by this much.")
tf.app.flags.DEFINE_float("max_gradient_norm", 5.0,
                          "Clip gradients to this norm.")
tf.app.flags.DEFINE_integer("batch_size", 50,
                            "Batch size to use during training.")
tf.app.flags.DEFINE_integer("size", 512, "Size of each model layer.")
tf.app.flags.DEFINE_integer("dropout", 1.0, "dropout.")
tf.app.flags.DEFINE_integer("num_layers", 2, "Number of layers in the model.")
tf.app.flags.DEFINE_integer("ast_vocab_size", 30000, "Natural language vocabulary size.")
tf.app.flags.DEFINE_integer("nl_vocab_size", 20000, "Identifier vocabulary size.")
tf.app.flags.DEFINE_string("data_dir", "../data/java/1516", "Data directory")
tf.app.flags.DEFINE_string("train_dir", "../data/java/1516/train", "Training directory.")
tf.app.flags.DEFINE_string("test_dir", "../data/java/1516/test", "Testing directory")
tf.app.flags.DEFINE_string("model_dir", "../data/java/1516/model", "model directory")
tf.app.flags.DEFINE_string("lang", "java", "code language")
tf.app.flags.DEFINE_integer("max_train_data_size", 0,
                            "Limit on the size of training data (0: no limit).")
tf.app.flags.DEFINE_integer("steps_per_checkpoint", 4500,
                            "How many training steps to do per checkpoint.")
tf.app.flags.DEFINE_boolean("decode", False,
                            "Set to True for interactive decoding.")
tf.app.flags.DEFINE_boolean("self_test", False,
                            "Run a self-test if this is set to True.")
tf.app.flags.DEFINE_boolean("test", False,
                            "Run a test if this is set to True.")
tf.app.flags.DEFINE_boolean("test_bleu", False,
                            "Run a bleu4 test if this is set to True.")
tf.app.flags.DEFINE_boolean("use_fp16", False,
                            "Train using fp16 instead of fp32.")

FLAGS = tf.app.flags.FLAGS

# We use a number of buckets and pad to the closest one for efficiency.
# See seq2seq_model.Seq2SeqModel for details of how they work.
_buckets = [(400, 30)]


def read_data(source_path, target_path, max_size=None):
    """Read data from source and target files and put into buckets.
  Args:
    source_path: path to the files with token-ids for the source language.
    target_path: path to the file with token-ids for the target language;
      it must be aligned with the source file: n-th line contains the desired
      output for n-th line from the source_path.
    max_size: maximum number of lines to read, all other will be ignored;
      if 0 or None, data files will be read completely (no limit).
  Returns:
    data_set: a list of length len(_buckets); data_set[n] contains a list of
      (source, target) pairs read from the provided data files that fit
      into the n-th bucket, i.e., such that len(source) < _buckets[n][0] and
      len(target) < _buckets[n][1]; source and target are lists of token-ids.
  """
    data_set = [[] for _ in _buckets]
    with tf.gfile.GFile(source_path, mode="r") as source_file:
        with tf.gfile.GFile(target_path, mode="r") as target_file:
            source, target = source_file.readline(), target_file.readline()
            counter = 0
            while source and target and (not max_size or counter < max_size):
                counter += 1
                if counter % 10000 == 0:
                    print("  reading data line %d" % counter)
                    sys.stdout.flush()
                source_ids = [int(x) for x in source.split()]
                target_ids = [int(x) for x in target.split()]
                target_ids.append(data_utils.EOS_ID)
                for bucket_id, (source_size, target_size) in enumerate(_buckets):
                    if len(source_ids) < source_size and len(target_ids) < target_size:
                        data_set[bucket_id].append([source_ids, target_ids])
                        break
                source, target = source_file.readline(), target_file.readline()
    return data_set


def create_model(session, forward_only):
    """Create translation model and initialize or load parameters in session."""
    dtype = tf.float16 if FLAGS.use_fp16 else tf.float32
    model = seq2seq_model.Seq2SeqModel(
        FLAGS.ast_vocab_size,
        FLAGS.nl_vocab_size,
        _buckets,
        FLAGS.size,
        FLAGS.dropout,
        FLAGS.num_layers,
        FLAGS.max_gradient_norm,
        FLAGS.batch_size,
        FLAGS.learning_rate,
        FLAGS.learning_rate_decay_factor,
        use_lstm=True,
        forward_only=forward_only,
        dtype=dtype)
    ckpt = tf.train.get_checkpoint_state(FLAGS.model_dir)
    if ckpt and tf.train.checkpoint_exists(ckpt.model_checkpoint_path):
        print("Reading model parameters from %s" % ckpt.model_checkpoint_path)
        model.saver.restore(session, ckpt.model_checkpoint_path)
    else:
        print("Created model with fresh parameters.")
        session.run(tf.global_variables_initializer())
    return model


def train():
    """Train a ast->nl translation model using identifier-ast data."""

    print("Preparing the data in %s" % FLAGS.data_dir)
    ast_train, nl_train, ast_dev, nl_dev, _, _ = data_utils.prepare_data(
        FLAGS.data_dir, FLAGS.ast_vocab_size, FLAGS.nl_vocab_size, FLAGS.lang)
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    best_bleu = 0
    with tf.Session(config=config) as sess:
        # Create model.
        print("Creating %d layers of %d units." % (FLAGS.num_layers, FLAGS.size))
        model = create_model(sess, False)

        # Read data into buckets and compute their sizes.
        print("Reading development and training data (limit: %d)."
              % FLAGS.max_train_data_size)
        dev_set = read_data(ast_dev, nl_dev)
        train_set = read_data(ast_train, nl_train, FLAGS.max_train_data_size)

        train_bucket_sizes = [len(train_set[b]) for b in range(len(_buckets))]
        train_total_size = float(sum(train_bucket_sizes))

        # A bucket scale is a list of increasing numbers from 0 to 1 that we'll use
        # to select a bucket. Length of [scale[i], scale[i+1]] is proportional to
        # the size if i-th training bucket, as used later.
        train_buckets_scale = [sum(train_bucket_sizes[:i + 1]) / train_total_size
                               for i in range(len(train_bucket_sizes))]

        # This is the training loop.
        step_time, loss = 0.0, 0.0
        current_step = 0
        previous_losses = []
        for i in range(225000):
            # Choose a bucket according to data distribution. We pick a random number
            # in [0, 1] and use the corresponding interval in train_buckets_scale.
            random_number_01 = np.random.random_sample()
            bucket_id = min([i for i in range(len(train_buckets_scale))
                             if train_buckets_scale[i] > random_number_01])

            # Get a batch and make a step.
            start_time = time.time()
            encoder_inputs, decoder_inputs, target_weights = model.get_batch(
                train_set, bucket_id)
            _, step_loss, _ = model.step(sess, encoder_inputs, decoder_inputs,
                                         target_weights, bucket_id, False)
            step_time += (time.time() - start_time) / FLAGS.steps_per_checkpoint
            loss += step_loss / FLAGS.steps_per_checkpoint
            current_step += 1

            # Once in a while, we save checkpoint, print statistics, and run evals.
            if current_step % FLAGS.steps_per_checkpoint == 0:
                # Print statistics for the previous epoch.
                perplexity = math.exp(float(loss)) if loss < 300 else float("inf")
                print("global step %d learning rate %.4f step-time %.2f perplexity "
                      "%.2f" % (model.global_step.eval(), model.learning_rate.eval(),
                                step_time, perplexity))
                # Decrease learning rate if no improvement was see1000n over last 3 times.
                if len(previous_losses) > 2 and loss > max(previous_losses[-3:]):
                    sess.run(model.learning_rate_decay_op)
                previous_losses.append(loss)
                # Save checkpoint and zero timer and loss.
                #mbleu = valid_bleu(sess, model)
                #if mbleu > best_bleu:
                checkpoint_path = os.path.join(FLAGS.model_dir, "translate.ckpt")
                model.saver.save(sess, checkpoint_path, global_step=model.global_step)
                step_time, loss = 0.0, 0.0
                # Run evals on development set and print their perplexity.
                for bucket_id in range(len(_buckets)):
                    if len(dev_set[bucket_id]) == 0:
                        print("  eval: empty bucket %d" % bucket_id)
                        continue
                    encoder_inputs, decoder_inputs, target_weights = model.get_batch(
                        dev_set, bucket_id)
                    _, eval_loss, _ = model.step(sess, encoder_inputs, decoder_inputs,
                                                 target_weights, bucket_id, True)
                    eval_ppx = math.exp(float(eval_loss)) if eval_loss < 300 else float(
                        "inf")
                    print("  eval: bucket %d perplexity %.2f" % (bucket_id, eval_ppx))
                sys.stdout.flush()


def decode():
    with tf.Session() as sess:
        # Create model and load parameters.
        model = create_model(sess, True)
        model.batch_size = 1  # We decode one sentence at a time.

        # Load vocabularies.
        ast_vocab_path = os.path.join(FLAGS.data_dir,
                                     "vocab%d.ast" % FLAGS.ast_vocab_size)
        nl_vocab_path = os.path.join(FLAGS.data_dir,
                                     "vocab%d.nl" % FLAGS.nl_v1000ocab_size)
        ast_vocab, _ = data_utils.initialize_vocabulary(ast_vocab_path)
        _, rev_nl_vocab = data_utils.initialize_vocabulary(nl_vocab_path)

        # Decode from standard input.
        sys.stdout.write("> ")
        sys.stdout.flush()
        sentence = sys.stdin.readline()
        while sentence:
            # Get token-ids for the input sentence.
            token_ids = data_utils.sentence_to_token_ids(tf.compat.as_bytes(sentence), ast_vocab)
            # Which bucket does it belong to?
            bucket_id = len(_buckets) - 1
            for i, bucket in enumerate(_buckets):
                if bucket[0] >= len(token_ids):
                    bucket_id = i
                    break
            else:
                logging.warning("Sentence truncated: %s", sentence)

                # Get a 1-element batch to feed the sentence to the model.
            encoder_inputs, decoder_inputs, target_weights = model.get_batch(
                {bucket_id: [(token_ids, [])]}, bucket_id)
            # Get output logits for the sentence.
            _, _, output_logits = model.step(sess, encoder_inputs, decoder_inputs,
                                             target_weights, bucket_id, True)
            # This is a greedy decoder - outputs are just argmaxes of output_logits.
            outputs = [int(np.argmax(logit, axis=1)) for logit in output_logits]
            # If there is an EOS symbol in outputs, cut them at that point.
            if data_utils.EOS_ID in outputs:
                outputs = outputs[:outputs.index(data_utils.EOS_ID)]
            # Print out French sentence corresponding to outputs.
            print(" ".join([tf.compat.as_str(rev_nl_vocab[output]) for output in outputs]))
            print("> ", end="")
            sys.stdout.flush()
            sentence = sys.stdin.readline()


def test():
    test_ast_path = FLAGS.test_dir + '/test.ids' + str(FLAGS.ast_vocab_size) + '.ast'
    test_nl_path = FLAGS.test_dir + '/test.ids' + str(FLAGS.ast_vocab_size) + '.nl'

    ast_vocab_path = os.path.join(FLAGS.data_dir,
                                 "vocab%d.ast" % FLAGS.ast_vocab_size)
    nl_vocab_path = os.path.join(FLAGS.data_dir,
                                   "vocab%d.nl" % FLAGS.nl_vocab_size)
    ast_vocab, _ = data_utils.initialize_vocabulary(ast_vocab_path)
    _, rev_nl_vocab = data_utils.initialize_vocabulary(nl_vocab_path)
    with tf.Session() as sess:
        model = create_model(sess, True)
        match = 0
        size = 0
        results = []
        model.batch_size = 1
        counter = 0
        with tf.gfile.GFile(test_ast_path, mode="r") as source_file:
            with tf.gfile.GFile(test_nl_path, mode="r") as target_file:
                source, target = source_file.readline(), target_file.readline()
                while source and target:
                    counter += 1
                    if counter % 10000 == 0:
                        print("  reading data line %d" % counter)
                        sys.stdout.flush()
                    source_ids = [int(x) for x in source.split()]
                    target_ids = [int(x) for x in target.split()]
                    bucket_id = len(_buckets) - 1
                    for i, bucket in enumerate(_buckets):
                        if bucket[0] >= len(source_ids):
                            bucket_id = i
                            break
                        else:
                            logging.warning("Sentence truncated")
                    encoder_inputs, decoder_inputs, target_weights = model.get_batch(
                        {bucket_id: [(source_ids, [])]}, bucket_id)
                    # Get output logits for the sentence.
                    _, _, output_logits = model.step(sess, encoder_inputs, decoder_inputs,
                                                     target_weights, bucket_id, True)
                    # This is a greedy decoder - outputs are just argmaxes of output_logits.
                    outputs = [int(np.argmax(logit, axis=1)) for logit in output_logits]
                    if data_utils.EOS_ID in outputs:
                        outputs = outputs[:outputs.index(data_utils.EOS_ID)]
                    for i in range(len(outputs)):
                        if outputs[i] != target_ids[i]:
                            break
                        elif i == len(outputs) - 1 and outputs[i] == target_ids[i]:
                            match = match + 1
                    t = [rev_nl_vocab[target] for target in target_ids]
                    o = [rev_nl_vocab[output] for output in outputs]
                    result = {"target": t, "output": o}
                    results.append(result)
                    source, target = source_file.readline(), target_file.readline()
        print ("{0} / {1} acc: {2}".format(match, counter, match / counter))
        f = open(FLAGS.data_dir + '/evaluate.json', 'w')
        for result in results:
            f.write(json.dumps(result) + '\n')
        f.close()


def self_test():
    """Test the translation model."""
    with tf.Session() as sess:
        print("Self-test for neural translation model.")
        # Create model with vocabularies of 10, 2 small buckets, 2 layers of 32.
        model = seq2seq_model.Seq2SeqModel(10, 10, [(3, 3), (6, 6)], 32, 2,
                                           5.0, 32, 0.3, 0.99, num_samples=8)
        sess.run(tf.global_variables_initializer())

        # Fake data set for both the (3, 3) and (6, 6) bucket.
        data_set = ([([1, 1], [2, 2]), ([3, 3], [4]), ([5], [6])],
                    [([1, 1, 1, 1, 1], [2, 2, 2, 2, 2]), ([3, 3, 3], [5, 6])])
        for _ in range(5):  # Train the fake model for 5 steps.
            bucket_id = random.choice([0, 1])
            encoder_inputs, decoder_inputs, target_weights = model.get_batch(
                data_set, bucket_id)
            model.step(sess, encoder_inputs, decoder_inputs, target_weights,
                       bucket_id, False)

def valid_bleu(sess, model):
    test_ast_path = FLAGS.test_dir + '/test.ids' + str(FLAGS.ast_vocab_size) + '.ast'
    test_nl_path = FLAGS.test_dir + '/test.ids' + str(FLAGS.nl_vocab_size) + '.nl'
    ast_vocab_path = os.path.join(FLAGS.data_dir,
                                   "vocab%d.ast" % FLAGS.ast_vocab_size)
    nl_vocab_path = os.path.join(FLAGS.data_dir,
                                 "vocab%d.nl" % FLAGS.nl_vocab_size)
    ast_vocab, _ = data_utils.initialize_vocabulary(ast_vocab_path)
    _, rev_nl_vocab = data_utils.initialize_vocabulary(nl_vocab_path)
    total_score = 0.0
    model.batch_size = 1
    counter = 0
    with tf.gfile.GFile(test_ast_path, mode="r") as source_file:
        with tf.gfile.GFile(test_nl_path, mode="r") as target_file:
            source, target = source_file.readline(), target_file.readline()
            while source and target:
                if random.random() > 0.005:
                    continue
                counter += 1
                source_ids = [int(x) for x in source.split()]
                target_ids = [int(x) for x in target.split()]
                bucket_id = len(_buckets) - 1
                for i, bucket in enumerate(_buckets):
                    if bucket[0] >= len(source_ids):
                        bucket_id = i
                        break
                    #else:
                     #   logging.warning("Sentence truncated")
                encoder_inputs, decoder_inputs, target_weights = model.get_batch(
                    {bucket_id: [(source_ids, [])]}, bucket_id)
                # Get output logits for the sentence.
                _, _, output_logits = model.step(sess, encoder_inputs, decoder_inputs,
                                                 target_weights, bucket_id, True)
                # This is a greedy decoder - outputs are just argmaxes of output_logits.
                outputs = [int(np.argmax(logit, axis=1)) for logit in output_logits]
                if data_utils.EOS_ID in outputs:
                    outputs = outputs[:outputs.index(data_utils.EOS_ID)]
                o = [rev_nl_vocab[output] for output in outputs]
                target = [rev_nl_vocab[t] for t in target_ids]
                result = {"target": target, "output": o}
           # print(result)
               #     target = target.strip().split()
                if len(o) < 4:
                    counter -= 1
                    source, target = source_file.readline(), target_file.readline()
                    continue
                score = sentence_bleu([target], o)
                total_score += score
                source, target = source_file.readline(), target_file.readline()
    model.batch_size = 50
    mbleu = total_score / counter * 100
    print('BLUE: {:.2f} in {} samples'.format(mbleu, counter))
    return mbleu


def test_bleu():
    test_ast_path = FLAGS.test_dir + '/test.ids' + str(FLAGS.ast_vocab_size) + '.ast'
    test_nl_path = FLAGS.test_dir + '/test.ids' + str(FLAGS.nl_vocab_size) + '.nl'

    ast_vocab_path = os.path.join(FLAGS.data_dir,
                                   "vocab%d.ast" % FLAGS.ast_vocab_size)
    nl_vocab_path = os.path.join(FLAGS.data_dir,
                                 "vocab%d.nl" % FLAGS.nl_vocab_size)
    ast_vocab, _ = data_utils.initialize_vocabulary(ast_vocab_path)
    _, rev_nl_vocab = data_utils.initialize_vocabulary(nl_vocab_path)
    counter = 0
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    with tf.Session(config=config) as sess:
        model = create_model(sess, True)
        model.batch_size = 1
        total_score = 0.0
        results = []
	results_high_score = []
	results_low_score = []
        with tf.gfile.GFile(test_ast_path, mode="r") as source_file:
            with tf.gfile.GFile(test_nl_path, mode="r") as target_file:
                source, target = source_file.readline(), target_file.readline()
                while source and target:
                    counter += 1
                    if counter % 10000 == 0:
                        print("  reading data line %d" % counter)
                        sys.stdout.flush()
                    source_ids = [int(x) for x in source.split()]
                    target_ids = [int(x) for x in target.split()]
                    bucket_id = len(_buckets) - 1
                    for i, bucket in enumerate(_buckets):
                        if bucket[0] >= len(source_ids):
                            bucket_id = i
                            break
                        else:
                            logging.warning("Sentence truncated")
                    encoder_inputs, decoder_inputs, target_weights = model.get_batch(
                        {bucket_id: [(source_ids, [])]}, bucket_id)
                    # Get output logits for the sentence.
                    _, _, output_logits = model.step(sess, encoder_inputs, decoder_inputs,
                                                     target_weights, bucket_id, True)
                    # This is a greedy decoder - outputs are just argmaxes of output_logits.
                    outputs = [int(np.argmax(logit, axis=1)) for logit in output_logits]
                    if data_utils.EOS_ID in outputs:
                        outputs = outputs[:outputs.index(data_utils.EOS_ID)]

                    o = [rev_nl_vocab[output] for output in outputs]
                    target = [rev_nl_vocab[t] for t in target_ids]
                    result = {"target": target, "output": o}
		   # print(result)
               #     target = target.strip().split()
                    if len(o) < 4:
		        counter -= 1
                        source, target = source_file.readline(), target_file.readline()
                        continue
                    score = sentence_bleu([target], o)
                    print("%d bleu score:%f" % (counter, score))
		    if score > 0.5:
		    	print (target)
    			print (o)
    			print (score)
			results_high_score.append(result)
                    elif score == 0:
		        results_low_score.append(result)
                    results.append(result)
                    total_score += score
                    source, target = source_file.readline(), target_file.readline()
        print('BLUE: {:.2f} in {} samples'.format(total_score / counter * 100, counter))
        f = open(FLAGS.data_dir + '/evaluate.json', 'w')
        f.write('BLUE: {:.2f} in {} samples'.format(total_score / counter * 100, counter) + '\n')
        for result in results:
            f.write(json.dumps(result) + '\n')
        f.close()
        f = open(FLAGS.data_dir + '/resultWithHighScore.json', 'w')
        for result_high_score in results_high_score:
            f.write(json.dumps(result_high_score) + '\n')
	f.close()
        f = open(FLAGS.data_dir + '/resultWithLowScore.json', 'w')
	for result_low_score in results_low_score:
	    f.write(json.dumps(result_low_score) + '\n')
	f.close()


def main(_):
    #data_utils.create_set(FLAGS.data_dir)
    if FLAGS.self_test:
        self_test()
    elif FLAGS.decode:
        decode()
    elif FLAGS.test:
        test()
    elif FLAGS.test_bleu:
        test_bleu()
    else:
        train()
   

if __name__ == "__main__":
    tf.app.run()
