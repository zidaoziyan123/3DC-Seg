import torch
from torch import nn
import numpy as np
from network.Modules import Refine3dDG, Refine3dConvTranspose
from network.Resnet3d import resnet50, resnet50_no_ts
from network.Resnet3dAgg import Encoder3d, Decoder3d, Resnet3dSimilarity, Encoder3d_csn_ir, Resnet3d
from network.embedding_head import NonlocalOffsetEmbeddingHead
from network.models import BaseNetwork
from torch.nn import functional as F

from network.modules.multiscale import MultiscaleCombinedHeadLongTemporalWindow


class DecoderWithEmbedding(Decoder3d):
  def __init__(self, n_classes=2, e_dim = 64, add_spatial_coord=True):
    super(DecoderWithEmbedding, self).__init__(n_classes)
    self.embedding_head = NonlocalOffsetEmbeddingHead(256, 128, e_dim, downsampling_factor=2,
                                                      add_spatial_coord=add_spatial_coord)

  def forward(self, r5, r4, r3, r2, support):
    x = self.GC(r5)
    r = self.convG1(F.relu(x))
    r = self.convG2(F.relu(r))
    m5 = x + r  # out: 1/32, 64
    m4 = self.RF4(r4, m5)  # out: 1/16, 64
    m3 = self.RF3(r3, m4)  # out: 1/8, 64
    m2 = self.RF2(r2, m3)  # out: 1/4, 64
    e = self.embedding_head(F.interpolate(F.relu(m2), scale_factor=(1,0.5,0.5), mode='trilinear'))

    p2 = self.pred2(F.relu(m2))
    p = F.interpolate(p2, scale_factor=(1, 4, 4), mode='trilinear')

    return p, e, m2


class DecoderSegmentEmbedding(DecoderWithEmbedding):
  def __init__(self, n_classes=2, e_dim=64):
    super(DecoderSegmentEmbedding, self).__init__(n_classes=n_classes, e_dim=e_dim)
    # self.convG1 = nn.Conv3d(2048, 256, kernel_size=3, padding=1)
    self.con1x1 = nn.Conv3d(e_dim, 256, kernel_size=1, padding=1)

  def forward(self, r5, r4, r3, r2, support):
    x = self.GC(r5)
    r = self.convG1(F.relu(x))
    r = self.convG2(F.relu(r))
    m5 = x + r  # out: 1/32, 64
    m4 = self.RF4(r4, m5)  # out: 1/16, 64
    m3 = self.RF3(r3, m4)  # out: 1/8, 64
    m2 = self.RF2(r2, m3)  # out: 1/4, 64
    e = self.embedding_head(F.interpolate(m3, scale_factor=(2, 1, 1), mode='trilinear'))

    e_unrolled = self.con1x1(F.relu(e))
    p2 = self.pred2(F.relu(m2) + F.interpolate(e_unrolled, m2.shape[2:], mode='trilinear'))
    p = F.interpolate(p2, scale_factor=(1, 4, 4), mode='trilinear')

    return p, e, m2


class DecoderEmbedding(Decoder3d):
  def __init__(self,  n_classes=2, e_dim = 3, add_spatial_coord=True, scale=0.5):
    super(DecoderEmbedding, self).__init__( n_classes=n_classes)
    self.RF4 = Refine3dConvTranspose(1024, 256)
    self.RF3 = Refine3dConvTranspose(512, 256)
    self.RF2 = Refine3dConvTranspose(256, 256)


# Multi scale decoder
class MultiScaleDecoder(Decoder3d):
  def __init__(self, n_classes=2, add_spatial_coord = True):
    super(MultiScaleDecoder, self).__init__(n_classes)
    self.convG1 = nn.Conv3d(2048, 256, kernel_size=3, padding=1)
    self.embedding_head = MultiscaleCombinedHeadLongTemporalWindow(256, n_classes,True, True,seed_map=True,
                                                                   add_spatial_coord=add_spatial_coord)

  def forward(self, r5, r4, r3, r2, support):
    r = self.convG1(F.relu(r5))
    r = self.convG2(F.relu(r))
    m5 = r  # out: 1/32, 64
    m4 = self.RF4(r4, m5)  # out: 1/16, 64
    m3 = self.RF3(r3, m4)  # out: 1/8, 64
    m2 = self.RF2(r2, m3)  # out: 1/4, 64
    p, e = self.embedding_head.forward([m5, m4, m3, m2])

    return p, e


class Resnet3dEmbeddingNetwork(Resnet3dSimilarity):
  def __init__(self, tw=8, sample_size=112, e_dim=7, n_classes=2):
    super(Resnet3dEmbeddingNetwork, self).__init__()
    self.encoder = Encoder3d(tw, sample_size)
    self.decoder = DecoderWithEmbedding(n_classes=n_classes, e_dim=e_dim)


class Resnet3dSegmentEmbedding(Resnet3dSimilarity):
  def __init__(self, tw=8, sample_size=112,n_classes=2, e_dim=7):
    super(Resnet3dSegmentEmbedding, self).__init__(n_classes=n_classes)
    self.encoder = Encoder3d(tw, sample_size)
    self.decoder = DecoderSegmentEmbedding(n_classes=n_classes, e_dim=e_dim)


class Resnet3dSpatialEmbedding(Resnet3dSimilarity):
  def __init__(self, tw=8, sample_size=112,n_classes=2, e_dim=7):
    super(Resnet3dSpatialEmbedding, self).__init__(n_classes=n_classes)
    resnet = resnet50_no_ts(sample_size=sample_size, sample_duration=tw)
    self.encoder = Encoder3d(tw, sample_size, resnet=resnet)
    self.decoder = DecoderWithEmbedding(e_dim=e_dim, add_spatial_coord=False)


class Resnet3dEmbeddingMultiDecoder(Resnet3d):
  def __init__(self, tw=8, sample_size=112, e_dim=7, decoders=None):
    super(Resnet3dEmbeddingMultiDecoder, self).__init__(tw=tw, sample_size=sample_size)
    resnet = resnet50_no_ts(sample_size=sample_size, sample_duration=tw)
    self.encoder = Encoder3d(tw, sample_size, resnet=resnet)
    decoders = [Decoder3d(), DecoderEmbedding(n_classes=e_dim)] if decoders is None else decoders
    self.decoders = nn.ModuleList()
    for decoder in decoders:
      self.decoders.append(decoder)

  def forward(self, x, ref = None):
    r5, r4, r3, r2 = self.encoder.forward(x, ref)
    p = [decoder.forward(r5, r4, r3, r2, None) for decoder in self.decoders]
    # e = self.decoder_embedding.forward(r5, r4, r3, r2, None)
    return p


class Resnet3dChannelSeparated_ir(Resnet3dEmbeddingMultiDecoder):
  def __init__(self, tw=16, sample_size = 112, e_dim=7, n_classes=2):
    super(Resnet3dChannelSeparated_ir, self).__init__( decoders =
                                                       [Decoder3d(n_classes=n_classes),
                                                        DecoderEmbedding(n_classes=e_dim)
                                                        ])
    self.encoder = Encoder3d_csn_ir(tw, sample_size)


class Resnet3dCSNiRSameDecoders(Resnet3dEmbeddingMultiDecoder):
  def __init__(self, tw=16, sample_size = 112, e_dim=7):
    super(Resnet3dCSNiRSameDecoders, self).__init__(decoders=
                                                      [Decoder3d(),
                                                       Decoder3d(n_classes=e_dim)
                                                       ])
    self.encoder = Encoder3d_csn_ir(tw, sample_size)


class Resnet3dCSNiRMultiScale(Resnet3d):
  def __init__(self, tw=16, sample_size = 112, e_dim=7, add_spatial_coord=True):
    super(Resnet3dCSNiRMultiScale, self).__init__()
    self.encoder = Encoder3d_csn_ir(tw, sample_size)
    self.decoder = MultiScaleDecoder(add_spatial_coord=add_spatial_coord)

  def forward(self, x, ref):
    r5, r4, r3, r2 = self.encoder.forward(x, ref)
    p = self.decoder.forward(r5, r4, r3, r2, None)

    return p


class Resnet3dCSNiRMultiScaleNoCoord(Resnet3dCSNiRMultiScale):
  def __init__(self):
    super(Resnet3dCSNiRMultiScaleNoCoord, self).__init__(add_spatial_coord=False)