from datasets.DAVIS import DAVIS, DAVISEval, DAVISInfer
from datasets.YoutubeVOS import YoutubeVOSDataset
from utils.Constants import DAVIS_ROOT, YOUTUBEVOS_ROOT


def get_dataset(args):
  if args.train_dataset == "davis":
    trainset = DAVIS(DAVIS_ROOT, imset='2017/train.txt', is_train=True,
                     random_instance=args.random_instance, crop_size=args.crop_size, resize_mode=args.resize_mode,
                     max_temporal_gap=12)
  elif args.train_dataset == "youtube_vos":
    trainset = YoutubeVOSDataset(YOUTUBEVOS_ROOT, imset='train', is_train=True,
                                 random_instance=args.random_instance, crop_size=args.crop_size,
                                 resize_mode=args.resize_mode)

  if 'infer' in args.task:
   testset = DAVISInfer(DAVIS_ROOT, random_instance=False, crop_size=args.crop_size_eval,
                        resize_mode=args.resize_mode_eval)
  else:
    testset = DAVISEval(DAVIS_ROOT, imset='2017/val.txt', random_instance=False, crop_size=args.crop_size_eval,
                        resize_mode=args.resize_mode_eval)

  return trainset, testset