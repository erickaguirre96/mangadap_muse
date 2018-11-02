
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import pytest
import os

import numpy
from scipy import interpolate
from astropy.io import fits
import astropy.constants

from mangadap.drpfits import DRPFits, DRPFitsBitMask

from mangadap.par.artifactdb import ArtifactDB
from mangadap.par.emissionlinedb import EmissionLineDB
from mangadap.par.absorptionindexdb import AbsorptionIndexDB
from mangadap.par.bandheadindexdb import BandheadIndexDB

from mangadap.util.pixelmask import SpectralPixelMask

from mangadap.proc.templatelibrary import TemplateLibrary
from mangadap.proc.ppxffit import PPXFFit
from mangadap.proc.stellarcontinuummodel import StellarContinuumModel, StellarContinuumModelBitMask

from mangadap.tests.util import data_file

from mangadap.par.emissionmomentsdb import EmissionMomentsDB
from mangadap.proc.emissionlinemoments import EmissionLineMoments
from mangadap.proc.sasuke import Sasuke
from mangadap.proc.emissionlinemodel import EmissionLineModelBitMask

from mangadap.proc.spectralindices import SpectralIndices, SpectralIndicesBitMask

import warnings
warnings.simplefilter("ignore", UserWarning)
warnings.simplefilter("ignore", RuntimeWarning)

def test_model_indices():

    # Setup
    bm = SpectralIndicesBitMask()
    absdb = AbsorptionIndexDB('EXTINDX')
    bhddb = BandheadIndexDB('BHBASIC')

    # Grab the model spectra
    tpl = TemplateLibrary('M11MILES', spectral_step=1e-4, log=True, hardcopy=False)
    flux = numpy.ma.MaskedArray(tpl['FLUX'].data, mask=tpl['MASK'].data > 0)

    # Try to measure only absorption-line indices
    indices = SpectralIndices.measure_indices(absdb, None, tpl['WAVE'].data, flux[:2,:],
                                              bitmask=bm)

    # Try to measure only bandhead indices
    indices = SpectralIndices.measure_indices(None, bhddb, tpl['WAVE'].data, flux[:2,:],
                                              bitmask=bm)

    # Measure both
    indices = SpectralIndices.measure_indices(absdb, bhddb, tpl['WAVE'].data, flux[:2,:],
                                              bitmask=bm)

    # Mask them
    indx = numpy.ma.MaskedArray(indices['INDX'], mask=bm.flagged(indices['MASK']))

    assert indx.shape == (2,46), 'Incorrect output shape'
    assert numpy.sum(indx.mask[0,:]) == 21, 'Incorrect number of masked indices'
    assert numpy.allclose(indx[1,:].compressed(),
                numpy.array([-0.02676533,  0.01940112,  0.29238722,  1.91095547,  1.86120777,
                              1.13905315,  2.45219675,  2.24147868,  3.55100001,  5.25574319,
                              0.02093633,  0.05472762,  0.23184498,  1.92040166,  1.95694412,
                              0.98104418,  2.46517217,  1.03945802,  2.27591354,  2.69111323,
                              6.778595  ,  1.31360657,  0.00910697,  1.17072128,  1.12525642]))
