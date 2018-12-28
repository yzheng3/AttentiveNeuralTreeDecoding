import unicodedata
import string
import re
import random as rd
import time
import math
import cPickle
import torch
import torch.nn as nn
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F
from nltk.tokenize import word_tokenize
from attention_model import *
import numpy as np
import tree_of_mesh as tom 
import os.path
import nltk
import operator
import read_embeddings as re
from collections import Counter

np.random.seed(9001)
rd.seed(9001)

USE_CUDA = True

teacher_forcing_ratio = 1.0
clip = 5.0
num_top_words = 50000

abstract_train = None
abstract_test = None
abstract_val = None
def traverse_tree(node, decoder_input, decoder_context, decoder_hidden, encoder_outputs, teacher_forcing, depth):

	if len(decoder_input) == 0 or len(node.children.keys()) == 0:
		return 0
	
	loss = 0
	target = [-1]*output_size
	for i in node.children.keys():
		target[i] = 1
	target_orig =  np.array(target)

	if teacher_forcing:
		decoder_context_orig = decoder_context.clone()
		decoder_hidden_orig = decoder_hidden.clone()
		decoder_output, decoder_context, decoder_hidden, decoder_attention = decoder(decoder_input, decoder_context, decoder_hidden, encoder_outputs)
		decoder_output_new = decoder_output.data.cpu().numpy()
		target = [target[i] if i in node_to_children[node.name] else decoder_output_new[0,i] for i in xrange(len(target))]		
		target = [[float(t) for t in target]]
		target = Variable(torch.FloatTensor(target)).cuda()
		loss = criterion(decoder_output, target)
		loss.backward(retain_graph=True)
		torch.nn.utils.clip_grad_norm(encoder.parameters(), clip)
		torch.nn.utils.clip_grad_norm(decoder.parameters(), clip)
		encoder_optimizer.step()
		decoder_optimizer.step()
		decoder_output, decoder_context, decoder_hidden, decoder_attention = decoder(decoder_input, decoder_context_orig, decoder_hidden_orig, encoder_outputs)
		decoder_output_fin = decoder_output.data.cpu().numpy()
		# for i in node_to_children[node.name]:
		# 	print decoder_output_new[0,i], decoder_output_fin[0,i], target_orig[i]
		
	else:
		# print "length decoder input: ", len(decoder_input)
		for one_input in decoder_input:
			target_new = []
			one_input = Variable(torch.LongTensor([[one_input]])).cuda()
			decoder_output, decoder_context, decoder_hidden, decoder_attention = decoder(one_input, decoder_context, decoder_hidden, encoder_outputs)
			decoder_output_new = decoder_output.data.cpu().numpy()
			target_new = [target[i] if i in node_to_children[node.name] else decoder_output_new[0,i] for i in xrange(len(target))]		
			target_new = [[float(t) for t in target_new]]
			target_new = Variable(torch.FloatTensor(target_new)).cuda()
			loss += criterion(decoder_output, target_new)

	# print index_2_code[decoder_input.data[0].cpu().numpy()[0]], index_2_code[node.name], index_2_code[node.children.keys()[0]], len(node.children.keys())

	for child in node.children:
		if teacher_forcing:
			decoder_input = Variable(torch.LongTensor([[node.children[child].name]])).cuda()
		else:
			# print decoder_output
			# print "max decoder output: ", np.max(decoder_output.data[0].cpu().numpy())
			decoder_input = (decoder_output > 0.5).data[0].cpu().numpy()
			decoder_input = [i for i in node_to_children[node.name] if decoder_input[i]]
		loss += traverse_tree(node.children[child], decoder_input, decoder_context, decoder_hidden, encoder_outputs, teacher_forcing, depth+1)

	return loss
	
def train(input_variables, target_variables, encoder, decoder, encoder_optimizer, decoder_optimizer, criterion):
	
	# Zero gradients of both optimizers
	encoder_optimizer.zero_grad()
	decoder_optimizer.zero_grad()
	loss = 0 # Added onto for each word
	teacher_forcing = rd.random() < teacher_forcing_ratio

	for it in xrange(len(input_variables)):

		input_variable = input_variables[it]
		target_variable = target_variables[it]

		# Convert it to LongTensor
		input_variable = Variable(torch.LongTensor(input_variable).view(-1, 1)).cuda()
		input_length = input_variable.size()[0]
		target_length = float(sum([len(a) for a in target_variable]))

		# Run words through encoder
		encoder_hidden = encoder.init_hidden()
		encoder_outputs, encoder_hidden = encoder(input_variable, encoder_hidden)
		
		# Prepare input and output variables
		if teacher_forcing:			
			decoder_input = Variable(torch.LongTensor([[code_2_index['SSOS']]]))
		else:
			decoder_input = [code_2_index['SSOS']]
		
		decoder_context = Variable(torch.zeros(1, decoder.hidden_size))
		decoder_hidden = encoder_hidden # Use last hidden state from encoder to start decoder
		
		if USE_CUDA:
			if teacher_forcing:
				decoder_input = decoder_input.cuda()
			decoder_context = decoder_context.cuda()

		root = tom.generate_tree(target_variable)
		# cPickle.dump((root, target_variable), open('root.pkl', 'w'))
		# return
		loss += traverse_tree(root, decoder_input, decoder_context, decoder_hidden, encoder_outputs, teacher_forcing, 0)

	# Backpropagation
	# loss.backward()
	# torch.nn.utils.clip_grad_norm(encoder.parameters(), clip)
	# torch.nn.utils.clip_grad_norm(decoder.parameters(), clip)
	# encoder_optimizer.step()
	# decoder_optimizer.step()

	if type(loss) != int:
		return loss.data[0]
	else:
		return 0

def traverse_prediction(decoder_input, decoder_context, decoder_hidden, encoder_outputs, depth):
	sequences = []
	if decoder_input.data[0].cpu().numpy()[0] == code_2_index['EOS'] or depth == 20:
		return [[]]

	decoder_output, decoder_context, decoder_hidden, decoder_attention = decoder(decoder_input, decoder_context, decoder_hidden, encoder_outputs)
	predictions = (decoder_output > 0).data[0].cpu().numpy()
	predictions = [i for i in node_to_children[int(decoder_input.data[0].cpu().numpy()[0])] if predictions[i] == True]
	# predictions = np.where(predictions)[0]
	# predictions = [p for p in predictions if p in node_to_children[int(decoder_input.data[0].cpu().numpy()[0])]]

	if len(predictions) == 0:
		return [[]]

	for pred in predictions:
		decoder_input = Variable(torch.LongTensor([[pred]])).cuda()
		lists_returned = traverse_prediction(decoder_input, decoder_context, decoder_hidden, encoder_outputs, depth+1)
		sequences += [[pred] + sublist for sublist in lists_returned]
	return sequences


def predict(input_variable, encoder, decoder):
	# Run words through encoder
	encoder_hidden = encoder.init_hidden()
	encoder_outputs, encoder_hidden = encoder(input_variable, encoder_hidden)

	# Prepare input and output variables
	decoder_input = Variable(torch.LongTensor([[code_2_index['SSOS']]]))
	decoder_context = Variable(torch.zeros(1, decoder.hidden_size))
	decoder_hidden = encoder_hidden # Use last hidden state from encoder to start decoder
	
	decoder_input = decoder_input.cuda()
	decoder_context = decoder_context.cuda()

	predictions = traverse_prediction(decoder_input, decoder_context, decoder_hidden, encoder_outputs, 0)
	predictions = [[code_2_index["SSOS"]]+sublist for sublist in predictions]

	return predictions 

def as_minutes(s):
	m = math.floor(s / 60)
	s -= m * 60
	return '%dm %ds' % (m, s)

def time_since(since, percent):
	now = time.time()
	s = now - since
	es = s / (percent)
	rs = es - s
	return '%s (- %s)' % (as_minutes(s), as_minutes(rs))

def convert_data_format(list_items):
	global max_seq_len
	abstracts = []
	targets = []
	for item in list_items:
		target_for_one_abstract = []
		abstracts.append(item['abstract'])
		for seq in item['sequence']:
			seq_mesh = seq[1].split(".")
			seq_mesh = [ ":".join(seq_mesh[:i+1]) for i in xrange(len(seq_mesh))]
			seq_mesh.append('EOS')
			seq_mesh.insert(0,'SOS')
			target_for_one_abstract.append(seq_mesh)
		targets.append(target_for_one_abstract)
	return abstracts, targets

def fit_tokenizer(X):
	all_words = []
	for abstract in X:
		all_words += word_tokenize(abstract.decode('utf8', 'ignore'))

	all_words_new = []
	for word in all_words:
		word = word.split("/")
		all_words_new += word

	all_words = all_words_new
	all_words = [word.lower() for word in all_words]

	wordcount = Counter(all_words)
	sorted_wc = sorted(wordcount.items(), key=operator.itemgetter(0), reverse=True)
	top_words = [l[0] for l in sorted_wc[0:num_top_words]]
	top_words = top_words + ["UNK"]

	word2index = {}
	for i in xrange(len(top_words)):
		word2index[top_words[i]] = i

	return word2index, len(top_words)

def build_sequences(X, word2index):
	X_new = []
	for abstract in X:
		seq = word_tokenize(abstract.decode('utf8', 'ignore'))
		seq_new = []
		for s in seq :
			seq_new += s.split("/")
		seq = seq_new
		seq = [s.lower() for s in seq]
		# X_new.append([word2index[k] if k in word2index else word2index["UNK"]  for k in seq])
		X_new.append([word2index[k] for k in seq if k in word2index])
	print "build_sequences ", len(X_new) 
	return X_new

def flatten_list(Y):
	Y = [code for listoflist in Y for sublist in listoflist for code in sublist]
	return Y 

def get_y_index_sequences(Y):
	Y_new = []
	for sequences in Y:
		sequences_for_one_abstarct = []
		for sequence in sequences:
			sequence = ["SSOS"] + sequence
			sequences_for_one_abstarct.append([code_2_index[s] for s in sequence[0:4]])
		Y_new.append(sequences_for_one_abstarct)
	return Y_new

def split_data(data, split_ratio):
	ind1 = rd.sample(range(len(data)), int(split_ratio*len(data)))
	ind2 = [i for i in xrange(len(ind1)) if i not in ind1 ]

	data1 = [data[i] for i in ind1]
	data2 = [data[i] for i in ind2]

	return data1, data2

def get_data():
	global abstract_train, abstract_test, abstract_val
	list_items = cPickle.load(open('pubmed.pkl','r'))
	list_items = [l for l in list_items[0:1000]]
	
	list_items_train, list_items_test = split_data(list_items, 0.6)
	list_items_train, list_items_val = split_data(list_items_train, 0.8)

	abstract_train, Y_train = convert_data_format(list_items_train)
	abstract_test, Y_test = convert_data_format(list_items_test)
	abstract_val, Y_val = convert_data_format(list_items_val)

	word2index, num_english_words = fit_tokenizer(abstract_train+abstract_test+abstract_val)

	X_train = build_sequences(abstract_train, word2index)
	X_test = build_sequences(abstract_test, word2index)
	X_val = build_sequences(abstract_val, word2index)

	return (X_train, Y_train, X_test, Y_test, X_val, Y_val, num_english_words, word2index)

def get_mesh_terms_from_sequences(true_sequences, predicted_sequences):
	true_mesh_terms = []
	pred_mesh_terms = []
	
	for sequence in true_sequences:
		sequence = [str(index_2_code[s]) for s in sequence[2:-1]]
		if ".".join(sequence) in seq2mesh:
			true_mesh_terms.append(seq2mesh[".".join(sequence)])

	for sequence in predicted_sequences:
		sequence = [str(index_2_code[s]) for s in sequence[2:-1]]
		if ".".join(sequence) in seq2mesh:
			pred_mesh_terms.append(seq2mesh[".".join(sequence)])

	return true_mesh_terms, pred_mesh_terms

def get_metrics(true_mesh_terms, pred_mesh_terms):
	true_mesh_terms = set(true_mesh_terms)
	pred_mesh_terms = set(pred_mesh_terms)
	
	if len(pred_mesh_terms) == 0:
		precision = 0.0
	else:
		precision = len(true_mesh_terms.intersection(pred_mesh_terms))/float(len(pred_mesh_terms))

	if len(true_mesh_terms) == 0:
		recall = 1.0
	else:
		recall = len(true_mesh_terms.intersection(pred_mesh_terms))/float(len(true_mesh_terms))
	
	if precision ==0 and recall == 0:
		f1_score = 0.0
	else:
		f1_score = 2*precision*recall/(precision+recall)

	return (precision, recall, f1_score)


def save_model_after_training(encoder, decoder):
	encoder = encoder.cpu()
	decoder = decoder.cpu()
	cPickle.dump((encoder, decoder), open('trained_model.pkl','w'))
	print "writing model"
	encoder = encoder.cuda()
	decoder = decoder.cuda()


def load_model():
	encoder, decoder = cPickle.load(open('trained_model.pkl','r'))
	encoder = encoder.cuda()
	decoder = decoder.cuda()
	return encoder, decoder


def generate_predictions(X, Y, encoder=None, decoder=None, is_test=0, abstracts=None):	
	if encoder is None and decoder is None:
		encoder, decoder = load_model()
	
	mesh_terms_pred = []
	mesh_terms_test = []
	metrics = []
	for i in xrange(len(X)):	
		if len(X[i]) == 0:
			continue

		input_variable = Variable(torch.LongTensor(X[i]).view(-1, 1)).cuda()
		predicton = predict(input_variable, encoder, decoder)
		# if i < 4:
		# 	print predicton
		true_mesh_terms, pred_mesh_terms = get_mesh_terms_from_sequences(Y[i], predicton)
		# if abstracts is not None:
		# 	print abstracts[i]
		# print pred_mesh_terms, true_mesh_terms
		print "*********************************************"
		print [sublist[2:] for sublist in Y[i]]
		print "***********************"
		print [sublist[2:] for sublist in predicton]
		print "**********************************************"
		metrics += [get_metrics(true_mesh_terms, pred_mesh_terms)]
		mesh_terms_pred.append(predicton)
		mesh_terms_test.append(true_mesh_terms)

	if is_test == 1:
		cPickle.dump((mesh_terms_test, mesh_terms_pred), open('predictions_decoder.pkl','w'))
	return metrics

X_train, Y_train, X_test, Y_test, X_val, Y_val, num_english_words, word2index = get_data()

word_embeddings = re.read_word_embeddings(word2index)
node_embeddings = re.read_node_embeddings()

code_2_index = cPickle.load(open('code_2_index.pkl', 'r'))
index_2_code = cPickle.load(open('index_2_code.pkl', 'r'))
seq2mesh = cPickle.load(open('seq_to_mesh.pkl','r'))

Y_train = get_y_index_sequences(Y_train)
Y_test = get_y_index_sequences(Y_test)
Y_val = get_y_index_sequences(Y_val)

X_train = [X_train[i] for i in xrange(256)]
Y_train = [Y_train[i] for i in xrange(256)]


# Y_train = [Y_train[i][:3] for i in xrange(len(Y_train))]

X_val = X_train
Y_val = Y_train

X_test = X_train
Y_test = Y_train
#reduce size of validation set
# X_val = [X_val[i] for i in xrange(4000)]
# Y_val = [Y_val[i] for i in xrange(4000)]

output_size = len(code_2_index.keys())
sequences = Y_train+Y_test+Y_val
sequences = [s for sublist in sequences for s in sublist]
node_to_children = tom.get_node_children(sequences)

# Running Training
attn_model = 'general'
hidden_size = 500
n_layers = 2
dropout_p = 0.0

# Initialize models
encoder = EncoderRNN(num_english_words, hidden_size, n_layers, embeddings=word_embeddings)
decoder = AttnDecoderRNN(attn_model, hidden_size, output_size, n_layers, dropout_p=dropout_p, embeddings=node_embeddings)

# Move models to GPU
if USE_CUDA:
	encoder.cuda()
	decoder.cuda()

# Initialize optimizers and criterion
learning_rate = 0.0001
encoder_optimizer = optim.Adam(encoder.parameters(), lr=learning_rate)
decoder_optimizer = optim.Adam(decoder.parameters(), lr=learning_rate)
# criterion = nn.MultiLabelSoftMarginLoss()    
criterion = nn.MSELoss(size_average=False)
# criterion = nn.BCELoss()
# Configuring training
n_epochs = 	500
plot_every = 200
print_every = 1
batch_size = 64

# Keep track of time elapsed and running averages
start = time.time()
plot_losses = []
print_loss_total = 0 # Reset every print_every
plot_loss_total = 0 # Reset every plot_every
seq_len_to_train = 20

partial_training = False

ind_list = []
samples =  np.random.permutation(len(X_train))
ind_list = [samples[i*batch_size:(i+1)*batch_size].tolist() for i in xrange(len(X_train)/batch_size)]

if partial_training:
	encoder, decoder = cPickle.load(open('trained_model.pkl','r'))
	encoder = encoder.cuda()
	decoder = decoder.cuda()

load_trained_model = False

if load_trained_model:
	encoder, decoder = cPickle.load(open('trained_model.pkl','r'))
	encoder = encoder.cuda()
	decoder = decoder.cuda()

else:
	f1_avg = -20
	for epoch in range(0, n_epochs + 1):

		if epoch > 20:
			teacher_forcing_ratio = 1.0

		if epoch > int(n_epochs/3) and epoch < int((2/3.0)*n_epochs):
			seq_len_to_train = 20

		if epoch > int((2/3.0)*n_epochs):
			seq_len_to_train = 20

		# Get training data for this cycle
		ind = rd.sample(xrange(len(X_train)), batch_size)
		# 
		# ind = ind_list[epoch%len(ind_list)]
		ind = [i for i in ind if len(X_train[i])>0]
		print ind
		input_variables = [X_train[i] for i in ind]
		target_variables = [Y_train[i] for i in ind]
		print target_variables[0]		
		# Run the train function
		loss = train(input_variables, target_variables, encoder, decoder, encoder_optimizer, decoder_optimizer, criterion)

		# Keep track of loss
		print_loss_total += loss
		plot_loss_total += loss

		# if epoch == 0: continue
		if epoch % print_every == 0 and epoch != 0:
			metrics = generate_predictions(X_val, Y_val, encoder, decoder)
			f1_avg_cur = np.average([m[2] for m in metrics])
			print "f1 score: ", f1_avg_cur, " Samples:", epoch*batch_size
			if f1_avg_cur >= f1_avg:
				f1_avg = f1_avg_cur
				save_model_after_training(encoder, decoder)

			print_loss_avg = print_loss_total / float(epoch)
			print_loss_total = 0
			print_summary = '%s (%d %d%%) %.8f' % (time_since(start, epoch / float(n_epochs)), epoch, epoch / float(n_epochs) * 100, loss)
			print(print_summary)

print "finished training"
metrics = generate_predictions(X_test, Y_test, is_test=1, abstracts=abstract_test)
avg_precision = np.average([m[0] for m in metrics])
avg_recall = np.average([m[1] for m in metrics])
avg_f1 = np.average([m[2] for m in metrics])

print "Precision: ", avg_precision
print "Recall: ", avg_recall
print "F1 Micro: ", avg_f1
