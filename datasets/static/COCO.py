import os
import zipfile

import numpy as np
from PIL import Image

from imgaug import augmenters as iaa
from torch.utils.data import Dataset

# from datasets.Loader import register_dataset
from util import get_one_hot_vectors
from utils.Resize import ResizeMode, resize

COCO_DEFAULT_PATH = "/globalwork/mahadevan/mywork/data/coco/"
NAME = "COCO"
TRANSLATION = 0.1
SHEAR = 0.05
ROTATION = 15
FLIP = 40


# @register_dataset(NAME)
class COCODataset(Dataset):
  def __init__(self, root, is_train=False, crop_size=None,temporal_window=8, resize_mode=ResizeMode.FIXED_SIZE):
    self.crop_size = crop_size
    self.resize_mode = ResizeMode(resize_mode)
    self.data_dir = root
    self.is_archive = zipfile.is_zipfile(self.data_dir)
    self.temporal_window = temporal_window

    subset = "train" if is_train else "valid"
    if subset == "train":
      self.data_type = "train2014"
      self.filter_crowd_images = True
      self.min_box_size = 30
    else:
      self.data_type = "val2014"
      self.filter_crowd_images = False
      self.min_box_size = -1.0

    self.restricted_image_category_list = ['person','bicycle','car','motorcycle','airplane','bus','train','truck','boat','bird','cat','dog','horse','sheep','cow','elephant','bear','zebra','giraffe','frisbee','skis','snowboard','sports ball','kite','baseball bat','skateboard','surfboard','tennis racket']
    if len(self.restricted_image_category_list) == 0:
      self.restricted_image_category_list = None
    self.restricted_annotations_category_list = ['person','bicycle','car','motorcycle','airplane','bus','train','truck','boat','bird','cat','dog','horse','sheep','cow','elephant','bear','zebra','giraffe','frisbee','skis','snowboard','sports ball','kite','baseball bat','skateboard','surfboard','tennis racket']
    if len(self.restricted_annotations_category_list) == 0:
      self.restricted_annotations_category_list = None

    self.exclude_image_category_list = []
    if len(self.exclude_image_category_list) == 0:
      self.exclude_image_category_list = None
    self.exclude_annotations_category_list = []
    if len(self.exclude_annotations_category_list) == 0:
      self.exclude_annotations_category_list = None

    # Use the minival split as done in https://github.com/rbgirshick/py-faster-rcnn/blob/master/data/README.md
    self.annotation_file = '%s/annotations/instances_%s.json' % (self.data_dir, subset)
    self.init_coco()
    self.inputfile_lists = self.read_inputfile_lists()

  def init_coco(self):
    # only import this dependency on demand
    import pycocotools.coco as coco
    self.coco = coco.COCO(self.annotation_file)
    ann_ids = self.coco.getAnnIds([])
    self.anns = self.coco.loadAnns(ann_ids)
    self.label_map = {k - 1: v for k, v in self.coco.cats.items()}
    self.filename_to_anns = dict()
    self.build_filename_to_anns_dict()

  def build_filename_to_anns_dict(self):
    for ann in self.anns:
      img_id = ann['image_id']
      img = self.coco.loadImgs(img_id)
      file_name = img[0]['file_name']
      if file_name in self.filename_to_anns:
        self.filename_to_anns[file_name].append(ann)
      else:
        self.filename_to_anns[file_name] = [ann]
        # self.filename_to_anns[file_name] = ann
    self.filter_anns()

  def filter_anns(self):
    # exclude all images which contain a crowd
    if self.filter_crowd_images:
      self.filename_to_anns = {f: anns for f, anns in self.filename_to_anns.items()
                               if not any([an["iscrowd"] for an in anns])}
    # filter annotations with too small boxes
    if self.min_box_size != -1.0:
      self.filename_to_anns = {f: [ann for ann in anns if ann["bbox"][2] >= self.min_box_size and ann["bbox"][3]
                                   >= self.min_box_size] for f, anns in self.filename_to_anns.items()}

    # remove annotations with crowd regions
    self.filename_to_anns = {f: [ann for ann in anns if not ann["iscrowd"]]
                             for f, anns in self.filename_to_anns.items()}
    # restrict images to contain considered categories
    if self.restricted_image_category_list is not None:
      print("filtering images to contain categories", self.restricted_image_category_list)
      self.filename_to_anns = {f: anns for f, anns in self.filename_to_anns.items()
                               if any([self.label_map[ann["category_id"] - 1]["name"]
                                       in self.restricted_image_category_list for ann in anns])}
      for cat in self.restricted_image_category_list:
        n_imgs_for_cat = sum([1 for anns in self.filename_to_anns.values() if
                              any([self.label_map[ann["category_id"] - 1]["name"] == cat for ann in anns])])
        print("number of images containing", cat, ":", n_imgs_for_cat)
    # exclude images that only contain objects in the given list
    elif self.exclude_image_category_list is not None:
      print("Excluding images categories", self.exclude_image_category_list)
      self.filename_to_anns = {f: anns for f, anns in self.filename_to_anns.items()
                               if any([self.label_map[ann["category_id"] - 1]["name"]
                                       not in self.exclude_image_category_list for ann in anns])}

    # restrict annotations to considered categories
    if self.restricted_annotations_category_list is not None:
      print("filtering annotations to categories", self.restricted_annotations_category_list)
      self.filename_to_anns = {f: [ann for ann in anns if self.label_map[ann["category_id"] - 1]["name"]
                                   in self.restricted_annotations_category_list]
                               for f, anns in self.filename_to_anns.items()}
    elif self.exclude_annotations_category_list is not None:
      print("Excluding annotations for object categories", self.exclude_annotations_category_list)
      self.filename_to_anns = {f: [ann for ann in anns if self.label_map[ann["category_id"] - 1]["name"]
                                   not in self.exclude_annotations_category_list]
                               for f, anns in self.filename_to_anns.items()}

    # filter out images without annotations
    self.filename_to_anns = {f: anns for f, anns in self.filename_to_anns.items() if len(anns) > 0}
    n_before = len(self.anns)
    self.anns = []
    for anns in self.filename_to_anns.values():
      self.anns += anns
    n_after = len(self.anns)
    print("filtered annotations:", n_before, "->", n_after)

  def load_image(self, img_filename):
    path = img_filename.split('/')[-1]
    img_dir = os.path.join(self.data_dir, "train2014") if path.split('_')[1] == "train2014" else \
      os.path.join(self.data_dir, "val2014")
    path = os.path.join(img_dir, path)
    img = np.array(Image.open(path).convert('RGB'))
    return img

  def load_annotation(self, img_filename):
    anns = self.filename_to_anns[img_filename.split("/")[-1]]
    img = self.coco.loadImgs(anns[0]['image_id'])[0]

    height = img['height']
    width = img['width']

    label = np.zeros((height, width, 1))
    for ann in anns:
      label[:, :, 0] += self.coco.annToMask(ann)[:, :]
    if len(np.unique(label)) == 1:
      print("GT contains only background.")

    return (label != 0).astype(np.uint8)

  def read_frame(self, index, instance_id=None):
    # use a blend of both full random instance as well as the full object
    
    raw_frames = self.load_image(self.inputfile_lists[index])
    raw_masks = self.load_annotation(self.inputfile_lists[index])
    # raw_masks = (raw_masks == instance_id).astype(np.uint8) if instance_id is not None else raw_masks
    #num_proposals, raw_proposals, proposal_mask = self.read_proposals(video, f, raw_masks)
    # if self.is_train:
    #   [raw_frames, raw_masks] = do_occ_aug(self.occluders, [raw_frames, raw_masks])
    tensors_resized = resize({"image":raw_frames, "mask":raw_masks[:, :, 0]}, self.resize_mode, self.crop_size)
    return tensors_resized["image"], tensors_resized["mask"]

  def read_inputfile_lists(self):
    img_dir = '%s/%s/' % (self.data_dir, self.data_type)
    # Filtering the image file names since some of them do not have annotations.
    imgs = [os.path.join(img_dir,fn) for fn in self.filename_to_anns.keys()]
    return imgs

  def generate_clip(self, raw_frame, raw_mask):
    clip_frames = np.repeat(raw_frame[np.newaxis], self.temporal_window, axis=0)
    clip_masks = np.repeat(raw_mask[np.newaxis], self.temporal_window, axis=0)
    # Sometimes(0.5, ...) applies the given augmenter in 50% of all cases,
    # e.g. Sometimes(0.5, GaussianBlur(0.3)) would blur roughly every second
    # image.
    sometimes = lambda aug: iaa.Sometimes(0.2, aug)
    blur = sometimes(iaa.OneOf([
                  iaa.GaussianBlur((0.0, 0.5)),
                  # iaa.AverageBlur(k=(2, 7)),
                  # iaa.MedianBlur(k=(3, 11)),
                ]))
    seq = iaa.Sequential([
      #iaa.Fliplr(FLIP / 100.),  # horizontal flips
      sometimes(iaa.ElasticTransformation(alpha=(200, 220), sigma=(17.0, 19.0))),
      iaa.Affine(
        scale={"x": (0.8, 1.2), "y": (0.8, 1.2)},
        translate_percent={"x": (-TRANSLATION, TRANSLATION), "y": (-TRANSLATION, TRANSLATION)},
        rotate=(-ROTATION, ROTATION),
        shear=(-SHEAR, SHEAR),
        mode='reflect'
      )
    ], random_order=True)

    frame_aug = raw_frame[np.newaxis]
    mask_aug = raw_mask[np.newaxis]
    # create sequence of transformations of the current image
    for t in range(self.temporal_window-1):
      frame_aug, mask_aug = seq(images=frame_aug.astype(np.uint8), segmentation_maps=mask_aug)
      frame_aug = blur(images=frame_aug)
      clip_frames[t + 1] = frame_aug[0]
      clip_masks[t + 1] = mask_aug[0]
    # clip_frames, clip_masks = seq(images=clip_frames.astype(np.uint8), segmentation_maps=clip_masks)

    return clip_frames / 255.0, clip_masks

  def set_video_id(self, video):
    pass

  def get_video_ids(self):
    return [0]

  def __len__(self):
    return len(self.inputfile_lists)

  def __getitem__(self, index):
    info = {}
    info['name'] = "coco"
    info['num_frames'] = len(self.inputfile_lists)
    #info['shape'] = self.shape[sequence]

    raw_frames, raw_masks = self.read_frame(index)
    raw_frames, raw_masks = self.generate_clip(raw_frames, raw_masks)
    raw_frames = np.transpose(raw_frames, (3, 0, 1, 2))
    raw_masks = raw_masks[np.newaxis]

    info['num_objects'] = len(np.unique(raw_masks))

    # padding size to be divide by 32
    _,_, h, w = raw_masks.shape
    new_h = h + 32 - h % 32
    new_w = w + 32 - w % 32
    # print(new_h, new_w)
    lh, uh = (new_h - h) / 2, (new_h - h) / 2 + (new_h - h) % 2
    lw, uw = (new_w - w) / 2, (new_w - w) / 2 + (new_w - w) % 2
    lh, uh, lw, uw = int(lh), int(uh), int(lw), int(uw)
    pad_masks = np.pad(raw_masks, ((0,0),(0,0),(lh, uh), (lw, uw)), mode='constant')
    pad_frames = np.pad(raw_frames, ((0, 0),(0, 0),(lh, uh), (lw, uw)), mode='constant')
    info['pad'] = ((lh, uh), (lw, uw))

    return {'images': pad_frames, 'info': info,
            'target': pad_masks, "proposals": pad_masks, "raw_proposals": pad_masks,
            'raw_masks': pad_masks}


class COCOInstanceDataset(COCODataset):
  def __init__(self, root, is_train=False, crop_size=None,temporal_window=8, resize_mode=ResizeMode.FIXED_SIZE):
    super(COCOInstanceDataset, self).__init__(root, is_train=is_train, crop_size=crop_size,
                                              temporal_window=temporal_window, resize_mode=resize_mode)

  def build_filename_to_anns_dict(self):
    for ann in self.anns:
      ann_id = ann['id']
      img_id = ann['image_id']
      img = self.coco.loadImgs(img_id)
      file_name = img[0]['file_name']

      file_name = file_name + ":" + repr(img_id) + ":" + repr(ann_id)
      if file_name in self.filename_to_anns:
        print("Ignoring instance as an instance with the same id exists in filename_to_anns.")
      else:
        self.filename_to_anns[file_name] = [ann]

    self.filter_anns()

  def load_image(self, img_filename):
    path = img_filename.split(':')[0]
    path = path.split('/')[-1]
    img_dir = os.path.join(self.data_dir, "train2014") if path.split('_')[1] == "train2014" else \
      os.path.join(self.data_dir, "val2014")
    path = os.path.join(img_dir, path)
    img = np.array(Image.open(path).convert('RGB'))
    return img

  def load_annotation(self, img_filename):
    ann = self.filename_to_anns[img_filename.split("/")[-1]]
    img = self.coco.loadImgs(ann[0]['image_id'])[0]

    height = img['height']
    width = img['width']

    label = np.zeros((height, width, 1))
    label[:, :, 0] = self.coco.annToMask(ann[0])[:, :]
    if len(np.unique(label)) == 1:
      print("GT contains only background.")

    return label.astype(np.uint8)

  def __getitem__(self, item):
    input_dict = super(COCOInstanceDataset, self).__getitem__(item)
    input_dict['masks_guidance'] = input_dict['raw_masks'][:, 0]
    return input_dict


class COCOEmbeddingDataset(COCODataset):
  def __init__(self, root, is_train=False, crop_size=None,temporal_window=8, resize_mode=ResizeMode.FIXED_SIZE,
               num_classes=2):
    super(COCOEmbeddingDataset, self).__init__(root=root, is_train=is_train, crop_size=crop_size,
                                         temporal_window=temporal_window, resize_mode=resize_mode)
    self.num_classes = num_classes

  def load_annotation(self, img_filename):
    anns = self.filename_to_anns[img_filename.split("/")[-1]]
    img = self.coco.loadImgs(anns[0]['image_id'])[0]

    height = img['height']
    width = img['width']

    label = np.zeros((height, width, 1))
    for ann in anns:
      label[:, :, 0][self.coco.annToMask(ann) == 1] = (anns.index(ann) + 1)
    if len(np.unique(label)) == 1:
      print("GT contains only background.")

    return label.astype(np.uint8)

  def __getitem__(self, item):
    input_dict = super(COCOEmbeddingDataset, self).__getitem__(item)
    one_hot_masks = [get_one_hot_vectors(input_dict['target'][0, i], self.num_classes)[:, np.newaxis, :, :]
                     for i in range(len(input_dict['target'][0]))]
    # assert (len(np.unique(input_dict['target'][:, 1:])) - input_dict['info']['num_objects']) < 2
    input_dict['target_extra'] = {#'similarity_ref': np.concatenate(one_hot_masks, axis=1).astype(np.uint8),
                                  'similarity_raw_mask': input_dict['target']}
    input_dict['target'] = (input_dict['target'] != 0).astype(np.uint8)

    return input_dict
