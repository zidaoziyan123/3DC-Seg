import glob
import os
import random
import re

import numpy as np
from PIL import Image

from datasets.DAVIS import DAVIS
from utils.Constants import VISAL_ROOT
from utils.Resize import ResizeMode, resize


class SegTrackV2Dataset(DAVIS):
    def __init__(self, root, is_train=False, crop_size=None, temporal_window=8, min_temporal_gap=2,
                 max_temporal_gap=8, resize_mode=ResizeMode.FIXED_SIZE):
        # maintain a dict to store the index length for videos. They are different for fbms
        self.index_length = {}
        self.gt_frames = {}
        self.video_frames = {}
        self.video_masks = {}
        self.mask_list = []
        super(SegTrackV2Dataset, self).__init__(root=root, is_train=is_train, crop_size=crop_size,
                                          temporal_window=temporal_window,
                                          min_temporal_gap=min_temporal_gap, max_temporal_gap=max_temporal_gap,
                                          resize_mode=resize_mode)

    def set_paths(self, imset, resolution, root):
        self.image_dir = os.path.join(root, "JPEGImages")
        self.mask_dir = os.path.join(root, "GroundTruth")
        return self.image_dir

    def set_video_id(self, video):
        self.current_video = video
        self.start_index = self.get_start_index(video)
        self.img_list = self.video_frames[video]
        self.img_list.sort()
        # instance_ids = list(range(self.num_objects[video] + 1))
        # instance_ids.remove(0)
        # self.random_instance_ids[video] = random.choice(instance_ids)

    def create_img_list(self, _imset_f):
        assert os.path.exists(self.image_dir), "Images directory not found at expected path: {}".format(
            self.image_dir)
        assert os.path.exists(self.mask_dir), "Ground truth directory not found at expected path: {}".format(
            self.mask_dir)

        def get_image_files(dirpath):
            return [f for f in sorted(glob.glob(os.path.join(dirpath, "*"))) if re.search(r"(\.png|\.bmp)$", f)]

        seq_names = [d for d in sorted(os.listdir(self.image_dir))]
        seq_names = [d for d in seq_names if os.path.isdir(os.path.join(self.image_dir,d))]

        for _video in seq_names:
            self.videos.append(_video)
            seq_images_dir = os.path.join(self.image_dir, _video)
            assert os.path.exists(seq_images_dir), "Images directory not found at expected path: {}".format(
                seq_images_dir)
            print("Reading Sequence {}".format(_video))
            gt_dir_content = sorted(glob.glob(os.path.join(self.mask_dir, _video, "*")))
            vid_files = get_image_files(seq_images_dir)

            mask_paths = []
            if all([os.path.isdir(x) for x in gt_dir_content]):  # format (1): directories for instances
                for instance_dir in gt_dir_content:
                    mask_paths.append(get_image_files(instance_dir))
                mask_paths = list(zip(*mask_paths))
                assert len(mask_paths) == len(vid_files), "Size mismatch: {}".format(len(vid_files),
                                                                                               len(mask_paths))
                # print(seq.mask_paths)
                # exit(0)
            elif all([os.path.isfile(x) for x in gt_dir_content]):
                mask_paths = [f for f in gt_dir_content if re.search(r"(\.png|\.bmp)$", f)]
            else:
                raise ValueError("What bulls*it is this?!")

            self.gt_frames[_video] = [int(i) for i, f in enumerate(vid_files)]
            assert len(self.gt_frames[_video]) == len(mask_paths)
            self.num_frames[_video] = len(vid_files)
            self.video_frames[_video] = vid_files
            self.video_masks[_video] =  mask_paths
            self.num_objects[_video] = 1
            self.shape[_video] = np.shape(np.array(Image.open(vid_files[0]).convert('RGB')))[:2]
            self.img_list += vid_files
            self.mask_list += mask_paths
            # self.gt_frames[sequence] = [int(f.split("/")[-1].split("_")[-1].split(".")[0])
            #                          for f in glob.glob(os.path.join(self.mask_dir, sequence, '*.png'))]
        self.reference_list = self.img_list

    def read_frame(self, shape, video, f, instance_id=None, support_indices=None):
        # use a blend of both full random instance as well as the full object
        img_file = self.video_frames[video][f]
        fname = img_file.split("/")[-1]
        mask_file = self.video_masks[video][f]
        raw_frames = np.array(Image.open(img_file).convert('RGB')) / 255.
        if isinstance(mask_file, (list, tuple)):
            assert mask_file[0].split("/")[-1].split(".")[0] == img_file.split("/")[-1].split(".")[0]
            frame_masks = [np.array(Image.open(mask).convert('P')) > 128 for mask
                           in mask_file]
            raw_mask = np.any(np.stack(frame_masks, 0), 0).astype(np.uint8)
        else:
            assert mask_file.split("/")[-1].split(".")[0] == img_file.split("/")[-1].split(".")[0]
            raw_mask = np.array(Image.open(mask_file).convert('P'))
            raw_mask = (raw_mask > 0).astype(np.uint8)
        mask_void = (raw_mask == 255).astype(np.uint8)
        raw_mask[raw_mask == 255] = 0

        raw_masks = (raw_mask == instance_id).astype(np.uint8) if instance_id is not None else raw_mask
        tensors_resized = resize({"image": raw_frames, "mask": raw_masks},
                                 self.resize_mode, shape)
        return tensors_resized["image"] / 255.0, tensors_resized["mask"], mask_void

    def get_support_indices(self, index, sequence):
        # index should be start index of the clip
        if self.is_train:
            index_range = np.arange(index, min(self.num_frames[sequence],
                                               (index + max(self.max_temporal_gap, self.temporal_window))))
        else:
            index_range = np.arange(index,
                                    min(self.num_frames[sequence], (index + self.temporal_window)))

        support_indices = np.random.choice(index_range, min(self.temporal_window, len(index_range)), replace=False)
        support_indices = np.sort(np.append(support_indices, np.repeat([index],
                                                                       self.temporal_window - len(support_indices))))

        # print(support_indices)
        return support_indices

    def __getitem__(self, item):
        img_file = self.img_list[item]
        sequence = self.get_current_sequence(img_file)
        info = {}
        info['name'] = sequence
        info['num_frames'] = self.num_frames[sequence]
        num_objects = self.num_objects[sequence]
        info['num_objects'] = num_objects
        info['shape'] = self.shape[sequence]
        info['gt_frames'] = self.gt_frames[sequence]
        index = self.video_frames[sequence].index(img_file)

        # retain original shape
        # shape = self.shape[self.current_video] if not (self.is_train and self.MO) else self.crop_size
        shape = self.shape[sequence] if self.crop_size is None else self.crop_size
        support_indices = self.get_support_indices(index, sequence)
        info['support_indices'] = support_indices
        th_frames = []
        th_masks = []
        th_mask_void = []
        instance_id = np.random.choice(np.array(range(1, num_objects + 1))) if self.random_instance else None
        # add the current index and the previous frame with respect to the max index in supporting frame
        for i in np.sort(support_indices):
            raw_frame, raw_mask, mask_void = \
                self.read_frame(shape, sequence, i, instance_id, support_indices)

            # padding size to be divide by 32
            h, w = raw_mask.shape
            new_h = h + 32 - h % 32 if h % 32 > 0 else h
            new_w = w + 32 - w % 32 if w % 32 > 0 else w
            # print(new_h, new_w)
            lh, uh = (new_h - h) / 2, (new_h - h) / 2 + (new_h - h) % 2
            lw, uw = (new_w - w) / 2, (new_w - w) / 2 + (new_w - w) % 2
            lh, uh, lw, uw = int(lh), int(uh), int(lw), int(uw)
            pad_masks = np.pad(raw_mask, ((lh, uh), (lw, uw)), mode='constant')
            pad_mask_void = np.pad(mask_void, ((lh, uh), (lw, uw)), mode='constant')
            pad_frames = np.pad(raw_frame, ((lh, uh), (lw, uw), (0, 0)), mode='constant')
            info['pad'] = ((lh, uh), (lw, uw))

            th_frames.append(np.transpose(pad_frames, (2, 0, 1))[:, np.newaxis])
            th_masks.append(pad_masks[np.newaxis, np.newaxis])
            th_mask_void.append(pad_mask_void[np.newaxis, np.newaxis])
            # th_pc.append(proposal_categories[np.newaxis])
            # th_ps.append(proposal_scores[np.newaxis])

        th_masks_raw = th_masks.copy()
        th_masks[-1] = np.zeros_like(th_masks[-1])
        masks_guidance = np.concatenate(th_masks, axis=1)
        masks_void = np.concatenate(th_mask_void, axis=1)
        # remove masks with some probability to make sure that the network can focus on intermediate frames
        # if self.is_train and np.random.choice([True, False], 1, p=[0.15,0.85]):
        #   masks_guidance[0, -2] = np.zeros(masks_guidance.shape[2:])

        # Can be used for setting proposals if desired, but for now it isn't neccessary and will be ignored
        return {'images': np.concatenate(th_frames, axis=1),'info': info,
                'target': masks_guidance, "proposals": masks_guidance,
                "raw_proposals": masks_guidance,
                'raw_masks': np.concatenate(th_masks_raw, axis=1),
                'masks_void': masks_void,
                'target_extra': {'similarity_ref': masks_guidance,
                                  'similarity_raw_mask': masks_guidance, 'sem_seg':masks_guidance}}


if __name__ == '__main__':
    dataset = SegTrackV2Dataset(root="/home/sabari/vision-data/", crop_size=480, resize_mode=ResizeMode.RESIZE_SHORT_EDGE)
    dataset.set_video_id(dataset.get_video_ids()[-1])
    result = dataset.__getitem__(20)
    print("image range: {}\nfgmask: {}\nsupport: {}".format(
        (result['images'].min(),
        result['images'].max()),
        (np.unique(result['target']), result['target'].shape),
        result['info']['support_indices']))