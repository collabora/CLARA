import io
import json
import torch
import torchdata
from torch.utils.data import DataLoader
from torch.nn.utils.rnn import pad_sequence
from torchdata.dataloader2 import DataLoader2, DistributedReadingService, MultiProcessingReadingService, SequentialReadingService 

import pytorch_lightning as pl

import librosa
import soundfile
import numpy as np

from text.whisper.normalizers import EnglishTextNormalizer
from text.tokeniser import Tokeniser # from text.whisper.tokenizer import get_tokenizer

from typing import Optional



class MultilingualTorchDataDataModule(pl.LightningDataModule):
	def __init__(
			self, train_data_dir:str, 
			test_data_dir:str, 
			valid_data_dir:str,
			epochs:Optional[int]=1,
			batch_size:Optional[int]=None,
			num_workers:Optional[int]=0,
			persistent_workers:bool=True,
			shuffle:Optional[bool]=True,
        ):
		super().__init__()

		self.train_data_dir = train_data_dir
		self.test_data_dir = test_data_dir
		self.valid_data_dir = valid_data_dir

		self.epochs = epochs
		self.shuffle = shuffle
		self.batch_size = batch_size
		self.num_workers = num_workers
		self.persistent_workers = persistent_workers

		self.cleaner = EnglishTextNormalizer()
		self.tokenizer = Tokeniser() # self.tokenizer = get_tokenizer(True)

	def to_sampels(self, data):
		a, t = data
		return soundfile.read(io.BytesIO(a[1].read())), json.loads(t[1].read().decode('utf-8'))
	
	def _create_pipeline(self, data_dir):
		datapipe = torchdata.datapipes.iter.IterableWrapper(data_dir)\
			.shuffle()\
			.sharding_filter()\
			.open_files_by_fsspec(mode='rb')\
			.load_from_tar() \
			.batch(2) \
			.map(self.to_sampels) \
			.batch(self.batch_size) \
			.map(self.collate_fn)
		
		return datapipe

	def setup(self, stage:Optional[str] = None):
		if len(self.train_data_dir)>0:
			self.train = self._create_pipeline(self.train_data_dir)

		if len(self.test_data_dir)>0:
			self.test = self._create_pipeline(self.test_data_dir)

		if len(self.valid_data_dir)>0:
			self.valid = self._create_pipeline(self.valid_data_dir)

	def train_dataloader(self):
		service = [
			MultiProcessingReadingService(num_workers=self.num_workers),
			# DistributedReadingService(),
	     ]
		reading_service = SequentialReadingService(*service)
		self.train_dl = DataLoader2(self.train, reading_service=reading_service)
		return self.train_dl

	def val_dataloader(self):
		service = [
			MultiProcessingReadingService(num_workers=self.num_workers),
			DistributedReadingService()
	     ]
		reading_service = SequentialReadingService(*service)
		self.val_dl = DataLoader2(self.valid, reading_service=reading_service)
		return self.val_dl

	def test_dataloader(self):
		service = [
			MultiProcessingReadingService(num_workers=self.num_workers),
			DistributedReadingService()
	     ]
		reading_service = SequentialReadingService(*service)
		self.test_dl = DataLoader2(self.test, reading_service=reading_service)
		return self.test_dl

	def predict_dataloader(self):
		service = [
			MultiProcessingReadingService(num_workers=self.num_workers),
			DistributedReadingService()
	     ]
		reading_service = SequentialReadingService(*service)
		self.predict_dl = DataLoader2(self.valid, reading_service=reading_service)
		return self.predict_dl
	
	def tokeniser_encode(self, text:str, lanuage:str='en'):
		return self.tokenizer.encode(self.cleaner(text), language=lanuage)
	
	def tokeniser_decode(self, tensor:torch.Tensor):
		return self.tokenizer.decode(tensor)
	
	def collate_fn(self, data):
		mels = []
		texts = []

		for (a, t) in data:
			mel = librosa.feature.melspectrogram(y=a[0], sr=a[1], fmin=0, fmax=8000, n_mels=80, n_fft=1024, win_length=1024, hop_length=512)
			mel = librosa.power_to_db(mel, ref=np.max)
			mels.append(torch.tensor(mel, dtype=torch.float32).T)

			texts.append(
				torch.tensor(
					self.tokeniser_encode(
						', '.join(t['text']) if isinstance(t['text'], list) else t['text'], \
							'en' if 'original_data' not in t.keys() else t["original_data"]["language"] if "language" in t["original_data"].keys() else 'en'
					)
				) 
			)

		mel_lengths = [mel.shape[0] for mel in mels]
		mel_lengths = torch.tensor(mel_lengths)
		text_lengths = [text.shape[0] for text in texts]
		text_lengths = torch.tensor(text_lengths)


		texts = pad_sequence(texts).T.contiguous()
		mels = pad_sequence(mels).permute(1,2,0).contiguous()

		return texts, mels, text_lengths, mel_lengths

	# def teardown(self, stage: str) -> None:
	# 	super().teardown(stage)
	# 	if hasattr(self, 'train_dl'):
	# 		self.train_dl.shutdown() 
	# 	if hasattr(self, 'val_dl'):
	# 		self.val_dl.shutdown()
	# 	if hasattr(self, 'test_dl'):
	# 		self.test_dl.shutdown()
	# 	if hasattr(self, 'predict_dl'):
	# 		self.predict_dl.shutdown()


if __name__ == '__main__':
	import tqdm
	from utils import get_s3_paths, get_lists 

	dataset_names = get_lists('/fsx/knoriy/code/CLASP/config/test_list.txt')
	exclude = get_lists('/fsx/knoriy/code/CLASP/config/exclude_list.txt')

	urls = get_s3_paths(
		base_s3_path = 's-laion-audio/webdataset_tar/', 
		train_valid_test = ['train', 'test', 'valid'],
		dataset_names = dataset_names,
		exclude=exclude,
		)
	
	print(urls)

	dataset = MultilingualTorchDataDataModule(
			train_data_dir = urls['train'],
			test_data_dir =urls['test'],
			valid_data_dir = urls['valid'],
			batch_size = 64,
			num_workers=0,
		)

	dataset.setup()

	for epoch in tqdm.trange(2, desc="train"):
		for i in tqdm.tqdm(dataset.train_dataloader(), desc="train minibatch"):
			pass

	# for epoch in tqdm.trange(2, desc="valid"):
	# 	for i in tqdm.tqdm(dataset.val_dataloader(), desc="valid minibatch"):
	# 		pass

	# for epoch in tqdm.trange(2, desc="test"):
	# 	for i in tqdm.tqdm(dataset.test_dataloader(), desc="test minibatch"):
	# 		pass